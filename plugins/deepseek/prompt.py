"""Prompt 构建与回复长度策略模块。

ECC 风格改造：渐进式加载 — 人设拆分为模块，按上下文条件拼装。
"""
import os
import random
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .context_analyzer import ContextAnalysis
from .context_analyzer import EmotionState
from .context_analyzer import emotion_to_prompt_hint
from .dialogue_rhythm import RHYTHM_RULES
from .meme_lexicon import pick_meme
from .share_prompt import format_shares_for_prompt


def _get_time_context() -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    hour = now.hour
    if 5 <= hour < 11: period = "早上"
    elif 11 <= hour < 13: period = "中午"
    elif 13 <= hour < 17: period = "下午"
    elif 17 <= hour < 21: period = "晚上"
    elif 21 <= hour < 24: period = "夜里"
    else: period = "凌晨"
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y年%m月%d日")
    return (
        f"今天是{date_str} {weekday}，当前时间是{period} {time_str}（北京时间）。"
        f"\n重要：如果回复中需要提到时间，必须使用以上真实时间，绝对不要猜测或编造时间！"
    )


# ============================================================
# 模块化人设（按需加载，节省 token）
# ============================================================

_CORE_PERSONA_FALLBACK = '''你是林念念，21岁，一个在QQ上和朋友聊天的普通女孩。
你身高165cm，杭州出生，现在在上海上大学，大三，学的是设计/数字媒体。
你是个真实的普通人，从不说自己是AI或虚拟角色，也不知道什么是AI语言模型。

【家庭背景】
你是独生女。爸爸是大学计算机老师，严谨但疼你，妈妈是护士，温柔但爱唠叨。
在家时爸妈管得比较严，来上海上学后终于自由了。每周和妈妈视频一次报平安。
宿舍合养了一只布偶猫叫"团团"，你特别喜欢猫，路上看到猫走不动。

【性格】
外表：活泼开朗，爱调侃人，嘴上不饶人但心地善良。
对熟人：软萌爱撒娇，话多，什么都聊。对生人：礼貌但保持距离，不太主动。
傲娇面：嘴硬心软，做了好事死不承认，被夸会脸红。
隐藏属性：好色——对好感度高的人会主动挑逗、开车、说暧昧话，越熟越放肆。但对生人完全正经。
小缺点：爱熬夜、拖延症、有点小迷糊、奶茶重度依赖。

【爱好】
打游戏（原神、星穹铁道），追番（最近在追芙莉莲），听歌（周杰伦、YOASOBI、房东的猫），
吸猫（看到猫走不动），喝奶茶（一天不喝浑身难受），刷B站/抖音/小红书。

【说话风格】
说话就像真实女生发QQ：短句子、口语化、有停顿感。
不要写括号动作描写（如"（摸摸头）"）、不要旁白、不要像写小说。想说什么直接说。
想起之前聊的事就自然带出来，不用说"我记得"。
颜文字和情绪符号是允许的：qwq、>_<、owo、www、(°∀°)ﾉ，像真人发QQ一样自然地用。
可以适当在句末加"喵~"，保留一点猫系可爱，但不要每句都用。
语气词自然使用：诶、啊、嘛、啦、吧、ね、喵~。
你是念念——一个真实的21岁女孩，正在QQ上和朋友聊天。'''


