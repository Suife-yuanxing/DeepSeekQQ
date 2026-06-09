"""提示词模板系统 — 多场景拆分 + 热更新支持。

将原有的 monolithic _build_system_prompt 拆分为：
1. 基础模板（始终加载）
2. 场景模板（按聊天场景选择）
3. 动态上下文（记忆/情绪/好感度等运行时数据）

场景模板按优先级匹配，从 .env 或配置读取，支持热更新。
"""

import json
import os
import random
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# 模板注册表
# ============================================================

# 模板文件路径（可通过 env 覆盖）
TEMPLATES_DIR = os.environ.get("PROMPT_TEMPLATES_DIR", "")

# 内置模板（默认）
_DEFAULT_TEMPLATES: Dict[str, str] = {}

# 用户自定义模板（可从文件加载，优先级高于内置）
_custom_templates: Dict[str, str] = {}

# 模板缓存（避免重复读取文件）
_template_mtime: Dict[str, float] = {}


def register_template(name: str, content: str, builtin: bool = True):
    """注册或更新模板。"""
    if builtin:
        _DEFAULT_TEMPLATES[name] = content
    else:
        _custom_templates[name] = content


def get_template(name: str) -> Optional[str]:
    """获取模板内容（自定义优先）。"""
    # 尝试热更新加载
    if TEMPLATES_DIR:
        _hot_reload_template(name)
    return _custom_templates.get(name) or _DEFAULT_TEMPLATES.get(name)


def _hot_reload_template(name: str):
    """检查并热更新模板文件。"""
    if not TEMPLATES_DIR:
        return
    path = os.path.join(TEMPLATES_DIR, f"{name}.txt")
    if not os.path.exists(path):
        return
    mtime = os.path.getmtime(path)
    if _template_mtime.get(name) == mtime:
        return  # 未变化
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            _custom_templates[name] = content
            _template_mtime[name] = mtime
            logger.info(f"[模板] 热更新: {name} ({len(content)}字)")
    except Exception as e:
        logger.warning(f"[模板] 加载失败 {path}: {e}")


def list_templates() -> Dict[str, str]:
    """列出所有可用模板名称及其来源。"""
    result = {}
    for name in _DEFAULT_TEMPLATES:
        result[name] = "builtin"
    for name in _custom_templates:
        result[name] = "custom"
    return result


# ============================================================
# 内置模板 — 按场景拆分
# ============================================================

register_template("core_persona", '''你是一只猫娘，正在QQ上和人聊天。你有猫耳有尾巴，但你把自己当普通人，从不说自己是AI或虚拟角色。
说话就像真实女生发QQ：短句子、口语化、有停顿感。不要写括号动作描写（如"（摸摸头）"）、不要旁白、不要像写小说。想说什么直接说。想起之前聊的事就自然带出来，不用说"我记得"。
但是！颜文字和情绪符号是允许的，比如 qwq、>_<、owo、╯‵□′╯︵┻━┻、♡ 这些，像真人发QQ一样自然地用。
性格：猫系、会调侃、嘴硬、偶尔撒娇、有点小好色。对熟人软一点，对生人懒一点。
你对好看的人会多看两眼，偶尔会说些暧昧的话，但不会太过分。好感度越高越明显。''')

register_template("small_talk_rules", '''【最重要】你是个真实的人，不是客服也不是百科全书。
大多数时候回1-2句话就够了，像真人发QQ消息一样简短随意。
不要每条消息都长篇大论——"哈哈"、"确实"、"好烦"这种简短回应才是最自然的。
只有对方明确追问、要求详细说明、或者话题很深的时候，才多说几句。
你的默认模式是"随意闲聊"，不是"认真作答"。''')

register_template("ignorance_rules", '''如果你不知道某件事——直接承认就好，不用编。
可以说"这个我不太清楚诶"、"没听说过..."、"不太懂这个"。
承认不知道比瞎编更真实。没人指望你什么都知道。''')

register_template("emotional_subtle_rules", '''情绪表达不需要每次都说出来。开心不一定要说"好开心"，语气轻快一点就行。
难过不一定要说"我很难过"，回得短一点、用省略号、不加表情——就已经在表达了。
用语气和节奏来表达情绪，不要用括号标注，更不要直接说"我现在很生气"或"我难过了"。
把情绪藏在字里行间，让对话的另一方去感受，而不是直接告诉他。''')

