"""记忆压缩与深化：对话压缩、偏好学习、质量评估、深度记忆（共同回忆/私人梗/重要日期/社交关系）。

包含公开 API：get_user_pref_hints, get_shared_memory_hint, get_private_meme_hint, get_date_hint。
"""
import json as _json
import random
import re
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from nonebot import logger

from . import api
from .database import (
    append_memory_summary,
    count_memories,
    get_keep_ids,
    get_memory_summary,
    get_oldest_memories,
    get_quality_stats,
    get_recent_memories,
    get_top_preference,
    save_reply_quality,
    update_user_preference,
)
from .db_preferences import get_top_preferences


# ---------- 对话压缩 ----------

async def _summarize_and_compress(session_id: str):
    """对话压缩：将旧消息摘要后存入 summary 表，并将旧消息标记为 archived=1（B9: 保留历史不删除）。"""
    cnt = await count_memories(session_id)
    if cnt < 25:
        return

    # 摘要缓存：避免重复压缩
    from .context_optimizer import get_cached_summary
    from .context_optimizer import set_cached_summary
    cached = get_cached_summary(session_id, cnt)
    if cached:
        logger.debug(f"[记忆] 使用缓存摘要: {session_id[:20]}...")
        keep_ids = await get_keep_ids(session_id, 20)
        from .db_memories import archive_memories_except
        await archive_memories_except(session_id, keep_ids)
        return

    old_rows = await get_oldest_memories(session_id, 15)
    if len(old_rows) < 10:
        return

    dialog = "\n".join([f"{r['role']}：{r['content'][:100]}" for r in old_rows])
    prompt = f"""请用结构化方式总结以下对话。输出JSON格式，包含3个字段：
- topic: 当前话题（10字内）
- summary: 核心内容摘要（50字内）
- key_info: 用户提到的关键信息（列表，最多3条）

对话内容：
{dialog}

只输出JSON，不要其他文字。"""
    messages = [
        {"role": "system", "content": "你是一个对话摘要助手，只输出摘要文本，不要任何其他内容。"},
        {"role": "user", "content": prompt}
    ]
    summary = await api.call_deepseek_api(messages, temperature=0.5, task_type="summary")
    summary = summary.strip()[:300]
    # 尝试解析结构化摘要，失败则用原文
    try:
        from .utils import clean_json_text
        clean = clean_json_text(summary)
        parsed = _json.loads(clean)
        if isinstance(parsed, dict):
            structured = f"话题:{parsed.get('topic','')}; {parsed.get('summary','')}"
            if parsed.get("key_info"):
                structured += f" [关键:{','.join(parsed['key_info'][:3])}]"
            summary = structured[:300]
    except (ValueError, TypeError):
        pass  # 结构化解析失败用原文
    await append_memory_summary(session_id, summary)

    # 更新摘要缓存
    set_cached_summary(session_id, summary, cnt)

    keep_ids = await get_keep_ids(session_id, 20)
    from .db_memories import archive_memories_except
    await archive_memories_except(session_id, keep_ids)
    logger.info(f"[记忆] 会话 {session_id} 已压缩（归档旧消息），摘要：{summary[:60]}...")


# ---------- 用户偏好自学习（功能③）----------

