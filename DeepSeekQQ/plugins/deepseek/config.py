"""统一配置读取，所有密钥从 NoneBot config / .env 获取，禁止硬编码。

惰性初始化：NoneBot 运行时从 driver.config 读；独立 FastAPI 8766 进程
（api_platform.server）无 NoneBot 时从 os.environ 读，避免 get_driver() 炸。
"""
import os
from typing import Optional


class _EnvironConfig:
    """os.environ 的 getattr 兼容包装（模拟 nonebot Config 接口）。

    支持 getattr(cfg, "deepseek_api_key", "") → 读 os.environ["DEEPSEEK_API_KEY"]。
    NoneBot config 属性是小写带下划线，env 变量是大写，自动转换。
    """

    def __getattr__(self, name: str):
        env_key = name.upper()
        return os.environ.get(env_key, None)

    def get(self, name: str, default=None):
        return os.environ.get(name.upper(), default)


try:
    from nonebot import get_driver
    from nonebot import logger as _logger
    try:
        driver = get_driver()
        cfg = driver.config
        _HAS_NONEBOT = True
    except Exception:
        # NoneBot 未初始化（8766 独立进程）—— 降级到环境变量包装
        cfg = _EnvironConfig()
        _HAS_NONEBOT = False
        import logging
        _logger = logging.getLogger("config")
except Exception:
    cfg = _EnvironConfig()
    _HAS_NONEBOT = False
    import logging
    _logger = logging.getLogger("config")


def _safe_float(val, default: float, name: str = "") -> float:
    """安全的 float 转换，失败时返回默认值并打印警告。"""
    try:
        return float(val)
    except (ValueError, TypeError):
        if name:
            _logger.warning(f"[配置] {name}={val!r} 无法转为 float，使用默认值 {default}")
        return default


def _safe_int(val, default: int, name: str = "") -> int:
    """安全的 int 转换，失败时返回默认值并打印警告。"""
    try:
        return int(val)
    except (ValueError, TypeError):
        if name:
            _logger.warning(f"[配置] {name}={val!r} 无法转为 int，使用默认值 {default}")
        return default


# === DeepSeek ===
API_KEY: str = str(getattr(cfg, "deepseek_api_key", "") or "").strip()
MODEL: str = getattr(cfg, "deepseek_model", "deepseek-chat")
BASE_URL: str = getattr(cfg, "deepseek_base_url", "https://api.deepseek.com")

# === Kimi (Moonshot) ===
KIMI_API_KEY: str = str(getattr(cfg, "kimi_api_key", "") or "").strip()
KIMI_BASE_URL: str = getattr(cfg, "kimi_base_url", "https://api.moonshot.cn/v1")
KIMI_MODEL: str = getattr(cfg, "kimi_model", "kimi-k2.6")

# === MiniMax (Council 交叉验证) ===
MINIMAX_API_KEY: str = str(getattr(cfg, "minimax_api_key", "") or "").strip()
MINIMAX_BASE_URL: str = getattr(cfg, "minimax_base_url", "https://api.minimaxi.com/v1")
MINIMAX_MODEL: str = getattr(cfg, "minimax_model", "MiniMax-M3")

# === 百度 TTS ===
BAIDU_TTS_AK: str = str(getattr(cfg, "baidu_tts_ak", "") or "").strip()
BAIDU_TTS_SK: str = str(getattr(cfg, "baidu_tts_sk", "") or "").strip()

# === 路径 ===
DB_PATH: str = getattr(cfg, "deepseek_db_path", "./data/chat_memory.db")
VOICE_DIR: str = getattr(cfg, "deepseek_voice_dir", "./data/voice")
IMAGE_CACHE_DIR: str = getattr(cfg, "deepseek_image_dir", "./data/images")

# === 语音开关 ===
VOICE_ENABLED_PRIVATE: bool = str(getattr(cfg, "voice_enabled_private", "true")).lower() == "true"
VOICE_ENABLED_GROUP: bool = str(getattr(cfg, "voice_enabled_group", "true")).lower() == "true"
VOICE_CHANCE: float = _safe_float(getattr(cfg, "voice_chance", 0.03), 0.03, "voice_chance")
VOICE_MAX_LENGTH: int = _safe_int(getattr(cfg, "voice_max_length", 120), 120, "voice_max_length")
VOICE_TRY_CONVERT: bool = True
# P1-6: 语音配置统一 — 全部从 env 读取，消除硬编码
VOICE_NAME: str = getattr(cfg, "voice_name", "zh-CN-XiaomengNeural")  # Baidu TTS 音色
VOICE_TOKEN: str = getattr(cfg, "voice_token", "") or ""  # 语音文件端点鉴权 token
VOICE_DEFAULT_STYLE: str = getattr(cfg, "voice_default_style", "温柔甜美，自然可爱")  # MiMo TTS 默认风格

