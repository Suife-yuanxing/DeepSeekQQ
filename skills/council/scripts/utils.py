"""Council Skill 通用工具函数。

提供日志、token 计数、文本截断、门控提取、进度追踪等跨模块共用功能。
"""

import re
import sys
import time
from typing import Optional

# ── tiktoken 精确计数（可选，未安装时回退到 char/2 估算）──
try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    HAS_TIKTOKEN = True
except (ImportError, Exception):
    _TIKTOKEN_ENC = None
    HAS_TIKTOKEN = False


# ═══════════════════════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════════════════════

def log_progress(message: str, level: str = "info"):
    """输出进度到 stderr，不污染 stdout 的 Markdown 报告。"""
    prefix = {"info": "⏳", "success": "✅", "error": "❌", "phase": "📍"}
    print(f"{prefix.get(level, '•')} {message}", file=sys.stderr, flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Token 计数与截断
# ═══════════════════════════════════════════════════════════════════════════════

def count_tokens(text: str) -> int:
    """精确 token 计数（tiktoken），不可用时回退到 char/2 估算。"""
    if HAS_TIKTOKEN and _TIKTOKEN_ENC:
        try:
            return len(_TIKTOKEN_ENC.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 2)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """精确按 token 数截断文本。"""
    if HAS_TIKTOKEN and _TIKTOKEN_ENC:
        try:
            tokens = _TIKTOKEN_ENC.encode(text)
            if len(tokens) <= max_tokens:
                return text
            return _TIKTOKEN_ENC.decode(tokens[:max_tokens]) + "\n\n[... truncated ...]"
        except Exception:
            pass
    # 回退：char/2 估算
    max_chars = max_tokens * 2
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


# ═══════════════════════════════════════════════════════════════════════════════
# 门控提取
# ═══════════════════════════════════════════════════════════════════════════════

def extract_gate_from_report(report_text: str) -> Optional[str]:
    """从 Chairman 报告中提取质量门控结果（以 AI 输出为准）。

    搜索策略（按优先级）：
    1. 精确匹配"质量门控：**REVISE**"等标准格式
    2. 匹配"门控结论：PASS/BLOCK/REVISE"
    3. 宽泛搜索：任何靠近"门控"的 PASS/BLOCK/REVISE
    4. 最后手段：搜索报告前 15 行（摘要区）中的门控
    """
    # 策略 1+2：标准格式
    patterns = [
        re.compile(r'质量门控[：:]\s*\*{0,2}(PASS|BLOCK|REVISE)\*{0,2}', re.IGNORECASE),
        re.compile(r'门控结论[：:]\s*\*{0,2}(PASS|BLOCK|REVISE)\*{0,2}', re.IGNORECASE),
        re.compile(r'质量\s*门控[：:]\s*\*{0,2}(PASS|BLOCK|REVISE)\*{0,2}', re.IGNORECASE),
    ]
    for pat in patterns:
        match = pat.search(report_text)
        if match:
            return match.group(1).upper()

    # 策略 3：宽泛搜索（门控出现在同一行或下一行的 PASS/BLOCK/REVISE）
    broad = re.search(
        r'(?:门控|gate)[：:\s]*.{0,30}?\b(PASS|BLOCK|REVISE)\b',
        report_text, re.IGNORECASE
    )
    if broad:
        return broad.group(1).upper()

    # 策略 4：搜索报告前 15 行（摘要区通常在顶部）
    head = "\n".join(report_text.split("\n")[:15])
    for pat in [
        re.compile(r'\b(PASS|BLOCK|REVISE)\b', re.IGNORECASE),
    ]:
        match = pat.search(head)
        if match:
            val = match.group(1).upper()
            # 确认上下文是门控相关（避免匹配到方案正文中的词）
            if re.search(r'(?:门控|gate|质量)', head[:match.start() + 50], re.IGNORECASE):
                return val

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 进度跟踪
# ═══════════════════════════════════════════════════════════════════════════════

class ProgressTracker:
    """简单进度条 + ETA 估算。

    跟踪任务完成情况，基于已完成任务的平均耗时预估剩余时间。
    """

    def __init__(self, total_tasks: int, label: str = ""):
        self.total = total_tasks
        self.label = label
        self.completed = 0
        self.times: list[float] = []
        self._start = time.time()

    def task_done(self, task_name: str = "", elapsed: float | None = None):
        """标记一个任务完成。"""
        self.completed += 1
        t = elapsed if elapsed is not None else time.time() - self._start
        self.times.append(t)

    def _eta(self) -> float:
        """预估剩余秒数。"""
        remaining = self.total - self.completed
        if remaining <= 0 or not self.times:
            return 0.0
        avg = sum(self.times) / len(self.times)
        return avg * remaining

    def render(self) -> str:
        """渲染进度条（单行文本）。"""
        if self.total <= 0:
            return ""
        pct = self.completed / self.total
        bar_width = 12
        filled = int(bar_width * pct)
        bar = "█" * filled + "░" * (bar_width - filled)
        elapsed = time.time() - self._start
        eta = self._eta()
        label_str = f"{self.label} " if self.label else ""
        return (
            f"\r  {label_str}[{bar}] {self.completed}/{self.total}"
            f" | {elapsed:.0f}s"
            + (f" | 剩余 ~{eta:.0f}s" if eta > 1 else "")
        )

    def log(self):
        """输出进度条到 stderr。"""
        print(self.render(), file=sys.stderr, end="", flush=True)

    def done(self):
        """标记全部完成，输出最终行。"""
        elapsed = time.time() - self._start
        print(f"\r  ✅ {self.label}完成: {self.completed}/{self.total} | {elapsed:.1f}s",
              file=sys.stderr, flush=True)