async def _learn_preferences(user_id: str, raw_msg: str, reply_text: str, session_id: str):
    """从对话行为中异步学习用户偏好。"""
    try:
        # 0. 关系风格学习（Phase 4）：检测用户的口吻风格
        teasing_kw = ["笨蛋", "傻猫", "憨憨", "你不行", "就这", "菜", "笨猫", "蠢猫", "废物", "垃圾"]
        polite_kw = ["请问", "谢谢", "麻烦", "帮忙", "辛苦了", "谢谢你", "多谢"]
        gentle_kw = ["乖", "摸摸", "好喵", "可爱", "最喜欢你了", "抱抱"]

        if any(kw in raw_msg for kw in teasing_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "tsundere", 0.05)
        if any(kw in raw_msg for kw in polite_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "polite", 0.05)
        if any(kw in raw_msg for kw in gentle_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "gentle", 0.05)

        # 1. 回复长度偏好：用户追问 = 想要更长回复
        if len(reply_text) < 30 and any(kw in raw_msg for kw in ["然后呢", "继续", "详细说", "说清楚", "没说完"]):
            await update_user_preference(user_id, "reply_length", "long", 0.1)
        # 回复后用户简短回应 = 想要更短回复
        elif len(reply_text) > 100 and len(raw_msg.strip()) <= 3:
            await update_user_preference(user_id, "reply_length", "short", 0.05)

        # 2. 表情包偏好：用户发表情 = 喜欢表情包
        try:
            if any(kw in raw_msg for kw in ["[表情]", "😂", "🤣", "😍", "😘", "😋", "😜"]):
                await update_user_preference(user_id, "sticker_freq", "high", 0.05)
        except (OSError, ValueError, TypeError) as e:
            logger.debug(f"[偏好] sticker_freq 更新跳过: {e}")

        # 3. 活跃时段
        hour = datetime.now().hour
        if 6 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 22:
            period = "evening"
        else:
            period = "night"
        await update_user_preference(user_id, "active_hours", period, 0.1)

        # 4. 话题兴趣：关键词提取（不再 break，一条消息可以匹配多个话题）
        topic_keywords = {
            "游戏": ["游戏", "打", "玩", "排位", "段位", "王者", "原神", "LOL", "吃鸡", "上分", "开黑", "联机", "steam", "switch", "塞尔达", "明日方舟", "星穹铁道", "铁道"],
            "音乐": ["歌", "音乐", "听歌", "唱", "专辑", "演唱会", "乐队", "贝斯", "吉他", "钢琴", "说唱", "R&B", "KTV", "网易云", "QQ音乐", "播放"],
            "美食": ["吃", "饭", "美食", "好饿", "外卖", "做饭", "火锅", "烧烤", "奶茶", "咖啡", "甜品", "蛋糕", "零食"],
            "学习": ["作业", "考试", "学习", "课", "大学", "论文", "复习", "备考", "图书馆", "考研", "四六级", "期末"],
            "工作": ["上班", "加班", "工作", "老板", "同事", "工资", "跳槽", "面试", "简历", "实习", "出差"],
            "感情": ["喜欢", "恋爱", "对象", "单身", "表白", "分手", "暗恋", "crush", "前任", "谈恋爱"],
            "番剧": ["番", "动漫", "追番", "B站", "动画", "漫画", "新番", "芙莉莲", "我推", "咒术"],
            "运动": ["跑步", "健身", "运动", "减肥", "打球", "游泳", "瑜伽", "跳绳"],
        }
        for topic, keywords in topic_keywords.items():
            if any(kw in raw_msg for kw in keywords):
                await update_user_preference(user_id, "topic_interest", topic, 0.1)

        # 5. 话题情绪关联：记录用户聊什么话题时的情绪
        from .emotion_deep import record_topic_emotion
        emotion_label = ""
        if any(kw in raw_msg for kw in ["哈哈", "笑", "开心", "好", "棒", "喜欢", "爱"]):
            emotion_label = "开心"
        elif any(kw in raw_msg for kw in ["累", "烦", "难过", "哭", "气"]):
            emotion_label = "难过"
        elif any(kw in raw_msg for kw in ["喜欢", "心动", "想", "爱"]):
            emotion_label = "兴奋"
        if emotion_label:
            for topic, keywords in topic_keywords.items():
                if any(kw in raw_msg for kw in keywords):
                    await record_topic_emotion(user_id, topic, emotion_label)

        # 6. 昵称学习：用户自定义称呼
        nickname_patterns = [
            r"以后叫我(.{1,6})",
            r"叫我(.{1,6})就行",
            r"叫我(.{1,6})就好",
            r"叫我(.{1,6})吧",
            r"你可以叫我(.{1,6})",
        ]
        import re as _re
        for pattern in nickname_patterns:
            match = _re.search(pattern, raw_msg)
            if match:
                nickname = match.group(1).strip()
                if 1 <= len(nickname) <= 6 and nickname not in ["你", "我", "他"]:
                    from .db_session import update_user_profile
                    await update_user_profile(user_id, nickname=nickname)
                    logger.info(f"[个性化] 用户 {user_id[:6]} 自定义昵称: {nickname}")
                    break

        logger.debug(f"[偏好] 用户 {user_id[:6]} 偏好学习完成")
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.info(f"[偏好] 学习失败（非关键）: {e}")


