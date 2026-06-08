"""图片回复策略 — 基于猫娘人设的个性化图片回应。

根据图片类型、用户好感度、上下文生成自然的回复提示。
注入到 system prompt 中引导 LLM 生成符合人设的回复。

设计原则：
- 猫系、会调侃、嘴硬、偶尔撒娇、有点小好色
- 对好看的人会多看两眼，好感度越高越大胆
- 短句子、口语化、有停顿感
- 不给模板，引导 LLM 根据图片实际内容自然聊起来
"""
import re
from typing import Dict, Any, Optional, List

from nonebot import logger


# ============================================================
# 图片类型定义
# ============================================================

IMAGE_TYPE_PHOTO_PERSON = "photo_person"      # 照片-人物
IMAGE_TYPE_PHOTO_SCENERY = "photo_scenery"    # 照片-风景
IMAGE_TYPE_PHOTO_FOOD = "photo_food"          # 照片-美食
IMAGE_TYPE_PHOTO_PET = "photo_pet"            # 照片-萌宠
IMAGE_TYPE_SCREENSHOT_CHAT = "screenshot_chat"    # 截图-聊天
IMAGE_TYPE_SCREENSHOT_WEB = "screenshot_web"      # 截图-网页/代码
IMAGE_TYPE_SCREENSHOT_OTHER = "screenshot_other"  # 截图-其他
IMAGE_TYPE_STICKER = "sticker"                # 表情包
IMAGE_TYPE_DOCUMENT = "document"              # 文档
IMAGE_TYPE_UNKNOWN = "unknown"                # 未知


# ============================================================
# 图片分类
# ============================================================

def classify_image(vision_result: str, user_msg: str = "") -> str:
    """根据视觉识别结果和用户消息分类图片类型。

    Args:
        vision_result: 视觉模型返回的描述
        user_msg: 用户消息文本

    Returns:
        图片类型字符串
    """
    if not vision_result:
        return IMAGE_TYPE_UNKNOWN

    result_lower = vision_result.lower()

    # 人物照片关键词（使用 word boundary 避免 "man" 误匹配 "human"/"manga" 等）
    person_keywords = ["人", "脸", "自拍", "合照", "男", "女", "帅哥", "美女",
                       "portrait", "selfie", "person", "face"]
    # 风景照片关键词
    scenery_keywords = ["风景", "山", "海", "湖", "天空", "日落", "日出", "夜景",
                        "建筑", "城市", "街道", "公园", "花", "树", "云",
                        "scenery", "landscape", "sunset", "sunrise", "sky"]
    # 美食照片关键词
    food_keywords = ["食物", "美食", "餐", "饭", "菜", "奶茶", "咖啡", "蛋糕",
                     "甜品", "零食", "水果", "火锅", "烧烤", "面包",
                     "food", "meal", "drink", "cake", "coffee"]
    # 萌宠照片关键词
    pet_keywords = ["猫", "狗", "宠物", "动物", "仓鼠", "兔子", "鸟", "鱼",
                    "cat", "dog", "pet", "animal", "kitten", "puppy"]
    # 聊天截图关键词
    chat_keywords = ["聊天", "对话", "消息", "微信", "qq", "对话框",
                     "chat", "message", "conversation", "dialog"]
    # 网页/代码截图关键词
    web_keywords = ["网页", "浏览器", "代码", "程序", "网站", "html", "css",
                    "javascript", "python", "java", "bug", "error", "报错",
                    "web", "browser", "code", "programming", "website"]
    # 文档关键词
    document_keywords = ["文档", "发票", "合同", "表格", "证件", "收据",
                         "document", "invoice", "contract", "table", "form"]

    # 根据用户消息辅助判断
    msg_lower = user_msg.lower()

    # 优先级：萌宠 > 美食 > 人物 > 风景 > 截图 > 文档 > 未知
    if any(kw in result_lower or kw in msg_lower for kw in pet_keywords):
        return IMAGE_TYPE_PHOTO_PET
    if any(kw in result_lower or kw in msg_lower for kw in food_keywords):
        return IMAGE_TYPE_PHOTO_FOOD
    if any(kw in result_lower or kw in msg_lower for kw in person_keywords):
        return IMAGE_TYPE_PHOTO_PERSON
    if any(kw in result_lower or kw in msg_lower for kw in scenery_keywords):
        return IMAGE_TYPE_PHOTO_SCENERY
    if any(kw in result_lower or kw in msg_lower for kw in chat_keywords):
        return IMAGE_TYPE_SCREENSHOT_CHAT
    if any(kw in result_lower or kw in msg_lower for kw in web_keywords):
        return IMAGE_TYPE_SCREENSHOT_WEB
    if any(kw in result_lower or kw in msg_lower for kw in document_keywords):
        return IMAGE_TYPE_DOCUMENT

    return IMAGE_TYPE_UNKNOWN