# === 语音识别 (STT) ===
STT_ENABLED: bool = str(getattr(cfg, "stt_enabled", "true")).lower() == "true"

# === 回复策略 ===
RANDOM_REPLY_CHANCE: float = _safe_float(getattr(cfg, "random_reply_chance", 0.05), 0.05, "random_reply_chance")
MAX_MEMORY: int = _safe_int(getattr(cfg, "max_memory", 30), 30, "max_memory")
SHARE_TTL: int = _safe_int(getattr(cfg, "share_ttl", 1800), 1800, "share_ttl")

# === 服务器 ===
SERVER_HOST: str = getattr(cfg, "host", "127.0.0.1")
SERVER_PORT: int = _safe_int(getattr(cfg, "port", 8080), 8080, "port")
# CORS 白名单 origin（逗号分隔），为空则使用默认限制
CORS_ALLOW_ORIGINS: str = str(getattr(cfg, "cors_allow_origins", "") or "").strip()

# === 主人 QQ ===
MY_QQ: str = str(getattr(cfg, "my_qq", ""))

# === 人设（可通过 .env 覆盖） ===
BOT_NAME: str = str(getattr(cfg, "bot_name", "林念念") or "").strip() or "林念念"
BOT_AGE: int = _safe_int(getattr(cfg, "bot_age", 21), 21, "bot_age")
BOT_GENDER: str = str(getattr(cfg, "bot_gender", "女") or "").strip() or "女"
BOT_HEIGHT: int = _safe_int(getattr(cfg, "bot_height", 165), 165, "bot_height")
BOT_BIRTHDAY: str = str(getattr(cfg, "bot_birthday", "6月15日") or "").strip() or "6月15日"
BOT_ZODIAC: str = str(getattr(cfg, "bot_zodiac", "双子座") or "").strip() or "双子座"
BOT_CITY: str = str(getattr(cfg, "bot_city", "上海") or "").strip() or "上海"
BOT_HOMETOWN: str = str(getattr(cfg, "bot_hometown", "杭州") or "").strip() or "杭州"
BOT_OCCUPATION: str = str(getattr(cfg, "bot_occupation", "大学生") or "").strip() or "大学生"
BOT_UNIVERSITY: str = str(getattr(cfg, "bot_university", "") or "").strip()
BOT_MAJOR: str = str(getattr(cfg, "bot_major", "设计/数字媒体") or "").strip() or "设计/数字媒体"
BOT_CAT_NAME: str = str(getattr(cfg, "bot_cat_name", "团团") or "").strip() or "团团"

# === 回复长度策略 ===
REPLY_LENGTH_CONFIG = {
    "min_lines": 1,
    "max_lines": 4,
    "short_threshold": 5,
    "context_depth": 3,
}

# === Token 预算管理 ===
# DeepSeek 窗口 64K，分配 44% 给输入（~28K），留余量给输出和系统开销
MAX_INPUT_TOKENS: int = _safe_int(getattr(cfg, "max_input_tokens", 28000), 28000, "max_input_tokens")
RESERVE_OUTPUT_TOKENS: int = _safe_int(getattr(cfg, "reserve_output_tokens", 2000), 2000, "reserve_output_tokens")
SYSTEM_PROMPT_TOKEN_BUDGET: int = _safe_int(getattr(cfg, "system_prompt_token_budget", 4000), 4000, "system_prompt_token_budget")

# P1-2: 用户消息截断（防止超长消息撑爆 context）
MAX_USER_MSG_CHARS: int = _safe_int(getattr(cfg, "max_user_msg_chars", 800), 800, "max_user_msg_chars")

# === 主动消息 ===
def _get_target_users():
    """动态获取目标用户列表，确保运行时读取最新 MY_QQ 值。"""
    return [MY_QQ] if MY_QQ else []