async def _sync_profile_summary(user_id: str):
    """同步用户兴趣 + 生成画像摘要（低频率调用，自带跳过逻辑）。"""
    try:
        from .db_session import sync_known_interests, build_user_profile_summary
        await sync_known_interests(user_id)
        await build_user_profile_summary(user_id)
    except (OSError, ValueError, TypeError) as e:
        logger.debug(f"[画像] _sync_profile_summary 失败（非关键）: {e}")


async def get_user_pref_hints(user_id: str) -> Dict[str, str]:
    """获取用户偏好的摘要字典，用于注入 prompt。"""
    try:
        result: Dict[str, str] = {}
        top_length = await get_top_preference(user_id, "reply_length")
        if top_length:
            result["reply_length"] = top_length
        top_sticker = await get_top_preference(user_id, "sticker_freq")
        if top_sticker:
            result["sticker_freq"] = top_sticker
        # 取 top-3 兴趣，而非 top-1
        top_topics = await get_top_preferences(user_id, "topic_interest", limit=3)
        if top_topics:
            result["topic_interest"] = top_topics  # 列表，供 prompt.py 格式化
        # Phase 4：关系风格
        rel_style = await get_top_preference(user_id, "relationship_style")
        if rel_style:
            result["relationship_style"] = rel_style
        return result
    except (ValueError, TypeError, KeyError, _json.JSONDecodeError):
        return {}


# ---------- 回复质量评估（功能⑦）----------

# 正面/负面反应关键词
_POSITIVE_REACTIONS = ["哈哈", "笑死", "😂", "🤣", "有意思", "好玩", "lol", "牛", "厉害", "太强了", "666"]
_NEGATIVE_REACTIONS = ["？", "什么意思", "没听懂", "啥意思", "说人话", "听不懂"]
_REJECTION_REACTIONS = ["滚", "烦", "不想聊", "闭嘴", "别说了", "无聊"]
_NEUTRAL_REACTIONS = ["哦", "嗯", "好", "行", "知道了", "ok"]


async def _evaluate_reply_quality(user_id: str, session_id: str, raw_msg: str, reply_text: str):
    """评估回复质量：根据用户当前消息判断对上一条回复的反应。"""
    try:
        recent = await get_recent_memories(session_id, 4)
        if len(recent) < 3:
            return

        score = 0.0
        feedback_type = "neutral"

        # 正面反应
        if any(kw in raw_msg for kw in _POSITIVE_REACTIONS):
            score = 1.0
            feedback_type = "emoji_reaction"
        # 困惑反应
        elif any(kw in raw_msg for kw in _NEGATIVE_REACTIONS):
            score = -1.0
            feedback_type = "confusion"
        # 拒绝反应
        elif any(kw in raw_msg for kw in _REJECTION_REACTIONS):
            score = -2.0
            feedback_type = "rejection"
        # 话题延续（用户在继续聊 = 回复引发了兴趣）
        elif len(raw_msg) > 10:
            score = 0.5
            feedback_type = "topic_continuation"
        # 简短中性回应
        elif any(kw in raw_msg for kw in _NEUTRAL_REACTIONS):
            score = 0.0
            feedback_type = "neutral"

        if feedback_type != "neutral":
            await save_reply_quality(
                user_id, session_id, reply_text, score, feedback_type
            )
            logger.debug(f"[质量] user={user_id[:6]} score={score} type={feedback_type}")

        # 每 10 条回复调整一次策略
        stats = await get_quality_stats(user_id, days=7)
        if stats["total"] >= 10 and stats["total"] % 10 == 0:
            await _adjust_reply_strategy(user_id, stats)

    except (ValueError, TypeError, KeyError) as e:
        logger.info(f"[质量] 评估失败（非关键）: {e}")


async def _adjust_reply_strategy(user_id: str, stats: Dict[str, Any]):
    """根据历史质量数据调整回复策略。"""
    try:
        avg = stats["avg_score"]
        confusion_rate = stats["confusion_rate"]
        rejection_rate = stats["rejection_rate"]

        # 回复质量差 → 偏好短回复、降低温度
        if avg < -0.3 or rejection_rate > 0.2:
            await update_user_preference(user_id, "reply_length", "short", 0.15)
            logger.info(f"[策略] user={user_id[:6]} 质量差(avg={avg:.2f})，偏好短回复")

        # 回复质量好 → 可以尝试更长回复
        elif avg > 0.5 and stats["positive_rate"] > 0.3:
            await update_user_preference(user_id, "reply_length", "long", 0.1)
            logger.info(f"[策略] user={user_id[:6]} 质量好(avg={avg:.2f})，偏好长回复")

        # 困惑率高 → 偏好更确定的回复（短回复更清晰）
        if confusion_rate > 0.3:
            await update_user_preference(user_id, "reply_length", "short", 0.1)

    except (ValueError, TypeError, KeyError) as e:
        logger.info(f"[策略] 调整失败（非关键）: {e}")