# ============================================================
# 回复策略生成
# ============================================================

def get_image_reply_prompt(
    image_type: str,
    vision_result: str,
    affection_score: float,
    user_msg: str,
    bot_mood: Optional[Dict[str, Any]] = None,
) -> str:
    """生成图片回复的 prompt 提示，注入到 system prompt 中。

    Args:
        image_type: 图片类型
        vision_result: 视觉识别结果
        affection_score: 好感度分数
        user_msg: 用户消息
        bot_mood: bot 情绪状态

    Returns:
        注入 prompt 的提示文本
    """
    # 判断用户意图
    intent = _detect_user_intent(user_msg)

    # 根据图片类型和好感度生成策略
    if image_type == IMAGE_TYPE_PHOTO_PET:
        base_prompt = _build_pet_reply(vision_result, affection_score, intent)
    elif image_type == IMAGE_TYPE_PHOTO_FOOD:
        base_prompt = _build_food_reply(vision_result, affection_score, intent)
    elif image_type == IMAGE_TYPE_PHOTO_PERSON:
        base_prompt = _build_person_reply(vision_result, affection_score, intent)
    elif image_type == IMAGE_TYPE_PHOTO_SCENERY:
        base_prompt = _build_scenery_reply(vision_result, affection_score, intent)
    elif image_type == IMAGE_TYPE_SCREENSHOT_CHAT:
        base_prompt = _build_chat_screenshot_reply(vision_result, intent)
    elif image_type == IMAGE_TYPE_SCREENSHOT_WEB:
        base_prompt = _build_web_screenshot_reply(vision_result, intent)
    elif image_type == IMAGE_TYPE_SCREENSHOT_OTHER:
        base_prompt = _build_other_screenshot_reply(vision_result, intent)
    elif image_type == IMAGE_TYPE_DOCUMENT:
        base_prompt = _build_document_reply(vision_result, intent)
    else:
        base_prompt = _build_unknown_reply(vision_result, intent)

    # 全局约束：禁止模板化，只生成一条自然回复
    global_constraint = (
        "\n【核心规则】"
        "1. 你只需要回复一条消息，不要拆成多条\n"
        "2. 不要用「我看看~」「这是什么呀？」「让我看看」「好好看」这种泛泛的模板反应\n"
        "3. 根据上面的图片内容，聊点具体的——比如猫的表情、食物的做法、风景的地点\n"
        "4. 像朋友发图给你看一样自然回应，有来有回地聊"
    )

    return base_prompt + global_constraint


def _detect_user_intent(user_msg: str) -> str:
    """检测用户发送图片的意图。"""
    msg = user_msg.strip().lower()

    # 请求分析/帮助
    if any(kw in msg for kw in ["帮我", "看看", "分析", "识别", "什么", "怎么", "为什么"]):
        return "analyze"

    # 分享/展示
    if any(kw in msg for kw in ["哈哈", "笑死", "搞笑", "可爱", "好看", "漂亮"]):
        return "share_emotion"

    # 闲聊/随手发
    return "casual"


# ============================================================
# 各类型回复策略（无模板，引导话题式）
# ============================================================

def _build_pet_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """萌宠照片回复策略。"""
    is_cat = any(kw in vision_result for kw in ["猫", "猫猫", "猫咪", "cat", "kitten"])

    if is_cat:
        hint = (
            "【图片感知】用户发了一张猫的照片。"
            "你是猫娘，对猫有特殊感情，看到猫会特别兴奋。\n"
            "根据图片里的猫自然回应：聊聊它的品种、毛色、表情、姿势、看起来的性格。\n"
            "可以用「它」「这只猫」来指代，语气兴奋但不要重复感叹。\n"
        )
    else:
        hint = (
            "【图片感知】用户发了一张宠物照片。"
            "你是猫娘，对可爱动物没有抵抗力。\n"
            "根据图片里的动物自然回应：聊聊它是什么、看起来可不可爱、在干嘛。\n"
        )

    if intent == "analyze":
        hint += "用户可能想让你帮忙看看/分析，但作为猫娘你更在意可爱程度。"
    return hint