PROACTIVE_CONFIG = {
    "morning_greeting": {
        "enabled": True,
        "hour": 9,
        "minute": 30,
        "messages": [
            "早呀~太阳都晒屁股了，你还在赖床吗？",
            "早安~今天也要元气满满哦，虽然我也刚睡醒...",
            "喵~早上好。我梦见你了呢，虽然梦到什么忘了...",
            "早~今天天气怎么样？要是不好就赖床吧，我陪你~",
            "早安！新的一天开始了，今天想聊点什么？",
        ],
        "target_users": _get_target_users,
        "target_groups": [],
    },
    "night_greeting": {
        "enabled": True,
        "hour": 0,
        "minute": 0,
        "messages": [
            "夜深了...还不睡吗？我都困了，你也早点休息呀。",
            "晚安~做个好梦，梦里有我哦。",
            "这么晚了还在忙？要注意休息呀，不然我会担心的。",
            "晚安喵~明天见，不许偷偷熬夜哦。",
            "该睡觉啦~熬夜会有黑眼圈的，明天再聊！",
        ],
        "target_users": _get_target_users,
        "target_groups": [],
    },
    "silence_check": {
        "enabled": True,
        "check_interval_hours": 2,
        "silence_threshold_hours": 6,
        "messages": [
            "喂~你还在吗？好久不见了，想你了...",
            "最近很忙吗？都没来找我说话，是不是把我忘了...",
            "哼，这么久不理我，是不是把我忘了？",
            "喵~突然出现！你最近怎么样？",
            "好久不见啦~发生什么事了吗？",
        ],
        "max_daily_proactive": 3,
    },
    "holiday_greeting": {
        "enabled": True,
        "holidays": {
            # 公历节日
            "01-01": "新年快乐~新的一年也要多多关照哦！",
            "02-14": "情人节快乐...那个，你有礼物给我吗？",
            "05-20": "520快乐~虽然我不知道这是什么节日，但好像很重要？",
            "06-01": "儿童节快乐！你在我心里永远是小朋友~",
            "10-01": "国庆节快乐~放假了要多陪陪我哦。",
            "12-25": "圣诞快乐~我想要你陪我聊天，这就是最好的礼物。",
            # 农历节日（2026年近似日期）
            "02-17": "除夕快乐~新的一年也要和我一起哦！",
            "02-18": "新年快乐！恭喜发财，红包拿来~喵~",
            "06-19": "端午安康~今天吃粽子了吗？我喜欢甜的！",
            "09-25": "中秋快乐！我们一起看月亮吧~虽然你在屏幕那一边...",
        },
        "target_users": _get_target_users,
        "target_groups": [],
    },
    "sleep_nag": {
        "enabled": True,
        "max_nags_per_night": 2,
        "target_users": _get_target_users,
    },
}

AFFECTION_LEVELS = [
    (0, "陌生人"),
    (20, "认识的人"),
    (50, "有点在意"),
    (100, "喜欢的人"),
    (200, "重要的人"),
    (500, "专属主人"),
    (1000, "命定之人"),
]

# === P2: 统一硬编码配置 ===
API_MAX_TOKENS: int = _safe_int(getattr(cfg, "api_max_tokens", 1500), 1500, "api_max_tokens")
ANALYSIS_HISTORY_LIMIT: int = _safe_int(getattr(cfg, "analysis_history_limit", 4), 4, "analysis_history_limit")
CHAT_HISTORY_MULTIPLIER: int = _safe_int(getattr(cfg, "chat_history_multiplier", 2), 2, "chat_history_multiplier")
URL_FETCH_COOLDOWN: int = _safe_int(getattr(cfg, "url_fetch_cooldown", 300), 300, "url_fetch_cooldown")
BAIDU_TTS_PER: int = _safe_int(getattr(cfg, "baidu_tts_per", 5118), 5118, "baidu_tts_per")
BAIDU_TTS_SPD: int = _safe_int(getattr(cfg, "baidu_tts_spd", 5), 5, "baidu_tts_spd")
BAIDU_TTS_PIT: int = _safe_int(getattr(cfg, "baidu_tts_pit", 5), 5, "baidu_tts_pit")
BAIDU_TTS_VOL: int = _safe_int(getattr(cfg, "baidu_tts_vol", 5), 5, "baidu_tts_vol")

