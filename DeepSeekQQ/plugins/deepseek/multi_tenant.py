"""多租户工具 — Phase 0.3/0.4 兼容包装层。

策略（开发计划 Phase 0 审计建议）：
  - bot_configs 表有数据时从表读（多租户模式）
  - bot_configs 表无对应记录时回退到 config.py 全局值（单 bot 兼容）
  - 现有代码零改动：config.py 中 MY_QQ / BOT_NAME 等仍可用作默认值

用法：
  from .multi_tenant import get_owner_qq, get_bot_persona
  owner = await get_owner_qq(bot_id=1)       # 或 None 取默认
  persona = await get_bot_persona(bot_id=1)  # 或 None 取默认
"""
from typing import Optional


async def get_owner_qq(bot_id: Optional[int] = None) -> str:
    """获取 Bot 的 owner QQ。

    Phase 0.3: 优先从 bot_configs.user_id 查 owner，无记录回退 config.MY_QQ。
    bot_id 为 None 时直接返回全局 MY_QQ（向后兼容）。
    """
    from .config import MY_QQ

    if bot_id is None:
        return str(MY_QQ) if MY_QQ else ""

    try:
        from .db_platform import get_bot
        bot = await get_bot(bot_id)
        if bot:
            owner_id = bot.get("user_id")
            if owner_id:
                # bot_configs.user_id 现在是平台用户 ID，但 QQ 通道需要的是 QQ 号
                # Phase 0 暂存：bot_configs 的 user_id 即 QQ 号（混合模式）
                # TODO: Phase 3 引入 channel_connections 后分离 QQ 号和平台用户 ID
                return str(owner_id)
    except Exception:
        pass

    return str(MY_QQ) if MY_QQ else ""


async def get_bot_persona(bot_id: Optional[int] = None) -> dict:
    """获取 Bot 人设的 12 项字段。

    Phase 0.4: 优先从 bot_configs.persona_json 读，无记录回退 config.py 全局值。
    返回 12 项 persona 字典。
    """
    from . import config

    defaults = {
        "BOT_NAME": config.BOT_NAME,
        "BOT_AGE": str(config.BOT_AGE),
        "BOT_GENDER": config.BOT_GENDER,
        "BOT_HEIGHT": str(config.BOT_HEIGHT),
        "BOT_BIRTHDAY": config.BOT_BIRTHDAY,
        "BOT_ZODIAC": config.BOT_ZODIAC,
        "BOT_CITY": config.BOT_CITY,
        "BOT_HOMETOWN": config.BOT_HOMETOWN,
        "BOT_OCCUPATION": config.BOT_OCCUPATION,
        "BOT_UNIVERSITY": config.BOT_UNIVERSITY,
        "BOT_MAJOR": config.BOT_MAJOR,
        "BOT_CAT_NAME": config.BOT_CAT_NAME,
    }

    if bot_id is not None:
        try:
            from .db_platform import get_bot
            import json
            bot = await get_bot(bot_id)
            if bot and bot.get("persona_json"):
                persona = json.loads(bot["persona_json"]) if isinstance(bot["persona_json"], str) else bot["persona_json"]
                # 映射 persona_json 字段 → BOT_* 字段
                # persona_json 使用 snake_case，BOT_* 用对应的字符串值
                return {
                    "BOT_NAME": bot.get("bot_name", defaults["BOT_NAME"]),
                    "BOT_AGE": str(persona.get("age", config.BOT_AGE)),
                    "BOT_GENDER": persona.get("gender", defaults["BOT_GENDER"]),
                    "BOT_HEIGHT": str(persona.get("height", config.BOT_HEIGHT)),
                    "BOT_BIRTHDAY": persona.get("birthday", defaults["BOT_BIRTHDAY"]),
                    "BOT_ZODIAC": persona.get("zodiac", defaults["BOT_ZODIAC"]),
                    "BOT_CITY": persona.get("city", defaults["BOT_CITY"]),
                    "BOT_HOMETOWN": persona.get("hometown", defaults["BOT_HOMETOWN"]),
                    "BOT_OCCUPATION": persona.get("occupation", defaults["BOT_OCCUPATION"]),
                    "BOT_UNIVERSITY": persona.get("university", defaults["BOT_UNIVERSITY"]),
                    "BOT_MAJOR": persona.get("major", defaults["BOT_MAJOR"]),
                    "BOT_CAT_NAME": persona.get("cat_name", defaults["BOT_CAT_NAME"]),
                }
        except Exception:
            pass

    return defaults


async def get_bot_id_by_session(session_id: str) -> Optional[int]:
    """从 session_id 推断 bot_id。

    Phase 0.9 完成后 session_id 格式为 '{channel}_{bot_id}_{user_id}'，
    目前（Phase 0.9 前）session_id 是 'private_{qq}' / 'group_{group_id}'，
    无法从中提取 bot_id。在此阶段返回 None（使用全局单 bot）。
    """
    return None  # Phase 0.9 完成后改写
