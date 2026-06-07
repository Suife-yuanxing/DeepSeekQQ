"""构建 QQ 音乐卡片消息。

双保险策略：
1. 优先用 OneBot v11 原生 music 段
2. 失败则用自定义 JSON 分享卡片
"""
import json
import logging
from typing import Optional

from nonebot.adapters.onebot.v11 import Message, MessageSegment

logger = logging.getLogger("music_card")


def build_music_card_native(song_id: int) -> Message:
    """OneBot v11 原生 music 段（网易云音乐）。"""
    return Message(MessageSegment.music(type="163", id=str(song_id)))


def build_music_card_custom(song_info: dict) -> Message:
    """
    自定义 JSON 分享卡片（NapCat 兼容 fallback）。
    当原生 music 段不可用时使用。

    song_info 需包含: id, name, artist, album, cover_url
    """
    song_url = f"https://music.163.com/song?id={song_info['id']}"
    audio_url = f"https://music.163.com/song/media/outer/url?id={song_info['id']}.mp3"

    card = {
        "app": "com.tencent.structmsg",
        "desc": "音乐",
        "view": "music",
        "ver": "0.0.0.1",
        "prompt": f"[分享] {song_info['name']}",
        "meta": {
            "music": {
                "title": song_info["name"],
                "desc": f"{song_info.get('artist', '')} · {song_info.get('album', '')}",
                "musicUrl": audio_url,
                "preview": song_info.get("cover_url", ""),
                "jumpUrl": song_url,
            }
        },
    }
    return Message(MessageSegment.json(data=json.dumps(card, ensure_ascii=False)))


async def send_music_card(bot, event, song_info, song_id: int = 0) -> bool:
    """
    发送音乐卡片（双保险）。

    先尝试原生 music 段，失败后用自定义 JSON 卡片。
    返回 True 表示成功发送。
    """
    sid = song_id or song_info.id
    info_dict = {
        "id": sid,
        "name": song_info.name,
        "artist": song_info.artist,
        "album": song_info.album,
        "cover_url": song_info.cover_url,
    }

    # 方案1: 尝试原生 music 段
    try:
        msg = build_music_card_native(sid)
        await bot.send(event, msg)
        logger.info(f"[音乐] 原生卡片发送成功: {song_info.name}")
        return True
    except Exception as e:
        logger.warning(f"[音乐] 原生卡片发送失败（{e}），尝试自定义卡片")

    # 方案2: 自定义 JSON 卡片
    try:
        msg = build_music_card_custom(info_dict)
        await bot.send(event, msg)
        logger.info(f"[音乐] 自定义卡片发送成功: {song_info.name}")
        return True
    except Exception as e:
        logger.error(f"[音乐] 自定义卡片也失败: {e}")
        return False
