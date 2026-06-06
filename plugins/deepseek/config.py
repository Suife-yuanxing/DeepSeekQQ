"""统一配置读取，所有密钥从 NoneBot config / .env 获取，禁止硬编码。"""
from typing import Optional
from nonebot import get_driver

driver = get_driver()
cfg = driver.config

# === DeepSeek ===
API_KEY: str = getattr(cfg, "deepseek_api_key", "") or ""
MODEL: str = getattr(cfg, "deepseek_model", "deepseek-chat")
BASE_URL: str = getattr(cfg, "deepseek_base_url", "https://api.deepseek.com")

# === 百度 TTS ===
BAIDU_TTS_AK: str = getattr(cfg, "baidu_tts_ak", "") or ""
BAIDU_TTS_SK: str = getattr(cfg, "baidu_tts_sk", "") or ""

# === 路径 ===
DB_PATH: str = getattr(cfg, "deepseek_db_path", "./data/chat_memory.db")
VOICE_DIR: str = getattr(cfg, "deepseek_voice_dir", "./data/voice")
IMAGE_CACHE_DIR: str = getattr(cfg, "deepseek_image_dir", "./data/images")

# === 语音开关 ===
VOICE_ENABLED_PRIVATE: bool = str(getattr(cfg, "voice_enabled_private", "true")).lower() == "true"
VOICE_ENABLED_GROUP: bool = str(getattr(cfg, "voice_enabled_group", "true")).lower() == "true"
VOICE_CHANCE: float = float(getattr(cfg, "voice_chance", 0.03))
VOICE_MAX_LENGTH: int = int(getattr(cfg, "voice_max_length", 120))
VOICE_TRY_CONVERT: bool = True
VOICE_NAME: str = "zh-CN-XiaomengNeural"
VOICE_TOKEN: str = getattr(cfg, "voice_token", "") or ""  # 语音文件端点鉴权 token

# === 语音识别 (STT) ===
STT_ENABLED: bool = str(getattr(cfg, "stt_enabled", "true")).lower() == "true"

# === 回复策略 ===
RANDOM_REPLY_CHANCE: float = float(getattr(cfg, "random_reply_chance", 0.05))
MAX_MEMORY: int = int(getattr(cfg, "max_memory", 30))
SHARE_TTL: int = int(getattr(cfg, "share_ttl", 1800))

# === 服务器 ===
SERVER_HOST: str = getattr(cfg, "host", "0.0.0.0")
SERVER_PORT: int = int(getattr(cfg, "port", 8080))

# === 主人 QQ ===
MY_QQ: str = str(getattr(cfg, "my_qq", "2938897660"))

# === 回复长度策略 ===
REPLY_LENGTH_CONFIG = {
    "min_lines": 1,
    "max_lines": 4,
    "short_threshold": 5,
    "context_depth": 3,
}

