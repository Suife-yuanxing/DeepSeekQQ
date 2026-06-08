"""话题追踪模块：维护对话话题链，避免重复提问。

功能：
1. 每 3-5 轮对话提取一次话题链
2. 存入 session_state 的 recent_topics 字段
3. 会话恢复时注入话题链到 prompt
"""

import re
import json
import time
from typing import List, Dict, Any, Optional
from nonebot import logger


# 话题关键词提取（去掉停用词）
_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "他", "她", "它", "们", "吗", "呢", "吧", "啊", "哦",
    "什么", "怎么", "为什么", "哪里", "哪个", "多少", "几",
}


def extract_topic_keywords(text: str, max_keywords: int = 3) -> List[str]:
    """从文本中提取话题关键词。"""
    # 提取中文词组（2-6字）
    words = re.findall(r'[一-鿿]{2,6}', text[:500])
    # 过滤停用词，保留有意义的词
    keywords = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    # 去重保序
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:max_keywords]


def classify_topic_type(keywords: List[str]) -> str:
    """根据关键词分类话题类型。"""
    keyword_str = " ".join(keywords)

    # 话题类型分类
    topic_types = {
        "technology": ["手机", "电脑", "游戏", "软件", "编程", "代码", "AI", "人工智能"],
        "food": ["吃", "喝", "饭", "菜", "餐厅", "外卖", "美食", "零食"],
        "entertainment": ["电影", "电视剧", "综艺", "音乐", "动漫", "小说", "漫画"],
        "daily": ["天气", "睡觉", "起床", "上班", "上学", "工作", "学习"],
        "emotion": ["开心", "难过", "生气", "累", "烦", "压力", "焦虑"],
        "relationship": ["朋友", "家人", "父母", "对象", "恋爱", "结婚"],
        "hobby": ["运动", "健身", "旅游", "摄影", "画画", "做饭", "手工"],
    }

    for topic_type, type_keywords in topic_types.items():
        if any(kw in keyword_str for kw in type_keywords):
            return topic_type

    return "general"


class TopicTracker:
    """话题追踪器：维护每个会话的话题链。"""

    def __init__(self, max_topics: int = 5, update_interval: int = 3):
        """
        Args:
            max_topics: 最多保留的话题数
            update_interval: 每隔几轮对话更新一次话题链
        """
        self._max_topics = max_topics
        self._update_interval = update_interval
        # session_id -> {"topics": [...], "message_count": int, "last_update": float}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        """获取或创建会话状态。"""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "topics": [],
                "message_count": 0,
                "last_update": 0,
            }
        return self._sessions[session_id]

    def add_message(self, session_id: str, user_msg: str) -> None:
        """添加用户消息，更新话题计数。"""
        session = self._get_session(session_id)
        session["message_count"] += 1

    def should_update(self, session_id: str) -> bool:
        """检查是否应该更新话题链。"""
        session = self._get_session(session_id)
        return session["message_count"] % self._update_interval == 0

    def update_topics(self, session_id: str, user_msg: str, reply_text: str) -> List[Dict[str, Any]]:
        """更新话题链。

        Returns:
            更新后的话题列表
        """
        session = self._get_session(session_id)

        # 提取当前话题关键词
        keywords = extract_topic_keywords(user_msg)
        if not keywords:
            return session["topics"]

        # 话题类型
        topic_type = classify_topic_type(keywords)

        # 构建话题记录
        topic_record = {
            "keywords": keywords,
            "type": topic_type,
            "summary": f"{keywords[0]}（{topic_type}）",
            "user_msg": user_msg[:100],
            "reply_snippet": reply_text[:100],
            "time": time.time(),
        }

        # 检查是否与最近话题重复（关键词重叠度 > 50%）
        if session["topics"]:
            last_topic = session["topics"][-1]
            last_keywords = set(last_topic.get("keywords", []))
            current_keywords = set(keywords)
            overlap = len(last_keywords & current_keywords) / max(len(last_keywords | current_keywords), 1)
            if overlap > 0.5:
                # 更新现有话题
                session["topics"][-1] = topic_record
                return session["topics"]

        # 添加新话题
        session["topics"].append(topic_record)

        # 保留最近 N 个话题
        if len(session["topics"]) > self._max_topics:
            session["topics"] = session["topics"][-self._max_topics:]

        session["last_update"] = time.time()
        logger.debug(f"[话题追踪] {session_id[:20]}... 更新话题: {keywords}")

        return session["topics"]

    def get_topic_chain(self, session_id: str) -> str:
        """获取话题链的 prompt 文本。

        Returns:
            格式化的话题链提示，用于注入到系统 prompt
        """
        session = self._get_session(session_id)
        topics = session.get("topics", [])

        if not topics:
            return ""

        # 构建话题链描述
        lines = ["【对话话题链】最近聊过的话题："]
        for i, topic in enumerate(topics[-3:], 1):  # 只显示最近3个
            keywords = topic.get("keywords", [])
            topic_type = topic.get("type", "general")
            lines.append(f"{i}. {'、'.join(keywords)}（{topic_type}）")

        lines.append("\n重要：不要重复问用户已经说过的事情！如果用户提到过某个话题，直接延续，不要反问。")

        return "\n".join(lines)

    def get_known_info(self, session_id: str, recent_memories: List[Dict[str, Any]] = None) -> str:
        """从最近记忆中提取已知信息，避免重复提问。

        Returns:
            已知信息提示
        """
        if not recent_memories:
            return ""

        known_facts = []
        for mem in recent_memories[-10:]:  # 检查最近10条记忆
            content = mem.get("content", "")
            role = mem.get("role", "")

            # 提取用户陈述的事实
            if role == "user":
                # 检测陈述句（不是问句）
                if not any(kw in content for kw in ["？", "?", "吗", "呢", "怎么", "为什么", "什么"]):
                    # 提取关键信息
                    if len(content) > 3 and len(content) < 100:
                        known_facts.append(content[:50])

        if not known_facts:
            return ""

        lines = ["【已知信息】用户之前说过的话（不要重复问）："]
        for fact in known_facts[-5:]:  # 只显示最近5条
            lines.append(f"- {fact}")

        return "\n".join(lines)


# 全局单例
topic_tracker = TopicTracker()


async def update_topic_tracker(session_id: str, user_msg: str, reply_text: str) -> None:
    """更新话题追踪器（异步包装）。"""
    try:
        topic_tracker.add_message(session_id, user_msg)
        if topic_tracker.should_update(session_id):
            topic_tracker.update_topics(session_id, user_msg, reply_text)
    except Exception as e:
        logger.debug(f"[话题追踪] 更新失败（非关键）: {e}")


def get_topic_context(session_id: str, recent_memories: List[Dict[str, Any]] = None) -> str:
    """获取话题上下文（用于 prompt 注入）。"""
    try:
        topic_chain = topic_tracker.get_topic_chain(session_id)
        known_info = topic_tracker.get_known_info(session_id, recent_memories)
        return f"{topic_chain}\n\n{known_info}" if topic_chain and known_info else topic_chain or known_info
    except Exception as e:
        logger.debug(f"[话题追踪] 获取上下文失败（非关键）: {e}")
        return ""
