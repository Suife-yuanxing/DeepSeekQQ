"""时间自检模块 — 校验 LLM 回复中的时间相关表达，修正不合理内容。

防止 bot 说出与当前北京时间不符的时间相关语句，
例如凌晨3点说"早上好"、上午10点说"晚安"等。
"""

import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Tuple

from nonebot import logger

# 北京时间时区
_BEIJING_TZ = timezone(timedelta(hours=8))

# ============================================================
# 时间校验规则
# ============================================================

# (正则模式, (合法开始小时, 合法结束小时), 替换函数 或 None=删除整句)
# 对于跨夜时段（如20:00-05:00），用 is_overnight=True
_TIME_RULES: list = []


def _register(patterns: list, start_h: int, end_h: int, fix: str = None, overwrite: bool = False,
              strip_only: bool = False):
    """注册时间校验规则。

    Args:
        patterns: 要匹配的正则模式列表
        start_h: 合法时段起始小时（含）
        end_h: 合法时段结束小时（含），跨夜用 is_overnight
        fix: 替换文本（None=删除匹配片段）
        overwrite: True=整句替换为fix，False=只替换匹配到的词组
        strip_only: True=直接删除匹配内容不替换
    """
    for pat in patterns:
        _TIME_RULES.append((re.compile(pat), start_h, end_h, fix, overwrite, strip_only))


# ---- 早安类 (合法 05:00-11:00) ----
_register(
    [r'早安', r'早上好', r'早呀', r'早晨', r'早啊', r'起床了?', r'起床啦', r'醒了吗',
     r'刚醒', r'才起床', r'刚起床', r'睡醒', r'起床没'],
    5, 11, strip_only=True
)

# ---- 晚安类 (合法 20:00-05:00 跨夜) ----
_register(
    [r'晚安', r'好梦', r'快去睡', r'去睡觉', r'快睡吧', r'早点睡', r'快去睡觉',
     r'该睡了', r'睡了哦', r'睡觉了', r'睡吧'],
    20, 5, strip_only=True
)

# ---- 下午好 (合法 12:00-18:00) ----
_register(
    [r'下午好'],
    12, 18, strip_only=True
)

# ---- 午饭/午餐 (合法 11:00-14:00) ----
_register(
    [r'吃午饭', r'午饭', r'午餐', r'吃中饭', r'中饭'],
    11, 14, fix='吃饭', overwrite=False
)

# ---- 晚饭/晚餐 (合法 17:00-21:00) ----
_register(
    [r'吃晚饭', r'晚饭', r'晚餐'],
    17, 21, fix='吃饭', overwrite=False
)

# ---- 凌晨/半夜 (合法 00:00-05:00) ----
_register(
    [r'凌晨', r'半夜', r'深更半夜'],
    0, 5, strip_only=True
)

# ---- 通宵/熬夜 (合法 00:00-08:00) ----
_register(
    [r'通宵', r'熬夜到现在', r'还没睡', r'一直没睡'],
    0, 8, strip_only=True
)

# ---- 午休/午睡 (合法 12:00-15:00) ----
_register(
    [r'午休', r'午睡', r'睡午觉'],
    12, 15, strip_only=True
)

# ---- 吃早餐/早饭 (合法 06:00-10:00) ----
_register(
    [r'吃早饭', r'早饭', r'早餐', r'吃早餐'],
    6, 10, fix='吃东西', overwrite=False
)


def _is_time_valid(now_hour: int, start_h: int, end_h: int) -> bool:
    """检查当前小时是否在合法时段内。

    支持跨夜时段：如 20:00-05:00 => 当前时间在20-23或0-5之间。
    """
    if start_h <= end_h:
        return start_h <= now_hour < end_h
    else:
        # 跨夜时段：如 20-05
        return now_hour >= start_h or now_hour < end_h


def validate_time_in_reply(reply_text: str) -> str:
    """校验 LLM 回复中的时间相关内容，修正不合理的时间表达。

    Args:
        reply_text: LLM 生成的回复文本

    Returns:
        修正后的文本
    """
    if not reply_text:
        return reply_text

    now = datetime.now(_BEIJING_TZ)
    hour = now.hour
    text = reply_text
    fixed_count = 0

    for pattern, start_h, end_h, fix, overwrite, strip_only in _TIME_RULES:
        match = pattern.search(text)
        if not match:
            continue

        # 检查当前时间是否在合法时段内
        if _is_time_valid(hour, start_h, end_h):
            continue  # 合法，跳过

        # 不合法 → 修正
        matched_text = match.group(0)
        if strip_only:
            # 删除匹配到的词组
            text = pattern.sub("", text)
            fixed_count += 1
            logger.debug(f"[时间校验] 删除 '{matched_text}'（当前{hour}时，合法{start_h}-{end_h}时）")
        elif overwrite:
            # 整句替换
            text = fix
            fixed_count += 1
            logger.debug(f"[时间校验] 整句替换 '{matched_text}' → '{fix}'（当前{hour}时）")
        elif fix:
            # 替换匹配到的词组
            text = pattern.sub(fix, text)
            fixed_count += 1
            logger.debug(f"[时间校验] 替换 '{matched_text}' → '{fix}'（当前{hour}时，合法{start_h}-{end_h}时）")

    # 清理多余空格和标点
    if fixed_count > 0:
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'，{2,}', '，', text)
        text = text.strip()
        if text:
            logger.info(f"[时间校验] 共修正 {fixed_count} 处时间错误")

    return text
