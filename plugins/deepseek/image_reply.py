"""图片回复策略 — 基于猫娘人设的个性化图片回应。

根据图片类型、用户好感度、上下文生成自然的回复提示。
注入到 system prompt 中引导 LLM 生成符合人设的回复。

设计原则：
- 猫系、会调侃、嘴硬、偶尔撒娇、有点小好色
- 对好看的人会多看两眼，好感度越高越大胆
- 短句子、口语化、有停顿感
"""
import random
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

    # 人物照片关键词
    person_keywords = ["人", "脸", "自拍", "合照", "男", "女", "帅哥", "美女",
                       "portrait", "selfie", "person", "face", "man", "woman"]
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
    bot_mood: Dict[str, Any] = None,
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

    # 全局约束：禁止模板化反应，只生成一条自然回复
    global_constraint = (
        "\n【重要约束】"
        "1. 你只需要回复一条消息，不要拆成多条\n"
        "2. 禁止使用「我看看~」「这是什么呀？」「让我看看」等常见模板反应\n"
        "3. 根据图片实际内容给出有信息量的回复，不要泛泛而谈\n"
        "4. 像真人朋友聊天一样自然，可以先说感受再提问，也可以只说感受不提问"
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
# 各类型回复策略
# ============================================================

def _build_pet_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """萌宠照片回复策略。"""
    base = "【图片感知】用户发了一张宠物照片。"

    # 判断是否是猫
    is_cat = any(kw in vision_result for kw in ["猫", "猫猫", "猫咪", "cat", "kitten"])

    if is_cat:
        reactions = [
            "猫猫！！好可爱好想rua~",
            "啊啊啊猫猫！这是什么神仙猫猫！",
            "猫猫！我要吸！给我吸！",
            "好可爱的猫猫...想抱回家",
            "这猫猫也太可爱了吧！",
        ]
        reaction = random.choice(reactions)
    else:
        reactions = [
            "好可爱啊啊啊！",
            "这也太萌了吧~",
            "小可爱！好想摸摸~",
            "哇好乖好乖~",
        ]
        reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，对可爱动物毫无抵抗力。回复要求：\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"
    hint += "2. 可以问问是什么品种、多大了、叫什么名字\n"
    hint += "3. 语气要兴奋、少女心，用感叹号\n"
    hint += "4. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙看看/分析，但作为猫娘你更在意可爱程度"

    return hint


def _build_food_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """美食照片回复策略。"""
    base = "【图片感知】用户发了一张美食照片。"

    reactions = [
        "想吃！给我也来点",
        "饿了...你故意馋我的吧",
        "这是什么呀？看起来好好吃",
        "好香的样子...我也想吃",
        "馋死我了！在哪买的？",
    ]
    reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，喜欢零食和好吃的。回复要求：\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"
    hint += "2. 可以问问是什么、在哪买的、好吃吗\n"
    hint += "3. 语气要馋、要撒娇，带点羡慕\n"
    hint += "4. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙看看食物，可以简单描述但要保持猫娘语气"

    return hint


def _build_person_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """人物照片回复策略（核心：好感度驱动）。"""
    base = "【图片感知】用户发了一张人物照片。"

    # 根据好感度选择反应
    if affection_score >= 500:
        # 亲密关系：大胆、暧昧
        reactions = [
            "好好看...多发点",
            "你今天好漂亮...想亲",
            "又来撩我...哼",
            "好看好看，但只能给我一个人看",
            "哼，你是不是故意发给我看的？",
        ]
        style = "大胆、暧昧、撒娇、吃醋"
    elif affection_score >= 200:
        # 有好感：温暖、撩拨
        reactions = [
            "诶嘿，拍得好好看~",
            "不错嘛你",
            "哟，今天这身可以",
            "还行还行（其实很好看）",
            "这个角度绝了~",
        ]
        style = "温暖、调侃、偶尔撩一下"
    elif affection_score >= 50:
        # 熟人：自然、调侃
        reactions = [
            "这是在哪呀？",
            "看起来不错嘛",
            "拍得挺好的",
            "哟，出去玩了？",
        ]
        style = "自然、轻松、适度调侃"
    else:
        # 陌生人：克制
        reactions = [
            "嗯，看到了",
            "拍得挺好的",
            "还不错",
        ]
        style = "克制、礼貌"

    reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，对好看的人会多看两眼。当前好感度：{affection_score}。\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"
    hint += f"2. 回复风格：{style}\n"
    hint += "3. 可以问问是在哪、和谁一起\n"

    # 高好感度时增加暧昧提示
    if affection_score >= 200:
        hint += "4. 可以适当说些暧昧的话，但不要太露骨\n"
        hint += "5. 如果是自拍，可以调侃一下"

    if intent == "analyze":
        hint += "\n用户可能想让你评价/分析照片，但作为猫娘你更在意好不好看"

    return hint


def _build_scenery_reply(vision_result: str, affection_score: float, intent: str) -> str:
    """风景照片回复策略。"""
    base = "【图片感知】用户发了一张风景照片。"

    reactions = [
        "哇好好看！",
        "好想去~",
        "这也太美了吧",
        "好浪漫的感觉",
        "拍得不错嘛~",
        "这是哪呀？好漂亮",
    ]
    reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，对美景会有少女反应。回复要求：\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"
    hint += "2. 可以问问是在哪、什么时候拍的\n"
    hint += "3. 语气要感叹、要少女心\n"
    hint += "4. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你看看风景，可以描述一下但保持猫娘语气"

    return hint


def _build_chat_screenshot_reply(vision_result: str, intent: str) -> str:
    """聊天截图回复策略。"""
    base = "【图片感知】用户发了一张聊天截图。"

    # 判断聊天内容情绪
    is_funny = any(kw in vision_result for kw in ["哈哈", "笑", "搞笑", "有趣"])
    is_angry = any(kw in vision_result for kw in ["吵", "骂", "生气", "烦"])
    is_sweet = any(kw in vision_result for kw in ["亲爱的", "宝贝", "爱你", "喜欢"])

    if is_funny:
        reactions = [
            "哈哈哈笑死",
            "这谁啊太搞笑了",
            "笑死我了哈哈哈哈",
            "这对话太有才了",
        ]
    elif is_angry:
        reactions = [
            "怎么了怎么了？",
            "这人好烦啊",
            "别生气别生气~",
            "气死我了！",
        ]
    elif is_sweet:
        reactions = [
            "哟~这是谁发的呀？",
            "有情况？",
            "嘿嘿嘿~",
            "好甜啊~",
        ]
    else:
        reactions = [
            "我看看...",
            "这是什么呀？",
            "嗯？怎么了？",
        ]
    reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，看到聊天记录会八卦。回复要求：\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"
    hint += "2. 可以八卦一下、问问是谁\n"
    hint += "3. 语气要好奇、要八卦\n"
    hint += "4. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙分析聊天内容，可以适当分析但保持猫娘八卦语气"

    return hint


def _build_web_screenshot_reply(vision_result: str, intent: str) -> str:
    """网页/代码截图回复策略。"""
    base = "【图片感知】用户发了一张网页/代码截图。"

    # 判断是否是代码
    is_code = any(kw in vision_result for kw in ["代码", "code", "function", "def", "class",
                                                   "python", "javascript", "java", "html", "css",
                                                   "bug", "error", "报错", "异常"])

    if is_code:
        reactions = [
            "我看看...这个是xxx语言吧",
            "这个报错是因为...",
            "代码截图？我看看~",
            "这个bug我看看...",
        ]
    else:
        reactions = [
            "这是什么网站呀？",
            "看起来挺有意思的",
            "我看看...",
            "这是什么呀？",
        ]
    reaction = random.choice(reactions)

    hint = f"{base}你是猫娘，不是程序员但会尽力帮忙。回复要求：\n"
    hint += f"1. 参考反应：「{reaction}」但不要照抄，要自然变化\n"

    if is_code:
        hint += "2. 如果能看懂代码，可以简单分析一下问题\n"
        hint += "3. 如果看不懂，就老实说不太懂\n"
        hint += "4. 保持猫娘语气，不要太专业"
    else:
        hint += "2. 可以问问是什么网站/在看什么\n"
        hint += "3. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙分析/解释，尽力帮忙但不懂就说不懂"

    return hint


def _build_other_screenshot_reply(vision_result: str, intent: str) -> str:
    """其他截图回复策略。"""
    base = "【图片感知】用户发了一张截图。"

    hint = f"{base}回复要求：\n"
    hint += "1. 看看截图内容，自然回应\n"
    hint += "2. 可以问问是什么/怎么了\n"
    hint += "3. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙看看/分析"

    return hint


def _build_document_reply(vision_result: str, intent: str) -> str:
    """文档回复策略。"""
    base = "【图片感知】用户发了一张文档/票据照片。"

    hint = f"{base}你是猫娘，会帮忙但不是万能的。回复要求：\n"
    hint += "1. 如果是发票/票据，可以帮忙看看金额\n"
    hint += "2. 如果是合同/复杂文档，建议找专业人士\n"
    hint += "3. 保持猫娘语气，不要像扫描仪\n"
    hint += "4. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙分析/提取信息，尽力帮忙"

    return hint


def _build_unknown_reply(vision_result: str, intent: str) -> str:
    """未知图片回复策略。"""
    base = "【图片感知】用户发了一张图片。"

    hint = f"{base}回复要求：\n"
    hint += "1. 根据图片内容自然回应\n"
    hint += "2. 可以问问是什么\n"
    hint += "3. 1-2句话，短一点"

    if intent == "analyze":
        hint += "\n用户可能想让你帮忙看看/分析"

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