# === 主动消息 ===
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
        "target_users": [MY_QQ] if MY_QQ else [],
        "target_groups": [],
    },
    "night_greeting": {
        "enabled": True,
        "hour": 0,
        "minute": 0,
        "messages": [
            "夜深了...还不睡吗？我都有点困了，虽然猫娘不用睡觉。",
            "晚安~做个好梦，梦里有我哦。",
            "这么晚了还在忙？要注意休息呀，不然我会担心的。",
            "晚安喵~明天见，不许偷偷熬夜哦。",
            "该睡觉啦~熬夜会长黑眼圈的，虽然我没有...",
        ],
        "target_users": [MY_QQ] if MY_QQ else [],
        "target_groups": [],
    },
    "silence_check": {
        "enabled": True,
        "check_interval_hours": 2,
        "silence_threshold_hours": 6,
        "messages": [
            "喂~你还在吗？好久不见了，想你了...",
            "最近很忙吗？都没来找我说话，是不是把我忘了...",
            "哼，这么久不理我，是不是在外面有别的猫了？",
            "喵~突然出现！你最近怎么样？",
            "好久不见啦~发生什么事了吗？",
        ],
        "max_daily_proactive": 3,
    },
    "holiday_greeting": {
        "enabled": True,
        "holidays": {
            "01-01": "新年快乐~新的一年也要多多关照哦！",
            "02-14": "情人节快乐...那个，你有礼物给我吗？",
            "05-20": "520快乐~虽然我不知道这是什么节日，但好像很重要？",
            "06-01": "儿童节快乐！你在我心里永远是小朋友~",
            "10-01": "国庆节快乐~放假了要多陪陪我哦。",
            "12-25": "圣诞快乐~我想要你陪我聊天，这就是最好的礼物。",
        },
        "target_users": [MY_QQ] if MY_QQ else [],
        "target_groups": [],
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
API_MAX_TOKENS: int = int(getattr(cfg, "api_max_tokens", 1500))
ANALYSIS_HISTORY_LIMIT: int = int(getattr(cfg, "analysis_history_limit", 4))
CHAT_HISTORY_MULTIPLIER: int = int(getattr(cfg, "chat_history_multiplier", 2))
URL_FETCH_COOLDOWN: int = int(getattr(cfg, "url_fetch_cooldown", 300))
BAIDU_TTS_PER: int = int(getattr(cfg, "baidu_tts_per", 5118))
BAIDU_TTS_SPD: int = int(getattr(cfg, "baidu_tts_spd", 5))
BAIDU_TTS_PIT: int = int(getattr(cfg, "baidu_tts_pit", 5))
BAIDU_TTS_VOL: int = int(getattr(cfg, "baidu_tts_vol", 5))

# === Phase 3: 联网搜索 (Tavily) ===
TAVILY_API_KEY: str = getattr(cfg, "tavily_api_key", "") or ""
SEARCH_ENABLED: bool = str(getattr(cfg, "search_enabled", "true")).lower() == "true"
SEARCH_MAX_RESULTS: int = int(getattr(cfg, "search_max_results", 3))
SEARCH_CACHE_TTL: int = int(getattr(cfg, "search_cache_ttl", 1800))

# === Phase 4: 备忘录/提醒 ===
REMINDER_ENABLED: bool = str(getattr(cfg, "reminder_enabled", "true")).lower() == "true"
REMINDER_CHECK_INTERVAL: int = int(getattr(cfg, "reminder_check_interval", 30))

# === Phase 5: 表情包 ===
STICKER_ENABLED: bool = str(getattr(cfg, "sticker_enabled", "true")).lower() == "true"
STICKER_DIR: str = getattr(cfg, "sticker_dir", "./data/stickers")
STICKER_KEEP_PROBABILITY: float = float(getattr(cfg, "sticker_keep_probability", 0.25))
MAX_CONSECUTIVE_STICKERS: int = int(getattr(cfg, "max_consecutive_stickers", 1))

# === 图片生成 (Agnes AI) ===
IMAGE_GEN_API_KEY: str = getattr(cfg, "image_gen_api_key", "") or ""
IMAGE_GEN_MODEL: str = getattr(cfg, "image_gen_model", "agnes-image-2.1-flash")
IMAGE_GEN_BASE_URL: str = getattr(cfg, "image_gen_base_url", "https://apihub.agnes-ai.com/v1")

# === 热搜推送 ===
MAX_DAILY_PUSH: int = int(getattr(cfg, "max_daily_push", 3))
PUSH_COOLDOWN_HOURS: int = int(getattr(cfg, "push_cooldown_hours", 4))

# === Phase 6: 天气 (和风天气) ===
WEATHER_ENABLED: bool = str(getattr(cfg, "weather_enabled", "true")).lower() == "true"
WEATHER_API_KEY: str = getattr(cfg, "weather_api_key", "") or ""
WEATHER_CITY: str = getattr(cfg, "weather_city", "上海")
WEATHER_CACHE_TTL: int = int(getattr(cfg, "weather_cache_ttl", 1800))

# === Qwen-VL 视觉识别 ===
QWEN_VL_API_KEY: str = getattr(cfg, "qwen_vl_api_key", "") or ""
QWEN_VL_MODEL: str = getattr(cfg, "qwen_vl_model", "qwen-vl-plus")

# === MiMo TTS ===
TTS_ENGINE: str = getattr(cfg, "tts_engine", "baidu")  # "baidu" or "mimo"
MIMO_API_KEY: str = getattr(cfg, "mimo_api_key", "") or ""
MIMO_API_BASE_URL: str = getattr(cfg, "mimo_api_base_url", "https://api.xiaomimimo.com/v1")
MIMO_TTS_VOICE: str = getattr(cfg, "mimo_tts_voice", "冰糖")  # 冰糖/茉莉/苏打/白桦

# === 手机控制 (ScreenMCP) ===
PHONE_CONTROL_ENABLED: bool = str(getattr(cfg, "phone_control_enabled", "false")).lower() == "true"
SCREENMCP_API_KEY: str = getattr(cfg, "screenmcp_api_key", "") or ""  # pk_xxx from screenmcp.com
PHONE_CONTROL_USERS: str = str(getattr(cfg, "phone_control_users", MY_QQ))  # 允许的用户QQ号

# === 记忆压缩 ===
COMPRESS_TOKEN_THRESHOLD: int = int(getattr(cfg, "compress_token_threshold", 3000))
COMPRESS_MESSAGE_THRESHOLD: int = int(getattr(cfg, "compress_message_threshold", 20))