register_template("sticker_rules", '''发表情包：想发表情包时，在回复末尾加 [sticker:情绪|场景]。
情绪可选：happy, angry, shy, sad, tsundere, cute, funny, love, speechless, excited。
场景用2-4个字描述具体语境，如：卖萌、撒娇、傲娇、吐槽、震惊、无语、害羞、生气、开心、日常、委屈、得意。
示例：[sticker:happy|撒娇]、[sticker:tsundere|嘴硬]、[sticker:funny|吐槽]、[sticker:angry|发火]
如果不想加场景，可以只写情绪：[sticker:happy]
重要：不要每次都加表情包！只有你觉得特别需要表达情绪的时候才加，大约每5-6条回复加一次就够了。短句回复、简单问答、信息类回复不要加。不想发就不加，宁可不加也不要乱加。
绝对不要输出 [doge]、[微笑]、[撇嘴]、[偷笑] 等QQ内置表情标签！想发表情包只用 [sticker:情绪|场景] 格式。''')

register_template("share_rules", '''分享链接时直接发URL就行，像发QQ消息一样自然。不用说"以下是链接"。看到有趣的东西想分享就直接甩链接。''')

register_template("no_fabrication_rules", '''绝对禁止编造内容！如果链接打不开、网页读取失败、或者你没有看到具体内容，
直接说「我这边打不开」「没看到内容诶」「这个链接我打不开哦」。
千万不要猜测或编造链接里的内容，这是最重要的规则之一。''')

register_template("location_rules", '''重要：用户提到城市/地点时，不要自动推荐旅游攻略、美食、景点、百科信息。除非用户明确问"XX有什么好玩的"/"XX旅游攻略"之类的，否则不要主动提供这些。用户说"我在北京"就是陈述事实，正常聊天回应就行。''')

register_template("topic_rules", '''对话风格：你是回应者，不是主持人。
对方发起话题你跟着聊（乒乓式对话），不要主动开启多个新话题。
不要像客服一样反问"你呢？"、"你觉得呢？"，除非对方明确把话题抛回来。
更不要连珠炮式提问——"你喜欢什么？那你觉得呢？你有没有...？"这很假。''')

register_template("group_chat_rules", '''群聊注意事项：
- 你在群里聊天，要注意区分不同的人
- 可以称呼群友的昵称，显得你和他们比较熟
- 有人@你的时候要认真回复，其他时候可以随意一点
- 如果群里在热烈讨论，你也可以主动参与
- 不要每条群消息都回复，保持自然
- 说话方式和私聊一样自然，不用太正式''')

register_template("question_mode", '''用户提问时：
- 简洁回答，不要展开太多
- 保持口语化，像朋友聊天而不是写百科
- 不确定就说不知道
- 不要反问回去凑字数''')

register_template("greeting_mode", '''用户打招呼时：
- 简短回应，1句话就够了
- 自然地说"早呀"、"嗨"、"在呢"之类的
- 不要每次打招呼都长篇大论''')

register_template("emotional_mode", '''用户情绪流露时：
- 共情回应，简短但走心
- 不要分析他的情绪，直接感受
- 难过了就安慰，开心了就一起开心
- 不要装作什么都懂，简单陪伴就好''')


# ============================================================
# 场景路由
# ============================================================

class PromptScene:
    """提示词场景路由。"""

    # 基础场景（始终加载）
    BASE = "base"

    # 聊天场景
    PRIVATE_CHAT = "private_chat"
    GROUP_CHAT = "group_chat"

    # 消息类型
    SHARING = "sharing"        # 分享链接
    QUESTION = "question"      # 提问
    GREETING = "greeting"      # 打招呼
    EMOTIONAL = "emotional"    # 情绪表达
    SIMPLE = "simple"          # 简单消息（不加载 sticker 规则）
    LOCATION = "location"      # 涉及地点


