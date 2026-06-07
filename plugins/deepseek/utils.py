"""通用工具函数。"""
import re
import random
from typing import List, Dict, Any, Tuple


def split_long_reply(text: str) -> List[str]:
    """按语义分句拆分回复，每句独立为一条消息。

    规则：
    1. 按换行拆行，每行独立一条消息
    2. 过短的行（< 5字）合并到上一条，避免碎片
    3. 超长行（> 80字）按句号/问号/感叹号再拆
    4. 清理尾部空格
    """
    if not text or not text.strip():
        return [text.strip()]

    # 第一步：按换行拆行，清理空行和尾部空格
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    if not lines:
        return [text.strip()]

    # 第二步：合并过短的行（< 5字）到上一条
    merged = []
    for line in lines:
        if merged and len(line) < 5:
            merged[-1] += line
        else:
            merged.append(line)

    # 第三步：超长行按句号/问号/感叹号再拆
    result = []
    for line in merged:
        if len(line) <= 80:
            result.append(line)
        else:
            parts = re.split(r'([。？！!?])', line)
            temp = ""
            for part in parts:
                if not part:
                    continue
                if len(temp) + len(part) > 80 and temp:
                    result.append(temp.strip())
                    temp = part
                else:
                    temp += part
            if temp and temp.strip():
                result.append(temp.strip())

    return result if result else [text.strip()]


def calc_message_delay(text: str, context: dict = None) -> float:
    """真人化延迟：模拟"看到消息→想回复→打字"的全过程。

    核心思路：延迟取决于对方说了什么（阅读量），而非自己回了什么。
    短消息如"嗯"反而要更久（因为要先看完对方说的），
    长消息反而可能更快（因为对方说了很多你有话要说）。

    context 可选字段:
      - user_msg: str (对方发的消息，用于计算阅读时间)
      - complexity: str (simple/normal/complex)
      - emotion_arousal: float (唤醒度 0~1, 低=慵懒)
      - is_question: bool (对方在提问)
      - is_first_reply: bool (首条回复)
      - schedule_speed: float (作息速度系数)
      - is_quick_reply: bool (简单消息快速通道)
      - is_night: bool (深夜模式)
    """
    reply_len = len(text)
    ctx = context or {}

    # === 1. 阅读时间：取决于对方消息长度 ===
    user_msg_len = len(ctx.get("user_msg", ""))
    if user_msg_len <= 3:
        read_time = random.uniform(0.5, 1.5)     # "嗯" → 快速扫一眼
    elif user_msg_len <= 15:
        read_time = random.uniform(1.0, 3.0)     # 一句话 → 正常看
    elif user_msg_len <= 50:
        read_time = random.uniform(2.0, 4.5)     # 一段话 → 仔细看
    else:
        read_time = random.uniform(3.0, 6.0)     # 长消息 → 认真看，最多6秒

    # 首条回复需要阅读，非首条（连发的后续）跳过阅读
    if not ctx.get("is_first_reply", True):
        read_time = 0.0

    # === 2. 思考时间：取决于消息复杂度 ===
    complexity = ctx.get("complexity", "normal")
    if complexity == "simple":
        # "哈哈"、"好的" → 不用想，直接回
        think_time = random.uniform(0.3, 1.5)
    elif complexity == "complex":
        # 提问、分析、需要搜索 → 要想一下
        think_time = random.uniform(2.0, 6.0)
    else:
        # 一般消息 → 正常想
        think_time = random.uniform(1.0, 3.0)

    # 问题需要额外思考
    if ctx.get("is_question"):
        think_time += random.uniform(1.0, 3.0)

    # === 3. 打字时间：基于自己回复长度 ===
    if reply_len <= 5:
        type_time = random.uniform(0.3, 0.8)     # "嗯" → 打得快
    elif reply_len <= 15:
        type_time = random.uniform(0.8, 1.5)
    elif reply_len <= 40:
        type_time = random.uniform(1.5, 3.0)
    else:
        type_time = random.uniform(2.5, 4.0) + (reply_len - 40) * random.uniform(0.03, 0.08)
        type_time = min(type_time, 8.0)

    # === 4. 合并 + 修正 ===
    total = read_time + think_time + type_time

    # 作息速度系数
    schedule_speed = ctx.get("schedule_speed", 1.0)
    total *= schedule_speed

    # 情绪修正：兴奋时手快脑子快，低落时慢悠悠
    arousal = ctx.get("emotion_arousal", 0.5)
    if arousal > 0.7:
        total *= random.uniform(0.6, 0.85)   # 兴奋 → 快
    elif arousal < 0.3:
        total *= random.uniform(1.2, 1.6)    # 低落 → 慢

    # 深夜模式：整体慢一拍
    if ctx.get("is_night"):
        total *= random.uniform(1.3, 1.8)

    # 快回模式：简单消息通道，大幅压缩
    if ctx.get("is_quick_reply"):
        total *= 0.4

    # 随机抖动：真人不是匀速的，±15%
    jitter = random.gauss(0, total * 0.15)
    total += jitter

    # 边界：最少1.5秒（不可能比这更快），最多30秒
    return max(1.5, min(total, 30.0))