# ---------- 记忆系统深化：共同回忆 ----------

async def _extract_shared_memories(user_id: str, user_msg: str, reply_text: str):
    """从对话中提取共同经历，保存为 shared_memories。"""
    try:
        # 快速关键词预筛：没有相关词汇就跳过 LLM 调用
        trigger_kw = ["第一次", "还记得", "上次", "一起", "我们", "那时候", "记得吗",
                       "认识", "开始", "纪念", "难忘", "印象", "经历", "回忆"]
        combined = user_msg + reply_text
        if not any(kw in combined for kw in trigger_kw):
            return

        prompt = f"""分析以下对话，判断是否包含值得长期记住的共同经历/重要时刻。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"event_type": "类型", "event_desc": "简短描述(20字内)", "emotion_tag": "情绪标签"}}

event_type 可选值：
- first_chat: 第一次聊天/认识
- shared_experience: 一起经历的事
- funny_milestone: 有趣的里程碑
- emotional_moment: 情感共鸣时刻
- important_event: 重要事件

没有值得记住的共同经历就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        from .utils import clean_json_text
        clean = clean_json_text(raw)
        if clean.lower() in ("null", "none", ""):
            return
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("event_type"):
            from .db_memories_deep import save_shared_memory
            await save_shared_memory(
                user_id,
                event_type=data["event_type"],
                event_desc=data.get("event_desc", ""),
                emotion_tag=data.get("emotion_tag", ""),
                context=f"用户:{user_msg[:100]}",
            )
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.debug(f"[共同回忆] 提取失败（非关键）: {e}")


async def _extract_private_memes(user_id: str, user_msg: str, reply_text: str):
    """检测私人梗形成（专属昵称、重复玩笑、暗号）。"""
    try:
        # 昵称检测：用户给 bot 起别名
        nickname_patterns = [
            r"(?:叫你|以后叫|就叫|给你起个|你的名字叫)[\s]*[「\"']?(.{1,6})[」\"']?",
            r"(.{1,4})(?:猫|喵|酱|子|宝|咪)",
        ]
        for pattern in nickname_patterns:
            match = re.search(pattern, user_msg)
            if match:
                nickname = match.group(1).strip()
                if 1 <= len(nickname) <= 6 and nickname not in ["你", "我", "他", "她"]:
                    from .db_memories_deep import save_private_meme
                    await save_private_meme(
                        user_id, "nickname", nickname,
                        origin_context=user_msg[:100],
                        trigger_keywords=nickname,
                        frequency=0.5,
                    )
                    return

        # 玩笑/梗检测：LLM 辅助
        trigger_kw = ["哈哈", "笑死", "梗", "暗号", "只有我们", "专属", "秘密"]
        if not any(kw in user_msg + reply_text for kw in trigger_kw):
            return

        prompt = f"""分析以下对话，判断是否形成了私人梗/专属笑话/暗号。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"meme_type": "类型", "content": "梗的内容(15字内)", "trigger_keywords": "触发关键词(逗号分隔)"}}

meme_type: joke(笑话) / catchphrase(口头禅) / code_word(暗号)
没有新梗就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        from .utils import clean_json_text
        clean = clean_json_text(raw)
        if clean.lower() in ("null", "none", ""):
            return
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("meme_type"):
            from .db_memories_deep import save_private_meme
            await save_private_meme(
                user_id,
                meme_type=data["meme_type"],
                content=data.get("content", ""),
                origin_context=user_msg[:100],
                trigger_keywords=data.get("trigger_keywords", ""),
            )
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.debug(f"[私人梗] 提取失败（非关键）: {e}")


