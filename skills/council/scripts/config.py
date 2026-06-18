"""Council Skill 配置加载。

从 models.json + .env 构建运行时配置。
"""

import json
import os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# 运行时常量
# ═══════════════════════════════════════════════════════════════════════════════

MODE_CONFIG = {
    "fast": {
        "rounds": [1],
        "deduplicate": False,
        "judge": False,
        "output": "Round 1 三模型独立审查报告（无交叉验证，无裁决）",
    },
    "debate": {
        "rounds": [1, 2],
        "deduplicate": True,
        "judge": False,
        "output": "Round 1+2 交叉验证报告（合并去重，无主席裁决）",
    },
    "deep": {
        "rounds": [1, 2, 3],
        "deduplicate": True,
        "judge": True,
        "output": "完整裁决报告（PASS/BLOCK/REVISE）",
    },
}

# 默认超时
DEFAULT_TIMEOUT = 120
KIMI_TIMEOUT = 180
MIMO_TIMEOUT = 180

# Mimo 专用：大方案截断阈值
MIMO_MAX_PLAN_CHARS = 8000

# 去重阈值（CJK 2-gram 最优，经 20 组标注数据验证）
JACCARD_THRESHOLD = 0.35

# 方案文件大小上限（约 10 万中文字 / 200KB）
MAX_PLAN_CHARS = 200_000


# ═══════════════════════════════════════════════════════════════════════════════
# 模型配置加载
# ═══════════════════════════════════════════════════════════════════════════════

def _load_dotenv(filepath: Path):
    """简易 .env 解析器（避免 python-dotenv 依赖）。"""
    if not filepath.exists():
        return
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and not os.getenv(key):
                    os.environ[key] = value