# === Phase 3: 联网搜索 (Tavily) ===
TAVILY_API_KEY: str = str(getattr(cfg, "tavily_api_key", "") or "").strip()
SEARCH_ENABLED: bool = str(getattr(cfg, "search_enabled", "true")).lower() == "true"
SEARCH_MAX_RESULTS: int = _safe_int(getattr(cfg, "search_max_results", 3), 3, "search_max_results")
SEARCH_CACHE_TTL: int = _safe_int(getattr(cfg, "search_cache_ttl", 1800), 1800, "search_cache_ttl")

# === Phase 4: 备忘录/提醒 ===
REMINDER_ENABLED: bool = str(getattr(cfg, "reminder_enabled", "true")).lower() == "true"
REMINDER_CHECK_INTERVAL: int = _safe_int(getattr(cfg, "reminder_check_interval", 30), 30, "reminder_check_interval")

# === Phase 5: 表情包 ===
STICKER_ENABLED: bool = str(getattr(cfg, "sticker_enabled", "true")).lower() == "true"
STICKER_DIR: str = getattr(cfg, "sticker_dir", "./data/stickers")
STICKER_KEEP_PROBABILITY: float = _safe_float(getattr(cfg, "sticker_keep_probability", 0.4), 0.4, "sticker_keep_probability")
MAX_CONSECUTIVE_STICKERS: int = _safe_int(getattr(cfg, "max_consecutive_stickers", 2), 2, "max_consecutive_stickers")

# === 图片生成 (Agnes AI) ===
IMAGE_GEN_API_KEY: str = str(getattr(cfg, "image_gen_api_key", "") or "").strip()
IMAGE_GEN_MODEL: str = getattr(cfg, "image_gen_model", "agnes-image-2.1-flash")
IMAGE_GEN_BASE_URL: str = getattr(cfg, "image_gen_base_url", "https://apihub.agnes-ai.com/v1")

# === 热搜推送 ===
MAX_DAILY_PUSH: int = _safe_int(getattr(cfg, "max_daily_push", 3), 3, "max_daily_push")
PUSH_COOLDOWN_HOURS: int = _safe_int(getattr(cfg, "push_cooldown_hours", 4), 4, "push_cooldown_hours")

# === 社交信息流引擎（Social Feed Engine）===
FEED_MAX_ITEMS: int = _safe_int(getattr(cfg, "feed_max_items", 100), 100, "feed_max_items")
FEED_TTL_MINUTES: int = _safe_int(getattr(cfg, "feed_ttl_minutes", 360), 360, "feed_ttl_minutes")
FEED_SCROLL_INTERVAL_MINUTES: int = _safe_int(getattr(cfg, "feed_scroll_interval_minutes", 45), 45, "feed_scroll_interval_minutes")
FEED_INJECTION_CHANCE: float = _safe_float(getattr(cfg, "feed_injection_chance", 0.12), 0.12, "feed_injection_chance")

# === 热梗自动检测 ===
MEME_AUTO_UPDATE_ENABLED: bool = str(getattr(cfg, "meme_auto_update_enabled", "true")).lower() == "true"
DYNAMIC_MEME_TTL_HOURS: int = _safe_int(getattr(cfg, "dynamic_meme_ttl_hours", 72), 72, "dynamic_meme_ttl_hours")
DYNAMIC_MEME_MAX_COUNT: int = _safe_int(getattr(cfg, "dynamic_meme_max_count", 10), 10, "dynamic_meme_max_count")
MEME_DETECTION_CONFIDENCE_THRESHOLD: float = _safe_float(getattr(cfg, "meme_detection_confidence_threshold", 0.7), 0.7, "meme_detection_confidence_threshold")

# === 群聊热度状态机（Heat Engine）===
HEAT_HALF_LIFE: int = _safe_int(getattr(cfg, "heat_half_life", 300), 300, "heat_half_life")
HEAT_IDLE_TIMEOUT: int = _safe_int(getattr(cfg, "heat_idle_timeout", 30), 30, "heat_idle_timeout")