async def _extract_important_dates(user_id: str, user_msg: str):
    """从用户消息中提取重要日期（生日、纪念日等）。"""
    try:
        # 快速关键词预筛
        date_kw = ["生日", "纪念日", "认识", "结婚", "周年", "几月几号", "什么时候"]
        if not any(kw in user_msg for kw in date_kw):
            return

        # 日期格式匹配
        date_patterns = [
            (r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})[日号]?", "full"),
            (r"(\d{1,2})[月/-](\d{1,2})[日号]?", "month_day"),
        ]

        for pattern, fmt in date_patterns:
            match = re.search(pattern, user_msg)
            if match:
                if fmt == "full":
                    date_value = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                else:
                    date_value = f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"

                # 判断日期类型
                if "生日" in user_msg:
                    date_type = "birthday"
                    desc = f"生日 {date_value}"
                elif "纪念" in user_msg or "周年" in user_msg:
                    date_type = "anniversary"
                    desc = f"纪念日 {date_value}"
                elif "认识" in user_msg or "第一次" in user_msg:
                    date_type = "first_chat"
                    desc = f"认识的日子 {date_value}"
                else:
                    date_type = "special_day"
                    desc = f"特别的日子 {date_value}"

                from .db_memories_deep import save_important_date
                await save_important_date(
                    user_id, date_type, date_value,
                    description=desc,
                    repeat_yearly=(fmt == "month_day"),
                )
                return

        # 无日期格式但有关键词时，用 LLM 提取
        if "生日" in user_msg and not any(re.search(p, user_msg) for p, _ in date_patterns):
            prompt = f"""用户提到了生日，提取日期信息。只输出 JSON 或 null。

用户说：{user_msg}

如果有日期，输出：{{"date_value": "MM-DD", "description": "描述"}}
没有明确日期就输出 null。只输出JSON。"""
            messages = [
                {"role": "system", "content": "你是一个信息提取助手，只输出JSON或null。"},
                {"role": "user", "content": prompt}
            ]
            raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
            from .utils import clean_json_text
            clean = clean_json_text(raw)
            if clean.lower() not in ("null", "none", ""):
                data = _json.loads(clean)
                if isinstance(data, dict) and data.get("date_value"):
                    from .db_memories_deep import save_important_date
                    await save_important_date(
                        user_id, "birthday", data["date_value"],
                        description=data.get("description", f"生日 {data['date_value']}"),
                    )
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.debug(f"[重要日期] 提取失败（非关键）: {e}")


# ---------- 记忆深化提示生成 ----------

async def get_shared_memory_hint(user_id: str, current_msg: str) -> Optional[str]:
    """获取共同回忆提示，供 prompt 注入。"""
    try:
        from .db_memories_deep import calculate_topic_relevance
        from .db_memories_deep import get_recall_candidates

        # 提取当前话题关键词
        current_keywords = re.findall(r'[一-鿿]{2,6}', current_msg)

        candidates = await get_recall_candidates(user_id, current_msg, limit=3)
        if not candidates:
            return None

        # 计算每个候选回忆的话题关联度
        scored_candidates = []
        for mem in candidates:
            relevance = calculate_topic_relevance(current_keywords, mem)
            if relevance >= 0.2:  # 最低关联度阈值
                score = relevance * mem.get('importance', 0.5)
                scored_candidates.append((score, mem))

        if not scored_candidates:
            return None

        # 选择关联度最高的回忆
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        mem = scored_candidates[0][1]

        desc = mem["event_desc"]
        event_type = mem["event_type"]
        type_hints = {
            "first_chat": "你们第一次认识的场景",
            "shared_experience": "你们一起经历的事",
            "funny_milestone": "你们之间的趣事",
            "emotional_moment": "你们之间的情感时刻",
            "important_event": "你们共同的重要事件",
        }
        hint = type_hints.get(event_type, "你们的回忆")
        return f"{hint}——{desc}。如果自然的话，可以在对话中不经意提到这段回忆。"
    except (ValueError, TypeError, KeyError, _json.JSONDecodeError):
        return None


async def get_private_meme_hint(user_id: str, current_msg: str) -> Optional[str]:
    """获取私人梗提示，供 prompt 注入。"""
    try:
        from .db_memories_deep import find_matching_meme
        meme = await find_matching_meme(user_id, current_msg)
        if not meme:
            return None
        content = meme["content"]
        meme_type = meme["meme_type"]
        type_hints = {
            "nickname": f"你们之间有个专属昵称「{content}」",
            "joke": f"你们之间有个梗「{content}」",
            "catchphrase": f"你们的口头禅「{content}」",
            "code_word": f"你们的暗号「{content}」",
        }
        return type_hints.get(meme_type, f"你们的默契「{content}」。在合适的时候自然地用出来。")
    except (ValueError, TypeError, KeyError, _json.JSONDecodeError):
        return None