def _load_models_json() -> dict:
    """加载 models.json，文件不存在时返回内置默认值。"""
    from utils import log_progress
    models_path = SKILL_DIR / "models.json"
    if models_path.exists():
        try:
            with open(models_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log_progress(f"models.json 解析失败，使用内置默认值: {e}", "error")
    # 内置默认值
    return {
        "supported_models": ["deepseek", "kimi", "mimo", "minimax"],
        "model_registry": {
            "deepseek": {"persona": "The Architect", "prefix": "DS",
                "api_key_env": "DEEPSEEK_API_KEY", "base_url_env": "DEEPSEEK_BASE_URL",
                "default_base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-v4-pro",
                "auth_header": "Bearer", "cost_per_1m": {"input": 3.15, "output": 6.31},
                "overrides": {"max_tokens": 4096}},
            "kimi": {"persona": "The Skeptic", "prefix": "K",
                "api_key_env": "KIMI_API_KEY", "base_url_env": "KIMI_BASE_URL",
                "default_base_url": "https://api.moonshot.cn/v1", "default_model": "kimi-k2.6",
                "auth_header": "Bearer", "cost_per_1m": {"input": 6.89, "output": 29.0},
                "overrides": {"temperature": 1.0}},
            "mimo": {"persona": "The Pragmatist", "prefix": "M",
                "api_key_env": "MIMO_CHAT_API_KEY", "base_url_env": "MIMO_CHAT_BASE_URL",
                "default_base_url": "https://api.xiaomimimo.com/v1", "default_model": "mimo-v2.5-pro",
                "auth_header": "api-key", "cost_per_1m": {"input": 3.15, "output": 6.31},
                "overrides": {"max_tokens": 4096}},
            "minimax": {"persona": "The Auditor", "prefix": "MM",
                "api_key_env": "MINIMAX_API_KEY", "base_url_env": "MINIMAX_BASE_URL",
                "default_base_url": "https://api.minimaxi.com/v1", "default_model": "MiniMax-M3",
                "auth_header": "Bearer", "cost_per_1m": {"input": 2.1, "output": 8.4},
                "overrides": {"thinking": {"type": "disabled"}}},
        },
        "judge_fallback_chain": [
            {"model": "deepseek-v4-pro", "api_key_env": "COUNCIL_JUDGE_API_KEY",
             "base_url_env": "COUNCIL_JUDGE_BASE_URL", "auth_header": "Bearer",
             "default_base_url": "https://api.deepseek.com/v1"},
            {"model": "deepseek-v4-flash", "api_key_env": "DEEPSEEK_API_KEY",
             "base_url_env": "DEEPSEEK_BASE_URL", "auth_header": "Bearer",
             "default_base_url": "https://api.deepseek.com/v1"},
        ],
    }


_MODELS_JSON = _load_models_json()

# 从 models.json 派生运行时配置
SUPPORTED_MODELS = set(_MODELS_JSON["supported_models"])

MODEL_OVERRIDES: dict[str, dict] = {}
COST_PER_M: dict[str, dict] = {}
for _key, _cfg in _MODELS_JSON["model_registry"].items():
    _model_name = _cfg["default_model"]
    if _cfg.get("overrides"):
        MODEL_OVERRIDES[_model_name] = _cfg["overrides"]
    if _cfg.get("cost_per_1m"):
        COST_PER_M[_model_name] = _cfg["cost_per_1m"]

JUDGE_FALLBACK_CHAIN = _MODELS_JSON["judge_fallback_chain"]


def _check_key_security():
    """检查 API Key 存储安全性，明文 .env 时输出提醒。"""
    from utils import log_progress
    skill_env = SKILL_DIR / ".env"
    if not skill_env.exists():
        return  # 没有 .env，可能用的系统环境变量

    try:
        content = skill_env.read_text(encoding="utf-8")
        # 检测是否有未注释的真实 Key（非 example/placeholder）
        real_keys = 0
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                _, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if value and len(value) > 20 and not value.startswith("your-"):
                    real_keys += 1
        if real_keys > 0:
            log_progress(
                f"⚠️  安全提醒：{real_keys} 个 API Key 以明文存储在 .env 文件中。",
                "info"
            )
            log_progress(
                "   建议：使用系统环境变量（setx）代替 .env 文件，或限制文件权限。",
                "info"
            )
            log_progress(
                "   风险：恶意脚本可能读取此固定路径的 Key，耗尽 API 额度。",
                "info"
            )
    except Exception:
        pass  # 读取失败时静默跳过


def load_config() -> dict:
    """加载配置（从 models.json 动态构建模型列表）。

    优先级：系统环境变量 > Skill .env > models.json 默认值 > 内置 fallback
    """
    _check_key_security()

    skill_env = SKILL_DIR / ".env"
    if skill_env.exists():
        _load_dotenv(skill_env)

    config = {}

    for model_key, reg in _MODELS_JSON.get("model_registry", {}).items():
        api_key_env = reg["api_key_env"]
        base_url_env = reg.get("base_url_env", "")
        default_model = reg["default_model"]
        default_base_url = reg["default_base_url"]
        auth_header = reg.get("auth_header", "Bearer")

        api_key = os.getenv(api_key_env, "")
        if model_key == "mimo" and not api_key:
            api_key = os.getenv("MIMO_API_KEY", "")

        config[model_key] = {
            "api_key": api_key,
            "base_url": os.getenv(base_url_env, default_base_url) if base_url_env else default_base_url,
            "model": default_model,
            "auth_header": auth_header,
        }

    # 裁判模型配置
    judge_api_key = os.getenv("COUNCIL_JUDGE_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
    # 有专用 Judge Key 时默认用 glm-5.2（智谱），否则保持 deepseek 兼容
    if judge_api_key and judge_api_key != os.getenv("DEEPSEEK_API_KEY", "") and not os.getenv("COUNCIL_JUDGE_MODEL"):
        judge_model = "glm-5.2"
    else:
        judge_model = os.getenv("COUNCIL_JUDGE_MODEL", "deepseek-v4-pro")
    # base_url 自动匹配模型厂商
    if judge_model.startswith("glm-"):
        default_judge_url = "https://open.bigmodel.cn/api/paas/v4"
    else:
        default_judge_url = "https://api.deepseek.com/v1"
    judge_base_url = os.getenv("COUNCIL_JUDGE_BASE_URL",
                               os.getenv("DEEPSEEK_BASE_URL", default_judge_url))
    config["judge"] = {
        "api_key": judge_api_key,
        "base_url": judge_base_url,
        "model": judge_model,
        "auth_header": "Bearer",
    }

    return config