# === Phase 6: 天气 (和风天气) ===
WEATHER_ENABLED: bool = str(getattr(cfg, "weather_enabled", "true")).lower() == "true"
WEATHER_API_KEY: str = str(getattr(cfg, "weather_api_key", "") or "").strip()
WEATHER_CITY: str = getattr(cfg, "weather_city", "上海")
WEATHER_CACHE_TTL: int = _safe_int(getattr(cfg, "weather_cache_ttl", 1800), 1800, "weather_cache_ttl")

# === Qwen-VL 视觉识别 ===
QWEN_VL_API_KEY: str = str(getattr(cfg, "qwen_vl_api_key", "") or "").strip()
QWEN_VL_MODEL: str = getattr(cfg, "qwen_vl_model", "qwen-vl-plus")

# === 智谱AI GLM（视觉免费模型 glm-4v-flash，永久免费、并发限制10）===
GLM_API_KEY: str = str(getattr(cfg, "glm_api_key", "") or "").strip()
GLM_MODEL: str = getattr(cfg, "glm_model", "glm-4-flash")
GLM_VISION_MODEL: str = getattr(cfg, "glm_vision_model", "glm-4v-flash")

# === MiMo TTS ===
TTS_ENGINE: str = getattr(cfg, "tts_engine", "baidu")  # "baidu" / "mimo" / "volcano"
MIMO_API_KEY: str = str(getattr(cfg, "mimo_api_key", "") or "").strip()
MIMO_API_BASE_URL: str = getattr(cfg, "mimo_api_base_url", "https://api.xiaomimimo.com/v1")
MIMO_TTS_VOICE: str = getattr(cfg, "mimo_tts_voice", "冰糖")  # 冰糖/茉莉/苏打/白桦

# === Mimo Chat (Council 交叉验证用) ===
MIMO_CHAT_API_KEY: str = str(getattr(cfg, "mimo_chat_api_key", "") or "").strip()
MIMO_CHAT_BASE_URL: str = getattr(cfg, "mimo_chat_base_url", "https://api.xiaomimimo.com/v1")
MIMO_CHAT_MODEL: str = getattr(cfg, "mimo_chat_model", "mimo-v2.5-pro")

# === 火山引擎 TTS ===
VOLCANO_APP_ID: str = str(getattr(cfg, "volcano_app_id", "") or "").strip()
VOLCANO_ACCESS_TOKEN: str = str(getattr(cfg, "volcano_access_token", "") or "").strip()
VOLCANO_VOICE_TYPE: str = getattr(cfg, "volcano_voice_type", "zh_female_jiaochuannv_uranus_bigtts")  # 默认娇喘女声大模型
VOLCANO_TTS_URL: str = getattr(cfg, "volcano_tts_url", "https://openspeech.bytedance.com/api/v1/tts")

# === MiMo STT (语音识别) ===
STT_ENGINE: str = getattr(cfg, "stt_engine", "mimo")  # "mimo" or "baidu"
MIMO_STT_API_KEY: str = str(getattr(cfg, "mimo_stt_api_key", "") or "").strip()
MIMO_STT_API_BASE_URL: str = getattr(cfg, "mimo_stt_api_base_url", "https://api.xiaomimimo.com/v1")
MIMO_STT_MODEL: str = getattr(cfg, "mimo_stt_model", "whisper-1")

# === 手机控制 (MCP 工具) ===
# 中继监听端口（手机 App 连接 ws://服务器公网IP:此端口）
PHONE_RELAY_PORT: int = _safe_int(getattr(cfg, "phone_relay_port", 8765), 8765, "phone_relay_port")
# 认证密钥（手机 App 和控制端共用）
PHONE_WS_KEY: str = str(getattr(cfg, "phone_ws_key", "") or "").strip()
# C-2: IP 白名单（逗号分隔），默认仅允许本地
PHONE_ALLOW_IPS: str = str(getattr(cfg, "phone_allow_ips", "127.0.0.1") or "").strip()

# === 音乐功能 ===
MUSIC_ENABLED: bool = str(getattr(cfg, "music_enabled", "true")).lower() == "true"
MUSIC_VOICE_CHANCE: float = _safe_float(getattr(cfg, "music_voice_chance", 0.5), 0.5, "music_voice_chance")

# === 消息分段 ===
# QQ / OneBot 单条消息上限约 900 字符，超过自动分段
MAX_REPLY_CHARS: int = _safe_int(getattr(cfg, "max_reply_chars", 900), 900, "max_reply_chars")