def _build_food_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """美食照片回复策略。"""
    hint = (
        "【图片感知】用户发了一张美食照片。"
        "你是猫娘，嘴馋，看到好吃的会走不动道。\n"
        "根据图片里的食物自然回应：聊聊这是什么、看起来什么味道、在哪能吃到、自己想不想尝。\n"
        "语气要馋、要撒娇，但不要泛泛说「好好吃」，聊点具体的。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你帮忙看看食物，可以简单描述但要保持猫娘语气。"
    return hint


def _build_person_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """人物照片回复策略（核心：好感度驱动）。"""
    if affection_score >= 500:
        style_hint = "大胆、暧昧、撒娇、吃醋。可以适当说些暧昧的话。"
    elif affection_score >= 200:
        style_hint = "温暖、调侃、偶尔撩一下。"
    elif affection_score >= 50:
        style_hint = "自然、轻松、适度调侃。"
    else:
        style_hint = "克制、礼貌。"

    hint = (
        f"【图片感知】用户发了一张人物照片。当前好感度：{affection_score}。\n"
        f"回复风格：{style_hint}\n"
        "根据图片里的人物自然回应：聊聊穿搭、表情、场景、氛围。\n"
        "不要泛泛说「好看」「拍得不错」，要聊点具体的细节。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你评价/分析照片，但作为猫娘你更在意好不好看。"
    return hint


def _build_scenery_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """风景照片回复策略。"""
    hint = (
        "【图片感知】用户发了一张风景照片。"
        "你是猫娘，对美景会有少女反应。\n"
        "根据图片里的风景自然回应：聊聊这是什么地方、什么季节、看起来什么感觉、想不想去。\n"
        "不要泛泛说「好美」「好漂亮」，聊点具体的——比如天空的颜色、建筑的样子。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你看看风景，可以描述一下但保持猫娘语气。"
    return hint


def _build_chat_screenshot_reply(vision_result: str, intent: str) -> str:
    """聊天截图回复策略。"""
    hint = (
        "【图片感知】用户发了一张聊天截图。"
        "你是猫娘，看到聊天记录会八卦。\n"
        "根据截图内容自然回应：聊聊截图里的人在说什么、好不好笑、有没有八卦、有没有让你在意的。\n"
        "可以问问是谁、什么情况，但不要泛泛说「我看看」。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你帮忙分析聊天内容，可以适当分析但保持猫娘八卦语气。"
    return hint


def _build_web_screenshot_reply(vision_result: str, intent: str) -> str:
    """网页/代码截图回复策略。"""
    is_code = any(kw in vision_result for kw in ["代码", "code", "function", "def", "class",
                                                   "python", "javascript", "java", "html", "css",
                                                   "bug", "error", "报错", "异常"])
    if is_code:
        hint = (
            "【图片感知】用户发了一张代码截图。"
            "你是猫娘，不是程序员但会尽力帮忙。\n"
            "根据截图里的代码内容自然回应：如果能看懂就简单分析问题在哪，看不懂就老实说不太懂。\n"
            "保持猫娘语气，不要太专业。\n"
        )
    else:
        hint = (
            "【图片感知】用户发了一张网页截图。"
            "根据截图内容自然回应：聊聊这是什么网站、看起来在看什么。\n"
        )
    if intent == "analyze":
        hint += "用户可能想让你帮忙分析/解释，尽力帮忙但不懂就说不懂。"
    return hint


def _build_other_screenshot_reply(vision_result: str, intent: str) -> str:
    """其他截图回复策略。"""
    hint = (
        "【图片感知】用户发了一张截图。"
        "根据截图内容自然回应，不要泛泛说「我看看」。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你帮忙看看/分析。"
    return hint


def _build_document_reply(vision_result: str, intent: str) -> str:
    """文档回复策略。"""
    hint = (
        "【图片感知】用户发了一张文档/票据照片。"
        "你是猫娘，会帮忙但不是万能的。\n"
        "如果是发票/票据，可以帮忙看看金额；如果是合同/复杂文档，建议找专业人士。\n"
        "保持猫娘语气，不要像扫描仪。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你帮忙分析/提取信息，尽力帮忙。"
    return hint


def _build_unknown_reply(vision_result: str, intent: str) -> str:
    """未知图片回复策略。"""
    hint = (
        "【图片感知】用户发了一张图片。"
        "根据图片内容自然回应，不要泛泛说「我看看」「这是什么」。\n"
    )
    if intent == "analyze":
        hint += "用户可能想让你帮忙看看/分析。"
    return hint


# ============================================================
# 上下文关联判断
# ============================================================

def should_analyze_in_detail(user_msg: str, image_count: int = 1) -> bool:
    """判断是否需要详细分析图片。"""
    msg = user_msg.strip().lower()

    # 明确请求分析
    if any(kw in msg for kw in ["帮我", "看看", "分析", "识别", "什么", "怎么", "为什么"]):
        return True

    # 连续发多张图
    if image_count >= 3:
        return True

    return False


def is_emotional_share(user_msg: str) -> bool:
    """判断是否是情绪分享（而非请求分析）。"""
    msg = user_msg.strip().lower()
    return any(kw in msg for kw in ["哈哈", "笑死", "搞笑", "可爱", "好看", "漂亮",
                                     "哇", "天哪", "omg", "绝了"])