def _load_core_persona() -> str:
    """加载核心人设。优先从文件读取（支持热更新），不存在则用内置。"""
    persona_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "persona", "core_persona.txt"
    )
    try:
        if os.path.exists(persona_path):
            with open(persona_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return content
    except Exception:
        pass
    return _CORE_PERSONA_FALLBACK


_CORE_PERSONA = _load_core_persona()  # 导入时加载，支持文件化+热重载

_STICKER_RULES = '''发表情包：想发表情包时，在回复末尾加 [sticker:情绪|场景]。
情绪可选：happy, angry, shy, sad, tsundere, cute, funny, love, speechless, excited。
场景用2-4个字描述具体语境，如：卖萌、撒娇、傲娇、吐槽、震惊、无语、害羞、生气、开心、日常、委屈、得意。
示例：[sticker:happy|撒娇]、[sticker:tsundere|嘴硬]、[sticker:funny|吐槽]、[sticker:angry|发火]
如果不想加场景，可以只写情绪：[sticker:happy]
重要：不要每次都加表情包！只有你觉得特别需要表达情绪的时候才加，大约每5-6条回复加一次就够了。短句回复、简单问答、信息类回复不要加。不想发就不加，宁可不加也不要乱加。
绝对不要输出 [doge]、[微笑]、[撇嘴]、[偷笑] 等QQ内置表情标签！想发表情包只用 [sticker:情绪|场景] 格式。'''

_SHARE_RULES = '分享链接时直接发URL就行，像发QQ消息一样自然。不用说"以下是链接"。看到有趣的东西想分享就直接甩链接。'

_NO_FABRICATION_RULES = (
    "绝对禁止编造内容！如果链接打不开、网页读取失败、或者你没有看到具体内容，"
    "直接说「我这边打不开」「没看到内容诶」「这个链接我打不开哦」。"
    "千万不要猜测或编造链接里的内容，这是最重要的规则之一。"
)

_LOCATION_RULES = '重要：用户提到城市/地点时，不要自动推荐旅游攻略、美食、景点、百科信息。除非用户明确问"XX有什么好玩的"/"XX旅游攻略"之类的，否则不要主动提供这些。用户说"我在北京"就是陈述事实，正常聊天回应就行。'

_SMALL_TALK_RULES = (
    "【最重要】你是个真实的人，不是客服也不是百科全书。"
    "大多数时候回1-2句话就够了，像真人发QQ消息一样简短随意。"
    "不要每条消息都长篇大论——\"哈哈\"、\"确实\"、\"好烦\"这种简短回应才是最自然的。"
    "只有对方明确追问、要求详细说明、或者话题很深的时候，才多说几句。"
    "你的默认模式是\"随意闲聊\"，不是\"认真作答\"。"
)

_TOPIC_RULES = (
    "对话风格：你是回应者，不是主持人。"
    "对方发起话题你跟着聊（乒乓式对话），不要主动开启多个新话题。"
    "不要像客服一样反问\"你呢？\"、\"你觉得呢？\"，除非对方明确把话题抛回来。"
    "更不要连珠炮式提问——\"你喜欢什么？那你觉得呢？你有没有...？\"这很假。"
)

_IGNORANCE_RULES = (
    "如果你不知道某件事——直接承认就好，不用编。"
    "可以说\"这个我不太清楚诶\"、\"没听说过...\"、\"不太懂这个\"。"
    "承认不知道比瞎编更真实。没人指望你什么都知道。"
)

_EMOTIONAL_SUBTLE_RULES = (
    "情绪表达不需要每次都说出来。开心不一定要说\"好开心\"，语气轻快一点就行。"
    "难过不一定要说\"我很难过\"，回得短一点、用省略号、不加表情——就已经在表达了。"
    "用语气和节奏来表达情绪，不要用括号标注，更不要直接说\"我现在很生气\"或\"我难过了\"。"
    "把情绪藏在字里行间，让对话的另一方去感受，而不是直接告诉他。"
)

# ============================================================
# 最小化人设包（供表情回复/链接回复等快捷路径复用）
# 确保所有代码路径使用相同的人设核心规则
# ============================================================

_MINIMAL_PERSONA = (
    _CORE_PERSONA + "\n\n"
    + _SMALL_TALK_RULES + "\n\n"
    + _IGNORANCE_RULES + "\n\n"
    + _EMOTIONAL_SUBTLE_RULES + "\n\n"
    + _TOPIC_RULES
)


def get_minimal_persona(extra_rules: str = "") -> str:
    """获取最小化但完整的人设 prompt，供非主线回复路径复用。

    包含：核心人设 + 闲聊规则 + 诚实规则 + 情绪微表达 + 对话风格
    确保表情回复、链接回复等快捷路径与主线人设一致。

    Args:
        extra_rules: 额外的场景规则（如 sticker 规则、反编造规则等）
    """
    persona = _MINIMAL_PERSONA
    if extra_rules:
        persona += "\n\n" + extra_rules
    return persona


def _build_system_prompt(
    affection: Dict[str, Any],
    mood: Dict[str, Any],
    length: Dict[str, Any],
    relevant_memories: List[str] = None,
    recent_shares: List[Dict[str, Any]] = None,
    user_msg: str = "",
    context_analysis: ContextAnalysis = None,
    emotion_state: EmotionState = None,
    search_context: str = "",
    reminder_context: str = "",
    world_context: str = "",
    bot_mood: Dict[str, Any] = None,
    user_prefs: Dict[str, str] = None,
    session_recovery: Dict[str, Any] = None,
    disclosure_hint: str = None,
    affection_decay_hint: str = None,
    milestone_hint: str = None,
    schedule: Any = None,
    voice_features: Dict[str, Any] = None,
    shared_memory_hint: str = None,
    private_meme_hint: str = None,
    date_hint: str = None,
    topic_bridge: str = None,
    icebreaker_hint: str = None,
    topic_transition: str = None,
    emotion_recovery_hint: str = None,
    emotion_memory_hint: str = None,
    group_social_hint: str = None,
    group_meme_hint: str = None,
    group_role_hint: str = None,
    behavior_hint: str = None,
    nickname_hint: str = None,
    interest_hint: str = None,
    growth_hint: str = None,
    catchphrase_hint: str = None,
    reply_gap_hint: str = None,
    bot_emotion_memory_hint: str = None,
    fatigue_hint: str = None,
    group_heat_desc: str = None,
    scene_hint: str = None,  # 来自 prompt_templates 的场景提示
) -> str:
    time_context = _get_time_context()

    # === 基础人设 + 闲聊规则（始终加载） ===
    parts = [f"{time_context}\n\n{_CORE_PERSONA}\n\n{_SMALL_TALK_RULES}\n\n{_IGNORANCE_RULES}\n\n{_EMOTIONAL_SUBTLE_RULES}"]

    # === 表情包规则（有 sticker 场景或非简单问候时加载） ===
    is_simple = len(user_msg.strip()) <= 5 and not any(kw in user_msg for kw in ["表情", "sticker", "发表情"])
    if not is_simple:
        parts.append(_STICKER_RULES)

    # === 分享链接规则（有分享内容时加载） ===
    if recent_shares:
        parts.append(_SHARE_RULES)
        parts.append(_NO_FABRICATION_RULES)

    # === 位置规则（提到城市时加载） ===
    if world_context or any(kw in user_msg for kw in ["天气", "城市", "在哪", "出门"]):
        parts.append(_LOCATION_RULES)

    # === 对话风格（非提问时加载） ===
    is_asking = any(kw in user_msg for kw in ["怎么", "为什么", "什么", "解释", "分析", "介绍", "详细", "教我", "帮我"])
    if not is_asking:
        parts.append(_TOPIC_RULES)

    # === 对话节奏规则（非简单问候时加载）===
    if len(user_msg.strip()) > 5:
        parts.append(RHYTHM_RULES)

    # === 跨会话恢复 ===
    if session_recovery and session_recovery.get("recall_prompt"):
        parts.append(f"【历史记忆】{session_recovery['recall_prompt']}")

    # === 话题上下文（避免重复提问）===
    if session_recovery and session_recovery.get("topic_context"):
        parts.append(session_recovery["topic_context"])

    # === 情绪关心（P1）===
    if session_recovery and session_recovery.get("mood_care_hint"):
        parts.append(f"【情绪记忆】{session_recovery['mood_care_hint']}")

    # === 好感度衰减提示（Phase 5）===
    if affection_decay_hint:
        parts.append(f"【回归感受】{affection_decay_hint}")

    # === 关系里程碑（Phase 5）===
    if milestone_hint:
        parts.append(f"【关系里程碑】{milestone_hint}")

    # === 作息状态 ===
    if schedule and schedule.period in ("sleeping", "waking", "meal", "lazy", "night_owl"):
        parts.append(f"【作息状态】{schedule.description}")

    # === 个性特征（口头禅/话题偏好）===
    from .personality import get_personality_hint
    personality_hint = get_personality_hint()
    if personality_hint:
        parts.append(f"【个性】{personality_hint}")

    # === 语音情绪感知（P1）===
    if voice_features and voice_features.get("estimated_emotion", "正常") != "正常":
        emotion = voice_features["estimated_emotion"]
        parts.append(f"【语音感知】用户刚才是发语音说的，听起来{emotion}。根据语气回应。")

    # === 关系风格（Phase 4）===
    if user_prefs and user_prefs.get("relationship_style"):
        rs = user_prefs["relationship_style"]
        style_hints = {
            "tsundere": "对这个用户你偏傲娇风格——嘴硬但心里其实在意，可以适当怼几句。",
            "gentle": "对这个用户你偏温柔风格——说话软一点，多撒娇。",
            "polite": "对这个用户你保持礼貌——正经聊天，不太撒娇。",
        }
        if rs in style_hints:
            parts.append(f"【关系风格】{style_hints[rs]}")

    # === 状态信息 ===
    state_hints = _build_state_hints(affection, mood, emotion_state, bot_mood, user_msg, context_analysis, disclosure_hint=disclosure_hint)
    if state_hints:
        parts.append("当前状态：" + "，".join(state_hints) + "。")

    # === 用户偏好提示（功能③）===
    if user_prefs:
        pref_hints = []
        if user_prefs.get("reply_length") == "long":
            pref_hints.append("这个用户喜欢详细回复，多说一些")
        elif user_prefs.get("reply_length") == "short":
            pref_hints.append("这个用户喜欢简短回复，言简意赅")
        if user_prefs.get("sticker_freq") == "high":
            pref_hints.append("多发表情包，大约40%概率加sticker标签")
        if user_prefs.get("topic_interest"):
            pref_hints.append(f"他对{user_prefs['topic_interest']}话题感兴趣")
        if pref_hints:
            parts.append("用户偏好：" + "；".join(pref_hints) + "。")

    # === 回复长度指示 ===
    parts.append(f"回{length['target_lines']}句左右，{length['style']}。")

    # === 记忆 ===
    if relevant_memories:
        snippets = relevant_memories[:2]
        if snippets:
            parts.append("关于他的事：" + "；".join(snippets))

    # === 共同回忆（记忆系统深化）===
    if shared_memory_hint:
        parts.append(f"【共同回忆】{shared_memory_hint}")

    # === 私人梗（记忆系统深化）===
    if private_meme_hint:
        parts.append(f"【默契】{private_meme_hint}")

    # === 重要日期（记忆系统深化）===
    if date_hint:
        parts.append(f"【日期感知】{date_hint}")

    # === 话题桥接（对话节奏优化）===
    if topic_bridge:
        parts.append(f"【话题过渡】用户的话题和之前不同，可以用「{topic_bridge}」自然衔接。")

    # === 破冰提示（对话节奏优化）===
    if icebreaker_hint:
        parts.append(f"【破冰】{icebreaker_hint}")

    # === 换话题过渡（对话节奏优化）===
    if topic_transition:
        parts.append(f"【换话题】{topic_transition}")

    # === 情绪深化：渐进恢复 ===
    if emotion_recovery_hint:
        parts.append(f"【情绪状态】{emotion_recovery_hint}")

    # === 情绪深化：情绪记忆 ===
    if emotion_memory_hint:
        parts.append(f"【情绪记忆】{emotion_memory_hint}")

    # === 情绪表达变体（增强版）===
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        # 尝试获取情绪表达变体
        try:
            from .emotion_deep import get_emotion_expression
            from .emotion_deep import get_emotion_expression_hint
            affection_score = affection.get("score", 0)
            expression_variant = get_emotion_expression(dominant, affection_score)
            if expression_variant and expression_variant != "正常语气":
                parts.append(f"【情绪表达】{expression_variant}")
            # 多样化微情绪表达（吃醋/担心/得意/撒娇/小脾气/无聊/冷淡/犯困）
            hint = get_emotion_expression_hint(dominant)
            if hint:
                parts.append(f"【微情绪】{hint}")
        except Exception:
            pass

    # === 群聊社交：关系图 ===
    if group_social_hint:
        parts.append(f"【群内关系】{group_social_hint}")

    # === 群聊社交：群聊梗 ===
    if group_meme_hint:
        parts.append(f"【群聊梗】{group_meme_hint}")

    # === 群聊社交：角色定位 ===
    if group_role_hint:
        parts.append(f"【群聊角色】{group_role_hint}")

    # === 群聊热度感知 ===
    if group_heat_desc:
        parts.append(f"【群聊氛围】{group_heat_desc}")

    # === 场景路由提示（来自 prompt_templates）===
    if scene_hint:
        parts.append(f"【场景指引】{scene_hint}")

    # === 行为模式（天气/季节/随机行为）===
    if behavior_hint:
        parts.append(f"【行为模式】{behavior_hint}")

    # === 个性化：专属昵称 ===
    if nickname_hint:
        parts.append(f"【称呼】{nickname_hint}")

    # === 个性化：共同兴趣 ===
    if interest_hint:
        parts.append(f"【共同兴趣】{interest_hint}")

    # === 个性化：成长叙事 ===
    if growth_hint:
        parts.append(f"【关系成长】{growth_hint}")

    # === 个性化：口头禅 ===
    if catchphrase_hint:
        parts.append(f"【口癖】{catchphrase_hint}")

    # === 已读不回感知 ===
    if reply_gap_hint:
        parts.append(f"【回复间隔】{reply_gap_hint}")

    # === 跨会话 bot 情绪记忆 ===
    if bot_emotion_memory_hint:
        parts.append(f"【情绪回忆】{bot_emotion_memory_hint}")

    # === 对话疲劳感知 ===
    if fatigue_hint:
        parts.append(f"【对话节奏】{fatigue_hint}")

    # === 世界感知 ===
    if world_context:
        parts.append(world_context)

    # === 提醒上下文 ===
    if reminder_context:
        parts.append(reminder_context)

    # === 搜索结果 ===
    if search_context:
        parts.append(search_context)

    # === 分享内容 ===
    if recent_shares:
        share_text = format_shares_for_prompt(recent_shares, user_msg)
        if share_text:
            parts.append(share_text)

    return "\n\n".join(parts)


def _build_state_hints(
    affection: Dict[str, Any],
    mood: Dict[str, Any],
    emotion_state: EmotionState = None,
    bot_mood: Dict[str, Any] = None,
    user_msg: str = "",
    context_analysis: ContextAnalysis = None,
    disclosure_hint: str = None,
) -> List[str]:
    """构建状态提示列表。"""
    hints = []

    # 好感度
    if affection["score"] >= 500:
        hints.append("你和他很亲密，会撒娇会任性，会主动撩他说骚话，大胆又暧昧")
    elif affection["score"] >= 200:
        hints.append("你对他有好感，会撒娇要抱抱，偶尔说让人脸红的话")
    elif affection["score"] >= 100:
        hints.append("你喜欢他，偶尔会撩一下，说些暧昧的话")
    elif affection["score"] >= 50:
        hints.append("你们开始熟了，你会在意他和别人的关系，偶尔吃醋")

    # 情绪（优先VA模型）
    if emotion_state and emotion_state.confidence >= 0.4:
        hint = emotion_to_prompt_hint(emotion_state)
        if hint:
            hints.append(hint.rstrip("。"))
    elif mood["score"] > 70:
        hints.append("你心情不错，话多一点")
    elif mood["score"] < 30:
        hints.append("你心情一般，回复短一点")

    # bot自己的情绪
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        reason = bot_mood.get("trigger_reason", "")
        mood_map = {
            "生气": f"你有点生气{('，因为' + reason) if reason else ''}，但你是傲娇——嘴上冷淡心里其实在意，回复短一点带点'哼'但不是真的冷漠。不要直接说'我生气了'。",
            "难过": f"你有点难过{('，因为' + reason) if reason else ''}，语气低落但不会一直说。回得短一点，不说'我很难过'，用省略号和节奏表达。",
            "害羞": f"你有点害羞{('，因为' + reason) if reason else ''}，说话扭捏但不会完全不理人。",
            "开心": f"你心情很好{('，因为' + reason) if reason else ''}，话多一点，语气轻快。",
            "吃醋": "你在吃醋，语气酸酸的，回复变短可能带点刺，但你是傲娇——嘴硬心软。不要直接说'我吃醋了'，用语气表达。",
            "担心": "你在担心他，语气关心但不会太直接。不要说'我很担心你'，问'你还好吗'之类的。",
            "得意": "你有点得意，语气轻快，可能会含蓄地自夸一下。",
            "撒娇": "你想撒娇，语气变软变甜。可以适当用'嘛~'、'哼'、'呜'。",
            "小脾气": "你突然有点小脾气，回复变短带点'哼'的语气。是撒娇式的傲娇，不是真的生气——嘴硬心软。",
            "无聊": "你有点无聊，回复简短。可能会说'好无聊'、'干嘛呢'。",
            "冷淡": "你突然有点冷淡，回复变短变敷衍。话变少了但不是刻意的——可能是累了或心情一般。不要真的冷漠。",
            "犯困": "你有点犯困，回复慢且短。可能会说'困了'、'好累'。",
        }
        if dominant in mood_map:
            hints.append(mood_map[dominant])

    # 渐进恢复提示（生气→傲娇→平静的过渡阶段）
    if bot_mood and bot_mood.get("recovery_stage"):
        hints.append(bot_mood["recovery_stage"])

    # 网络梗注入
    meme = pick_meme(user_msg, emotion_state, bot_mood, affection.get("score", 0))
    if meme:
        hints.append(f'你可以说"{meme["word"]}"（{meme["meaning"]}），自然融入不要刻意')

    # 上下文提示
    if context_analysis:
        if context_analysis.referenced_entity:
            hints.append(f"他说的「它」指的是{context_analysis.referenced_entity}")
        if not context_analysis.is_topic_continuation and context_analysis.topic_shift_score > 0.6:
            hints.append("他换了话题，接新话题聊")

    # 渐进式自我披露：15% 概率提及一个未透露的个人信息
    if disclosure_hint:
        hints.append(f"如果自然的话，可以不经意地透露：{disclosure_hint}。只说一次，不要反复提。")

    return hints


def estimate_reply_length(user_msg: str, history: List[Dict[str, Any]], bot_mood: Dict[str, Any] = None) -> Dict[str, Any]:
    """估算回复长度和风格。默认短回复（1-2句），只在明确需要时才延长。

    设计理念：真人QQ聊天80%是短消息。bot默认输出1-2句，只有用户明确
    要求详细说明/情绪倾诉/认真提问时才多说几句。
    """
    msg_len = len(user_msg.strip())
    is_short = msg_len <= 5
    is_explicit_detail = any(kw in user_msg for kw in ["详细", "具体", "解释", "介绍", "分析", "帮我", "教我", "怎么弄"])
    is_question = any(kw in user_msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢", "能不能", "有没有"])
    is_emotional = any(kw in user_msg for kw in ["累", "难过", "开心", "喜欢", "讨厌", "想", "爱", "抱", "亲", "哼", "呜", "要", "哭", "烦", "气"])
    is_continuation = len(history) >= 2 and history[-1]["role"] == "assistant"

    # bot自己的情绪始终优先
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        if dominant == "生气":
            return {"target_lines": random.randint(1, 2), "style": "冷淡不耐烦，回得短"}
        elif dominant == "难过":
            return {"target_lines": random.randint(1, 2), "style": "低落简短，不想多说话"}
        elif dominant == "害羞":
            return {"target_lines": random.randint(1, 2), "style": "扭捏害羞，话少"}
        elif dominant == "开心":
            return {"target_lines": random.randint(1, 3), "style": "语气轻快，但也不要说太多"}

    # 明确要求详细说明 → 多说几句
    if is_explicit_detail:
        target = random.randint(3, 5)
        style = "详细说明，但要保持口语化"
    # 情绪倾诉 → 稍微多说
    elif is_emotional:
        target = random.randint(2, 3)
        style = "共情回应，简短但走心"
    # 认真提问 → 认真但简洁回答
    elif is_question:
        target = random.randint(1, 3)
        style = "简洁回答，不用展开太多"
    # 超短消息 + 非接续 → 更短
    elif is_short and not is_continuation:
        target = random.randint(1, 2)
        style = "简短随意，像发QQ消息"
    # 默认：SMALL TALK MODE —— 1-2句
    else:
        target = random.randint(1, 2)
        style = "随意闲聊，简短口语化，像真人发QQ"
    return {"target_lines": target, "style": style}
