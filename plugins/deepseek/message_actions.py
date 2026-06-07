"""消息操作模块 — 撤回、手滑、随机分享。

让 bot 行为更像真人：偶尔发错消息撤回、突然分享东西。
"""
import asyncio
import random
from typing import Optional

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot import logger


async def send_and_maybe_recall(
    bot: Bot, event: MessageEvent, text: str, recall_chance: float = 0.02
) -> bool:
    """发送消息，有小概率撤回。

    流程: 发送消息 → 等待 2-5 秒 → 撤回 → 补发更正

    Returns:
        True 如果触发了撤回，False 否则
    """
    try:
        sent_msg = await bot.send(event, Message(text))
        msg_id = sent_msg.get("message_id") if isinstance(sent_msg, dict) else None

        if not msg_id or random.random() >= recall_chance:
            return False

        # 延迟 2-5 秒后撤回
        delay = random.uniform(2.0, 5.0)
        await asyncio.sleep(delay)

        try:
            await bot.delete_msg(message_id=msg_id)
            logger.info(f"[拟人] 手滑撤回: {text[:20]}...")
        except Exception as e:
            logger.debug(f"[拟人] 撤回失败（可能不支持）: {e}")
            return False

        # 撤回后补发更正消息
        await asyncio.sleep(random.uniform(0.5, 1.5))
        corrections = [
            "啊发错了",
            "当我没说",
            "这条忽略",
            "手滑了...",
            "撤回！看不见看不见",
            "oops 发错窗口了",
        ]
        await bot.send(event, Message(random.choice(corrections)))
        return True

    except Exception as e:
        logger.debug(f"[拟人] send_and_maybe_recall 失败: {e}")
        return False


async def maybe_share_something(bot: Bot, event: MessageEvent, share_chance: float = 0.03):
    """小概率随机分享：歌曲、想法、图片。

    在正常回复之后延迟 1-3 秒触发。
    """
    if random.random() >= share_chance:
        return

    await asyncio.sleep(random.uniform(1.0, 3.0))

    share_type = random.choice(["thought", "song", "meme"])

    try:
        if share_type == "thought":
            thoughts = [
                "啊对了我想起来了...",
                "诶突然想到一个事",
                "等下先不说这个",
                "刚才忘了说了",
                "emmm 算了不说了",
                "突然想发个表情包",
            ]
            await bot.send(event, Message(random.choice(thoughts)))

        elif share_type == "song":
            # 搜索一首随机歌曲并发送音乐卡片
            from .music_api import search_song, get_lyrics, extract_lyrics_snippet
            from .music_card import send_music_card
            from .music import _build_intro_message, _send_lyrics_snippet
            queries = ["热歌", "经典", "华语流行", "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "邓紫棋", "周深", "毛不易"]
            query = random.choice(queries)
            results = await search_song(query, limit=5)
            if results:
                song = random.choice(results[:3])
                intro = _build_intro_message(song, "recommend")
                await bot.send(event, Message(intro))
                await asyncio.sleep(random.uniform(0.5, 1.0))
                sent = await send_music_card(bot, event, song)
                if sent:
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    await _send_lyrics_snippet(bot, event, song)
            else:
                # API 不可用时 fallback 为纯文字
                from .api import call_deepseek_api
                prompt = (
                    "你突然想到一首歌想分享给对方。"
                    "只输出一句话，比如'突然想到这首歌'或'这首歌好好听'。"
                    "不要加括号。1句话就好。"
                )
                msg = await call_deepseek_api(
                    [{"role": "user", "content": prompt}], temperature=1.0
                )
                if msg and len(msg) > 3:
                    from .utils import filter_novel_actions
                    msg = filter_novel_actions(msg)
                    await bot.send(event, Message(msg))

        elif share_type == "meme":
            # 手滑发表情包
            from .sticker import select_sticker
            sticker_path = select_sticker("default")
            if sticker_path:
                from pathlib import Path
                await bot.send(event, MessageSegment.image(file=Path(sticker_path)))
                await asyncio.sleep(random.uniform(0.5, 1.5))
                await bot.send(event, Message("啊发错了"))

    except Exception as e:
        logger.debug(f"[拟人] 随机分享失败: {e}")