# === 记忆压缩 ===
COMPRESS_TOKEN_THRESHOLD: int = _safe_int(getattr(cfg, "compress_token_threshold", 3000), 3000, "compress_token_threshold")
COMPRESS_MESSAGE_THRESHOLD: int = _safe_int(getattr(cfg, "compress_message_threshold", 20), 20, "compress_message_threshold")

# === 3D: Think-then-Speak 双通道回复 ===
THINK_THEN_SPEAK_ENABLED: bool = str(getattr(cfg, "think_then_speak_enabled", "true")).lower() == "true"
THINK_MAX_TOKENS: int = _safe_int(getattr(cfg, "think_max_tokens", 200), 200, "think_max_tokens")
THINK_TEMPERATURE: float = _safe_float(getattr(cfg, "think_temperature", 0.3), 0.3, "think_temperature")

# === Ollama 本地模型 ===
# 默认关闭。服务器不跑本地模型（内存仅 2GB 可用），开发机可设为 true
OLLAMA_ENABLED: bool = str(getattr(cfg, "ollama_enabled", "false")).lower() == "true"

# === 打字延迟系数 ===
# 全局乘数：1.0=默认，0=即时回复（调试用），0.5=一半延迟
TYPING_DELAY_FACTOR: float = _safe_float(getattr(cfg, "typing_delay_factor", 1.0), 1.0, "typing_delay_factor")

# ============================================================
# === 真人化概率配置（HUMANIZE_）===
# ============================================================

# 错别字（分好感度三档，高好感最随意）
HUMANIZE_TYPO_CHANCE_HIGH: float = _safe_float(getattr(cfg, "humanize_typo_chance_high", 0.08), 0.08, "humanize_typo_chance_high")
HUMANIZE_TYPO_CHANCE_MID: float = _safe_float(getattr(cfg, "humanize_typo_chance_mid", 0.10), 0.10, "humanize_typo_chance_mid")
HUMANIZE_TYPO_CHANCE_LOW: float = _safe_float(getattr(cfg, "humanize_typo_chance_low", 0.05), 0.05, "humanize_typo_chance_low")

# 结巴
HUMANIZE_STUTTER_CHANCE_BASE: float = _safe_float(getattr(cfg, "humanize_stutter_chance_base", 0.05), 0.05, "humanize_stutter_chance_base")
HUMANIZE_STUTTER_CHANCE_AROUSED: float = _safe_float(getattr(cfg, "humanize_stutter_chance_aroused", 0.08), 0.08, "humanize_stutter_chance_aroused")
HUMANIZE_STUTTER_AFFECTION_MULTIPLIER: float = _safe_float(getattr(cfg, "humanize_stutter_affection_multiplier", 1.3), 1.3, "humanize_stutter_affection_multiplier")
HUMANIZE_STUTTER_NOOP_CHANCE: float = _safe_float(getattr(cfg, "humanize_stutter_noop_chance", 0.0), 0.0, "humanize_stutter_noop_chance")

# 改口
HUMANIZE_MIND_CHANGE_CHANCE_HIGH: float = _safe_float(getattr(cfg, "humanize_mind_change_chance_high", 0.06), 0.06, "humanize_mind_change_chance_high")
HUMANIZE_MIND_CHANGE_CHANCE_MID: float = _safe_float(getattr(cfg, "humanize_mind_change_chance_mid", 0.05), 0.05, "humanize_mind_change_chance_mid")
HUMANIZE_MIND_CHANGE_CHANCE_LOW: float = _safe_float(getattr(cfg, "humanize_mind_change_chance_low", 0.03), 0.03, "humanize_mind_change_chance_low")

# 不确定
HUMANIZE_UNCERTAINTY_CHANCE: float = _safe_float(getattr(cfg, "humanize_uncertainty_chance", 0.03), 0.03, "humanize_uncertainty_chance")

# 语气前缀
HUMANIZE_REACTION_PREFIX_HIGH: float = _safe_float(getattr(cfg, "humanize_reaction_prefix_high", 0.20), 0.20, "humanize_reaction_prefix_high")
HUMANIZE_REACTION_PREFIX_MID: float = _safe_float(getattr(cfg, "humanize_reaction_prefix_mid", 0.15), 0.15, "humanize_reaction_prefix_mid")
HUMANIZE_REACTION_PREFIX_LOW: float = _safe_float(getattr(cfg, "humanize_reaction_prefix_low", 0.08), 0.08, "humanize_reaction_prefix_low")