def calc_burst_delays(parts: List[str], base_context: dict = None) -> List[float]:
    """计算连发消息的延迟列表。

    真人连发消息的节奏：
    - 第一条：正常延迟（阅读+思考+打字）
    - 第二条：等 2~5 秒（打完一条想起来又补一条）
    - 第三条：等 1~3 秒（越说越快，抢着说）
    """
    if not parts:
        return []

    delays = []
    for i, part in enumerate(parts):
        if i == 0:
            # 第一条：正常延迟
            ctx = dict(base_context or {})
            delays.append(calc_message_delay(part, ctx))
        else:
            # 追加消息：固定范围随机，模拟"打完上条又想到要补"
            delays.append(random.uniform(2.0, 5.0))

    return delays




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
    强制过滤所有括号内容 + QQ内置表情标签。
    模型只会用括号写动作描写，所以直接删除所有（）和()内的内容。
    同时过滤 [doge]、[微笑] 等QQ内置表情标签，避免泄露到回复中。
    """
    if not text:
        return text

    # 删除QQ内置表情标签（[doge]、[微笑]、[撇嘴] 等）
    text = re.sub(
        r'\[(?:doge|微笑|撇嘴|色|发呆|得意|流泪|害羞|闭嘴|睡|大哭|尴尬|发怒|'
        r'调皮|呲牙|惊讶|难过|酷|冷汗|抓狂|吐|偷笑|愉快|白眼|傲慢|饥饿|困|'
        r'惊恐|流汗|憨笑|悠闲|奋斗|咒骂|疑问|嘘|晕|疯了|衰|骷髅|敲打|再见|'
        r'擦汗|抠鼻|鼓掌|糗大了|坏笑|左哼哼|右哼哼|哈欠|鄙视|委屈|快哭了|'
        r'阴险|亲亲|吓|可怜|菜刀|西瓜|啤酒|篮球|乒乓|咖啡|饭|猪头|玫瑰|'
        r'凋谢|嘴唇|爱心|蛋糕|闪电|炸弹|刀|足球|瓢虫|便便|月亮|太阳|礼物|'
        r'拥抱|强|弱|握手|胜利|抱拳|勾引|拳头|差劲|爱你|NO|OK|爱情|飞吻|'
        r'跳跳|发抖|怄火|转圈|磕头|回头|跳绳|挥手|激动|街舞|献吻|左太极|'
        r'右太极|双喜|鞭炮|灯笼|K歌|喝彩|祈祷|爆筋|棒棒糖|喝奶|下面|香蕉|'
        r'飞机|开车|高铁|左车头|车厢|右车头|多云|下雨|钞票|熊猫|灯泡|风车|'
        r'闹钟|打伞|气球|庆生|糖果|蜡烛|烟花)\]',
        '', text
    )

    # 删除所有中文括号内容
    text = re.sub(r'（[^（）]*?）', '', text)
    # 删除所有英文括号内容（但保留 [sticker:xxx] 标签）
    text = re.sub(r'(?<!\[sticker)\([^()]*?\)', '', text)

    # 清理连续的空格和多余换行
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'^[\s，。]+', '', text)

    return text.strip()
