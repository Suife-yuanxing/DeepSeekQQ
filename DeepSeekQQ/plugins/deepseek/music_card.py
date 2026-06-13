"""构建 QQ 音乐卡片消息。

三级 fallback 策略：
1. XML 富文本消息（QQ 原生音乐卡片格式）
2. 原生 music 段（OneBot v11 标准）
3. 纯文本 + 封面图 + 链接（保底）
"""
import html
import json
import logging
from typing import Optional

from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment

logger = logging.getLogger("music_card")

# 网易云音乐 appid（用于 XML source 标签）
_NETEASE_APPID = "110493898"
_NETEASE_ICON = "https://p1.music.126.net/URy0uMj9m3qoZVj3v2oqRQ==/109951169469923450.jpg"


def build_music_card_xml(song_info) -> Message:
    """
    XML 富文本音乐卡片（QQ 原生格式）。
    在 QQ 中渲染为带封面、标题、来源的卡片。
    """
    song_id = song_info.id
    name = html.escape(song_info.name)
    artist = html.escape(song_info.artist)
    album = html.escape(song_info.album)
    cover = song_info.cover_url or ""
    song_url = f"https://music.163.com/song?id={song_id}"

    # brief 是折叠时显示的文字
    brief = f"[分享] {song_info.name} - {song_info.artist}"

    xml_data = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<msg serviceID="2" templateID="1" action="web" brief="{brief}" sourceMsgId="0" url="{song_url}" flag="0" adverSign="0" multiMsgFlag="0">
  <item layout="2">
    <picture cover="{cover}" />
    <title>{name}</title>
    <summary>{artist} · {album}</summary>
  </item>
  <source name="网易云音乐" icon="{_NETEASE_ICON}" url="https://music.163.com" action="app" a_actionData="com.netease.cloudmusic" i_actionData="tencent{_NETEASE_APPID}//" appid="{_NETEASE_APPID}" />
</msg>"""

    return Message(MessageSegment.xml(data=xml_data))


def build_music_card_native(song_id: int) -> Message:
    """OneBot v11 原生 music 段（网易云音乐）。"""
    return Message(MessageSegment.music(type="163", id=str(song_id)))


def build_music_card_text(song_info) -> Message:
    """
    纯文本 + 封面图 + 链接（保底方案）。
    当 XML 和 music 段都失败时使用。
    """
    msg = Message()
    # 封面图
    if song_info.cover_url:
        try:
            msg += MessageSegment.image(file=song_info.cover_url)
        except Exception:
            pass
    # 文本信息
    text = f"🎵 {song_info.name} - {song_info.artist}\n专辑: {song_info.album}\n🔗 https://music.163.com/song?id={song_info.id}"
    msg += MessageSegment.text(text)
    return msg


async def send_music_card(bot, event, song_info, song_id: int = 0) -> bool:
    """
    发送音乐卡片（三级 fallback）。

    1. XML 富文本消息（最可能渲染为卡片）
    2. 原生 music 段（OneBot v11 标准）
    3. 纯文本 + 封面图 + 链接（保底）

    返回 True 表示成功发送。
    """
    sid = song_id or song_info.id

    # 方案1: XML 富文本卡片
    try:
        msg = build_music_card_xml(song_info)
        await bot.send(event, msg)
        logger.info(f"[音乐] XML卡片发送成功: {song_info.name}")
        return True
    except Exception as e:
        logger.warning(f"[音乐] XML卡片失败（{e}），尝试原生music段")

    # 方案2: 原生 music 段
    try:
        msg = build_music_card_native(sid)
        await bot.send(event, msg)
        logger.info(f"[音乐] 原生卡片发送成功: {song_info.name}")
        return True
    except Exception as e:
        logger.warning(f"[音乐] 原生卡片也失败（{e}），使用文本保底")

    # 方案3: 纯文本 + 封面图
    try:
        msg = build_music_card_text(song_info)
        await bot.send(event, msg)
        logger.info(f"[音乐] 文本保底发送成功: {song_info.name}")
        return True
    except Exception as e:
        logger.error(f"[音乐] 所有方案均失败: {e}")
        return False