# 颜文字（按情绪分档）
HUMANIZE_KAOMOJI_EXCITED: float = _safe_float(getattr(cfg, "humanize_kaomoji_excited", 0.25), 0.25, "humanize_kaomoji_excited")
HUMANIZE_KAOMOJI_HAPPY: float = _safe_float(getattr(cfg, "humanize_kaomoji_happy", 0.20), 0.20, "humanize_kaomoji_happy")
HUMANIZE_KAOMOJI_SHY: float = _safe_float(getattr(cfg, "humanize_kaomoji_shy", 0.18), 0.18, "humanize_kaomoji_shy")
HUMANIZE_KAOMOJI_ANGRY: float = _safe_float(getattr(cfg, "humanize_kaomoji_angry", 0.15), 0.15, "humanize_kaomoji_angry")
HUMANIZE_KAOMOJI_SAD: float = _safe_float(getattr(cfg, "humanize_kaomoji_sad", 0.15), 0.15, "humanize_kaomoji_sad")
HUMANIZE_KAOMOJI_TSUNDERE: float = _safe_float(getattr(cfg, "humanize_kaomoji_tsundere", 0.15), 0.15, "humanize_kaomoji_tsundere")
HUMANIZE_KAOMOJI_TEASE: float = _safe_float(getattr(cfg, "humanize_kaomoji_tease", 0.20), 0.20, "humanize_kaomoji_tease")
HUMANIZE_KAOMOJI_DEFAULT: float = _safe_float(getattr(cfg, "humanize_kaomoji_default", 0.15), 0.15, "humanize_kaomoji_default")

# 活动提及
HUMANIZE_ACTIVITY_MENTION_CHANCE: float = _safe_float(getattr(cfg, "humanize_activity_mention_chance", 0.08), 0.08, "humanize_activity_mention_chance")

# ============================================================
# === 行为引擎概率配置（BEHAVIOR_）===
# ============================================================

BEHAVIOR_WEATHER_CHANCE: float = _safe_float(getattr(cfg, "behavior_weather_chance", 0.25), 0.25, "behavior_weather_chance")
BEHAVIOR_HOLIDAY_CHANCE: float = _safe_float(getattr(cfg, "behavior_holiday_chance", 0.15), 0.15, "behavior_holiday_chance")
BEHAVIOR_SCROLL_CHANCE: float = _safe_float(getattr(cfg, "behavior_scroll_chance", 0.12), 0.12, "behavior_scroll_chance")
BEHAVIOR_HOT_TOPIC_CHANCE: float = _safe_float(getattr(cfg, "behavior_hot_topic_chance", 0.05), 0.05, "behavior_hot_topic_chance")
BEHAVIOR_SEASONAL_CHANCE: float = _safe_float(getattr(cfg, "behavior_seasonal_chance", 0.08), 0.08, "behavior_seasonal_chance")
BEHAVIOR_MICRO_EVENT_CHANCE: float = _safe_float(getattr(cfg, "behavior_micro_event_chance", 0.02), 0.02, "behavior_micro_event_chance")
BEHAVIOR_RANDOM_CHANCE: float = _safe_float(getattr(cfg, "behavior_random_chance", 0.05), 0.05, "behavior_random_chance")

# 轻量行为（短消息用）
BEHAVIOR_LIGHT_MICRO_EVENT_CHANCE: float = _safe_float(getattr(cfg, "behavior_light_micro_event_chance", 0.15), 0.15, "behavior_light_micro_event_chance")
BEHAVIOR_LIGHT_WEATHER_CHANCE: float = _safe_float(getattr(cfg, "behavior_light_weather_chance", 0.10), 0.10, "behavior_light_weather_chance")
BEHAVIOR_LIGHT_SEASONAL_CHANCE: float = _safe_float(getattr(cfg, "behavior_light_seasonal_chance", 0.05), 0.05, "behavior_light_seasonal_chance")

# 最多合并几个行为
BEHAVIOR_MAX_COMBINED: int = _safe_int(getattr(cfg, "behavior_max_combined", 2), 2, "behavior_max_combined")

# ============================================================
# === 其他行为/人设配置 ===
# ============================================================