async def get_date_hint(user_id: str) -> Optional[str]:
    """获取重要日期提示，供 prompt 注入。"""
    try:
        from datetime import datetime as _dt

        from .db_memories_deep import get_today_dates
        from .db_memories_deep import get_upcoming_dates
        today = _dt.now().strftime("%m-%d")
        today_dates = await get_today_dates(user_id, today)
        if today_dates:
            date = today_dates[0]
            desc = date.get("description", date["date_type"])
            return f"今天是一个特别的日子——{desc}。可以主动提起，表达在意。"

        upcoming = await get_upcoming_dates(user_id, within_days=3)
        if upcoming:
            date = upcoming[0]
            days = date.get("days_until", 0)
            desc = date.get("description", date["date_type"])
            if days == 1:
                return f"明天是{desc}，可以在聊天中稍微暗示一下你记得。"
            elif days <= 3:
                return f"快到{desc}了（还有{days}天），可以提前准备一下。"
        return None
    except (ValueError, TypeError, KeyError):
        return None


# ---------- 社交能力增强 ----------

async def _extract_social_references(user_id: str, user_msg: str):
    """从用户消息中提取社交圈人物（朋友、家人、同事等）。"""
    try:
        # 关键词预筛
        social_kw = ["我朋友", "我同学", "我同事", "我室友", "我对象", "我男/女朋友",
                      "我妈", "我爸", "我姐", "我哥", "我弟", "我妹",
                      "我老板", "我老师", "我闺蜜", "我兄弟", "我基友",
                      "朋友说", "同学说", "同事说", "室友说"]
        if not any(kw in user_msg for kw in social_kw):
            # 也检查"XX说"模式
            if not re.search(r'[一-鿿]{1,4}(?:说|跟我|找我|约我)', user_msg):
                return

        prompt = f"""从以下用户消息中，提取提到的社交圈人物。
只输出 JSON 数组，没有就输出空数组 []。

用户说：{user_msg}

示例输出：
[
  {{"name": "小明", "relationship": "朋友", "context": "小明说周末一起打球"}},
  {{"name": "妈妈", "relationship": "家人", "context": "我妈让我早点睡"}}
]

只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个信息提取助手，只输出JSON数组。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        from .utils import clean_json_text
        clean = clean_json_text(raw)
        refs = _json.loads(clean) if isinstance(clean, str) else clean
        if not isinstance(refs, list):
            return

        from .db_social import record_social_reference
        for ref in refs:
            if isinstance(ref, dict) and ref.get("name"):
                await record_social_reference(
                    user_id,
                    person_name=ref["name"],
                    relationship=ref.get("relationship", ""),
                    context=ref.get("context", user_msg[:100]),
                )
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.debug(f"[社交记忆] 提取失败（非关键）: {e}")


async def _extract_group_memes(session_id: str, user_id: str, user_msg: str, reply_text: str):
    """检测群聊梗形成并保存。"""
    try:
        is_group = isinstance(session_id, str) and session_id.startswith("group_")
        if not is_group:
            return

        group_id = session_id.replace("group_", "")

        # 快速关键词预筛
        trigger_kw = ["哈哈", "笑死", "经典", "名场面", "永远的神", "暗号", "只有我们"]
        if not any(kw in user_msg + reply_text for kw in trigger_kw):
            return

        prompt = f"""分析以下群聊对话，判断是否形成了群聊梗/专属笑话。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"meme_type": "类型", "content": "梗的内容(15字内)", "trigger_keywords": "触发关键词(逗号分隔)"}}

meme_type: joke(笑话) / catchphrase(口头禅) / event_reference(事件引用) / code_word(暗号)
没有新梗就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        from .utils import clean_json_text
        clean = clean_json_text(raw)
        if clean.lower() in ("null", "none", ""):
            return
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("meme_type"):
            from .db_social import save_group_meme
            await save_group_meme(
                group_id,
                meme_type=data["meme_type"],
                content=data.get("content", ""),
                trigger_keywords=data.get("trigger_keywords", ""),
                creator_id=user_id,
            )
    except (OSError, ValueError, TypeError, KeyError, _json.JSONDecodeError) as e:
        logger.debug(f"[群聊梗] 提取失败（非关键）: {e}")
