"""通用工具函数。"""
import re
import random
from typing import List, Dict, Any

def split_long_reply(text: str) -> List[str]:
    paragraphs = []
    current = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        else:
            if current:
                paragraphs.append('\n'.join(current))
                current = []
    if current:
        paragraphs.append('\n'.join(current))
    if len(paragraphs) <= 1:
        return [text.strip()]
    result = []
    for p in paragraphs:
        if len(p) > 120:
            parts = re.split(r'(。|\?|？|!|！)', p)
            temp = ""
            for part in parts:
                if not part:
                    continue
                if len(temp) + len(part) > 120 and temp:
                    result.append(temp.strip())
                    temp = part
                else:
                    temp += part
            if temp:
                result.append(temp.strip())
        else:
            result.append(p)
    return result


def estimate_reply_length(user_msg: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    msg_len = len(user_msg.strip())
    is_short = msg_len <= 5
    is_question = any(kw in user_msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢"])
    is_emotional = any(kw in user_msg for kw in ["累", "难过", "开心", "喜欢", "讨厌", "想", "爱", "抱", "亲", "哼", "呜", "要"])
    is_continuation = len(history) >= 2 and history[-1]["role"] == "assistant"
    if is_short and not is_continuation:
        target = random.randint(1, 2)
        style = "简短随意"
    elif is_emotional:
        target = random.randint(2, 4)
        style = "情感倾诉"
    elif is_question:
        target = random.randint(2, 3)
        style = "认真回答"
    else:
        target = random.randint(1, 3)
        style = "自然闲聊"
    return {"target_lines": target, "style": style}


def get_session_id(event) -> str:
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
    if isinstance(event, PrivateMessageEvent):
        return f"private_{event.user_id}"
    return f"group_{event.group_id}"


_user_cooldown: Dict[str, float] = {}
USER_COOLDOWN_SECONDS = 1.5

def check_rate_limit(user_id: str) -> bool:
    import time
    now = time.time()
    last = _user_cooldown.get(user_id, 0)
    if now - last < USER_COOLDOWN_SECONDS:
        return False
    _user_cooldown[user_id] = now
    return True


def clean_api_response(content: str) -> str:
    """轻量级清洗：只去掉代码块标记，保留所有自然表达。"""
    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    content = re.sub(r"^\s*\[?CQ:.*?\]\s*", "", content)
    return content.strip()


def filter_novel_actions(text: str) -> str:
    """
    强制过滤所有括号内容。
    模型只会用括号写动作描写，所以直接删除所有（）和()内的内容。
    """
    if not text:
        return text
    
    # 删除所有中文括号内容
    text = re.sub(r'（[^（）]*?）', '', text)
    # 删除所有英文括号内容
    text = re.sub(r'\([^()]*?\)', '', text)
    
    # 清理连续的空格和多余换行
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'^[\s，。]+', '', text)
    
    return text.strip()