# 口头禅学习门槛（从硬编码 600 降至 300，可配置）
CATCHPHRASE_LEARN_AFFECTION_MIN: int = _safe_int(getattr(cfg, "catchphrase_learn_affection_min", 300), 300, "catchphrase_learn_affection_min")

# 周兴趣评估开关（真人化Q5：默认开启，结果由personality_drift读取）
PERSONALITY_WEEKLY_EVAL_ENABLED: bool = str(getattr(cfg, "personality_weekly_eval_enabled", "true")).lower() == "true"

# ============================================================
# === 真人化 Phase 5.2 参数调优（HUMANIZE_TUNING_）===
# ============================================================

# 情绪累积触发阈值（默认 3.0）
# 低阈值 → 更敏感，情绪切换频繁；高阈值 → 更稳定，需要更多证据才切换
# 校准依据：观察实际聊天中 bot 情绪切换频率。理想频率为每 5-20 轮对话一次切换。
HUMANIZE_TUNING_EMOTION_ACCUMULATOR_THRESHOLD: float = _safe_float(
    getattr(cfg, "humanize_tuning_emotion_accumulator_threshold", 3.0), 3.0,
    "humanize_tuning_emotion_accumulator_threshold")

# 情绪隐藏概率 — 中强度情绪隐藏率（0.4 = 40%）
# 高值 → bot 更含蓄；低值 → bot 更直白
HUMANIZE_TUNING_EMOTION_HIDE_MEDIUM: float = _safe_float(
    getattr(cfg, "humanize_tuning_emotion_hide_medium", 0.4), 0.4,
    "humanize_tuning_emotion_hide_medium")

# 情绪隐藏概率 — 低强度情绪隐藏率（0.8 = 80%）
HUMANIZE_TUNING_EMOTION_HIDE_LOW: float = _safe_float(
    getattr(cfg, "humanize_tuning_emotion_hide_low", 0.8), 0.8,
    "humanize_tuning_emotion_hide_low")

# 好感度修正 — 高好感度隐藏概率修正系数（< 1.0 表示更愿意表达）
HUMANIZE_TUNING_HIDE_AFFECTION_MODIFIER_HIGH: float = _safe_float(
    getattr(cfg, "humanize_tuning_hide_affection_modifier_high", 0.5), 0.5,
    "humanize_tuning_hide_affection_modifier_high")

# 疲劳基线学习 — 建立基线所需最小样本数（默认 20 条消息）
HUMANIZE_TUNING_BASELINE_MIN_SAMPLES: int = _safe_int(
    getattr(cfg, "humanize_tuning_baseline_min_samples", 20), 20,
    "humanize_tuning_baseline_min_samples")

# 缺席事件 — 活跃时段基础触发概率（0.05 = 5%每条消息时检查）
HUMANIZE_TUNING_ABSENCE_ACTIVE_PROB: float = _safe_float(
    getattr(cfg, "humanize_tuning_absence_active_prob", 0.05), 0.05,
    "humanize_tuning_absence_active_prob")

# 缺席事件 — 上课/工作时间触发概率
HUMANIZE_TUNING_ABSENCE_CLASS_PROB: float = _safe_float(
    getattr(cfg, "humanize_tuning_absence_class_prob", 0.50), 0.50,
    "humanize_tuning_absence_class_prob")

# 情绪残留 — 恢复后基础残留比例（0.3 = 30% 原始强度）
HUMANIZE_TUNING_RESIDUE_BASE_RATIO: float = _safe_float(
    getattr(cfg, "humanize_tuning_residue_base_ratio", 0.3), 0.3,
    "humanize_tuning_residue_base_ratio")

# 情绪残留 — 每小时衰减率（10-15%）
HUMANIZE_TUNING_RESIDUE_DECAY_PER_HOUR: float = _safe_float(
    getattr(cfg, "humanize_tuning_residue_decay_per_hour", 0.10), 0.10,
    "humanize_tuning_residue_decay_per_hour")

# 情绪复发 — 基础触发概率（0.08 = 8%）
HUMANIZE_TUNING_REKINDLE_BASE_PROB: float = _safe_float(
    getattr(cfg, "humanize_tuning_rekindle_base_prob", 0.08), 0.08,
    "humanize_tuning_rekindle_base_prob")