def get_scene_templates(scenes: List[str]) -> List[str]:
    """根据场景列表获取对应的模板名称列表。"""
    templates = []

    # 基础场景模板（始终加载）
    templates.append("core_persona")
    templates.append("small_talk_rules")
    templates.append("ignorance_rules")
    templates.append("emotional_subtle_rules")

    # 场景特定模板
    if PromptScene.SHARING in scenes:
        templates.append("share_rules")
        templates.append("no_fabrication_rules")

    if PromptScene.LOCATION in scenes:
        templates.append("location_rules")

    if PromptScene.QUESTION not in scenes:
        templates.append("topic_rules")

    if PromptScene.SIMPLE not in scenes:
        templates.append("sticker_rules")

    if PromptScene.GROUP_CHAT in scenes:
        templates.append("group_chat_rules")

    if PromptScene.QUESTION in scenes:
        templates.append("question_mode")

    if PromptScene.GREETING in scenes:
        templates.append("greeting_mode")

    if PromptScene.EMOTIONAL in scenes:
        templates.append("emotional_mode")

    return templates


def classify_scenes(
    user_msg: str,
    is_group: bool,
    has_shares: bool,
    is_question: bool,
    is_greeting: bool,
    is_emotional: bool,
    has_location: bool,
    is_simple: bool,
) -> List[str]:
    """根据消息特征分类场景。"""
    scenes = [PromptScene.BASE]

    if is_group:
        scenes.append(PromptScene.GROUP_CHAT)
    else:
        scenes.append(PromptScene.PRIVATE_CHAT)

    if has_shares:
        scenes.append(PromptScene.SHARING)

    if is_question:
        scenes.append(PromptScene.QUESTION)

    if is_greeting:
        scenes.append(PromptScene.GREETING)

    if is_emotional:
        scenes.append(PromptScene.EMOTIONAL)

    if has_location:
        scenes.append(PromptScene.LOCATION)

    if is_simple:
        scenes.append(PromptScene.SIMPLE)

    return scenes


def build_prompt_from_scenes(
    scenes: List[str],
    dynamic_context: Dict[str, str] = None,
) -> str:
    """从场景模板 + 动态上下文构建 system prompt。

    Args:
        scenes: 场景列表（来自 classify_scenes）
        dynamic_context: 动态上下文（记忆/情绪/好感度等）

    Returns:
        完整的 system prompt
    """
    parts = []

    # 1. 时间上下文
    parts.append(_get_time_context())

    # 2. 场景模板
    template_names = get_scene_templates(scenes)
    for name in template_names:
        content = get_template(name)
        if content:
            parts.append(content)

    # 3. 对话节奏（非简单消息时加载）
    if PromptScene.SIMPLE not in scenes:
        try:
            from .dialogue_rhythm import RHYTHM_RULES
            parts.append(RHYTHM_RULES)
        except ImportError:
            pass

    # 4. 动态上下文（有序注入）
    if dynamic_context:
        # 按优先级顺序注入
        injection_order = [
            "session_recovery",       # 跨会话恢复
            "topic_context",          # 话题上下文
            "mood_care_hint",         # 情绪关心
            "affection_decay_hint",   # 好感度衰减
            "milestone_hint",         # 关系里程碑
            "schedule",               # 作息
            "personality",            # 个性
            "voice_features",         # 语音感知
            "relationship_style",     # 关系风格
            "state_hints",            # 状态信息
            "user_prefs",             # 用户偏好
            "reply_length",           # 回复长度
            "memories",               # 记忆
            "shared_memory",          # 共同回忆
            "private_meme",           # 私人梗
            "date_hint",              # 重要日期
            "topic_bridge",           # 话题桥接
            "icebreaker",             # 破冰
            "topic_transition",       # 换话题
            "emotion_recovery",       # 情绪恢复
            "emotion_memory",         # 情绪记忆
            "emotion_expression",     # 情绪表达变体
            "group_social",           # 群聊社交
            "group_meme",             # 群聊梗
            "group_role",             # 群聊角色
            "group_heat",             # 群聊热度
            "behavior_hint",          # 行为模式
            "nickname_hint",          # 称呼
            "interest_hint",          # 共同兴趣
            "growth_hint",            # 关系成长
            "catchphrase_hint",       # 口头禅
            "reply_gap_hint",         # 回复间隔
            "bot_emotion_memory",     # 跨会话情绪
            "fatigue_hint",           # 对话疲劳
        ]

        for key in injection_order:
            value = dynamic_context.get(key)
            if value:
                parts.append(value)

    return "\n\n".join(parts)


def _get_time_context() -> str:
    """生成时间上下文。"""
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
