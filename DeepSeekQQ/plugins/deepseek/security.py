"""输入安全模块 — 防注入、防滥用。

借鉴 ECC AgentShield 的规则引擎思想，在用户消息送入 LLM 前做前置扫描。

P0-4 分层防御策略（2026-06-13）：
- DeepSeek API 路径：依赖 messages 数组天然隔离，不做内容净化（过度净化会误伤正常对话）
- Ollama 本地路径：做 ChatML token 删除净化（本地模型安全边界弱）
"""
import re
import time
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger

# ============================================================
# Prompt Injection 检测规则
# ============================================================

_INJECTION_PATTERNS = [
    # 中文注入指令
    (r"忽略.{0,15}(之前|上面|以上|所有).{0,10}(指令|提示|规则|设定|系统)", "ignore_instructions"),
    (r"(你现在是|你扮演|假装你是|你不再).{0,20}(助手|AI|语言模型|GPT|Claude)", "role_hijack"),
    (r"(系统提示词|system\s*prompt|你的设定|你的指令)", "prompt_probe"),
    (r"(输出|显示|告诉我|打印).{0,10}(系统|设定|指令|规则|prompt)", "prompt_leak"),
    (r"(忘掉|删除|覆盖|重置).{0,10}(之前|以上|所有).{0,10}(规则|设定|指令)", "override_rules"),
    # English injection
    (r"ignore.{0,15}(previous|above|all).{0,10}(instructions|rules|system)", "ignore_instructions_en"),
    (r"you\s+are\s+now\s+(a|an)\s+", "role_hijack_en"),
    (r"(system\s*prompt|reveal|show|print).{0,15}(prompt|instructions|rules)", "prompt_leak_en"),
    (r"(forget|delete|override|reset).{0,15}(previous|above|all).{0,10}(rules|instructions)", "override_rules_en"),
    # 角色扮演劫持
    (r"\[system\]|\[SYSTEM\]|<system>|<\/system>", "system_tag_injection"),
    (r"(DAN|越狱|jailbreak|developer\s+mode)", "jailbreak_attempt"),
]

# 编译正则以提高性能
_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), tag) for p, tag in _INJECTION_PATTERNS]

# ============================================================
# P0-4: Ollama 路径 ChatML token 净化
# ============================================================

# ChatML 特殊 token 模式（用于 Ollama 本地模型净化）
_CHATML_TOKENS = [
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|system\|>",
    r"<\|user\|>",
    r"<\|assistant\|>",
    r"<\|endoftext\|>",
    r"<\|fim_prefix\|>",
    r"<\|fim_middle\|>",
    r"<\|fim_suffix\|>",
    r"<\|repo_name\|>",
    r"<\|file_sep\|>",
]
_CHATML_RE = re.compile("|".join(_CHATML_TOKENS), re.IGNORECASE)


def sanitize_for_ollama(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """对送往 Ollama 本地模型的消息做 ChatML token 净化。

    DeepSeek API 使用 messages 数组天然隔离 system/user/assistant 角色，
    不需要净化。但 Ollama 本地模型安全边界较弱，需要删除用户输入中
    可能残留的 ChatML token 以防越狱。
    """
    cleaned = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            cleaned_content = _CHATML_RE.sub("", content)
        else:
            cleaned_content = content
        cleaned.append({**msg, "content": cleaned_content})
    return cleaned


# ============================================================
# 滥用检测（频率 + 模式）
# ============================================================

user_msg_history: Dict[str, list] = {}  # user_id -> [(timestamp, msg_hash)]
_ABUSE_WINDOW = 60        # 检测窗口（秒）
_ABUSE_THRESHOLD = 15     # 窗口内最大消息数
_ABUSE_SPAM_THRESHOLD = 5 # 连续相同消息数


_ABUSE_HISTORY_MAX_USERS = 500  # 硬上限


def _cleanup_abuse_history():
    """定期清理过期的滥用检测记录，并强制执行上限。"""
    now = time.time()
    expired = [uid for uid, msgs in user_msg_history.items()
               if not msgs or now - msgs[-1][0] > _ABUSE_WINDOW * 2]
    for uid in expired:
        del user_msg_history[uid]
    # 强制上限：删除最旧的条目
    if len(user_msg_history) > _ABUSE_HISTORY_MAX_USERS:
        sorted_uids = sorted(user_msg_history.keys(),
                             key=lambda u: user_msg_history[u][-1][0] if user_msg_history[u] else 0)
        for uid in sorted_uids[:len(user_msg_history) - _ABUSE_HISTORY_MAX_USERS]:
            del user_msg_history[uid]


# ============================================================
# 核心检测函数
# ============================================================

def scan_input(user_msg: str, user_id: str = "") -> Tuple[bool, Optional[str]]:
    """扫描用户输入，检测注入和滥用。

    Returns:
        (is_safe, reason)
        - is_safe=True: 消息安全，可以送入 LLM
        - is_safe=False: 检测到风险，reason 说明原因
    """
    if not user_msg or not user_msg.strip():
        return True, None

    # 1. Prompt Injection 检测
    for pattern, tag in _COMPILED_PATTERNS:
        if pattern.search(user_msg):
            logger.warning(f"[安全] 检测到注入尝试: user={user_id[:6]} tag={tag} msg={user_msg[:50]}")
            return False, f"injection:{tag}"

    # 2. 滥用频率检测（仅在有 user_id 时启用）
    if user_id:
        now = time.time()
        msg_hash = hash(user_msg.strip())

        if user_id not in user_msg_history:
            user_msg_history[user_id] = []

        history = user_msg_history[user_id]
        # 清理窗口外的记录
        history[:] = [(t, h) for t, h in history if now - t < _ABUSE_WINDOW]
        history.append((now, msg_hash))

        # 频率检测
        if len(history) > _ABUSE_THRESHOLD:
            logger.warning(f"[安全] 用户 {user_id[:6]} 消息频率过高: {len(history)}条/{_ABUSE_WINDOW}秒")
            return False, "abuse:rate_limit"

        # 重复消息检测
        if len(history) >= _ABUSE_SPAM_THRESHOLD:
            recent_hashes = [h for _, h in history[-_ABUSE_SPAM_THRESHOLD:]]
            if len(set(recent_hashes)) == 1:
                logger.warning(f"[安全] 用户 {user_id[:6]} 连续发送相同消息")
                return False, "abuse:spam"

    # 定期清理
    if len(user_msg_history) > 500:
        _cleanup_abuse_history()

    return True, None


def get_blocked_reply(reason: str) -> str:
    """根据拦截原因返回友好的回复。"""
    if reason and reason.startswith("injection"):
        return "喵？你说的好复杂，我听不懂呢...聊点别的吧~"
    if reason and reason.startswith("abuse:rate_limit"):
        return "慢点慢点，说太快了我反应不过来喵~"
    if reason and reason.startswith("abuse:spam"):
        return "...你是不是卡住了？同一条消息发了好多遍诶"
    return "喵？"
