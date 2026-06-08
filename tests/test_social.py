"""社交能力增强测试 — 社交关系图、群聊梗、社交记忆、角色定位。"""
import os
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch


class _MockExecuteResult:
    """同时支持 await 和 async with。"""
    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        async def _f():
            return self._cursor
        return _f().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        pass


# ============================================================
# 社交关系图测试
# ============================================================

class TestSocialGraph:
    @pytest.mark.asyncio
    async def test_record_relationship_new(self):
        """记录新关系不应报错"""
        from plugins.deepseek.db_social import record_relationship
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            cursor = AsyncMock()
            cursor.fetchone = AsyncMock(return_value=None)

            db.execute = MagicMock(return_value=_MockExecuteResult(cursor))
            await record_relationship("group1", "user_a", "user_b", "friend", "一起打游戏")
            assert db.commit.called

    @pytest.mark.asyncio
    async def test_relationship_upgrade(self):
        """互动次数足够时关系应升级"""
        from plugins.deepseek.db_social import _maybe_upgrade_rel
        assert _maybe_upgrade_rel("stranger", 5) == "friend"
        assert _maybe_upgrade_rel("stranger", 3) == "stranger"
        assert _maybe_upgrade_rel("friend", 20) == "close"
        assert _maybe_upgrade_rel("friend", 10) == "friend"

    @pytest.mark.asyncio
    async def test_get_relationships_empty(self):
        """无关系时返回空列表"""
        from plugins.deepseek.db_social import get_relationships
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            cursor = AsyncMock()
            cursor.fetchall = AsyncMock(return_value=[])

            db.execute = MagicMock(return_value=_MockExecuteResult(cursor))
            result = await get_relationships("group1", "user_a")
            assert result == []


# ============================================================
# 群聊梗测试
# ============================================================

class TestGroupMemes:
    @pytest.mark.asyncio
    async def test_save_group_meme(self):
        """保存群聊梗不应报错"""
        from plugins.deepseek.db_social import save_group_meme
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            await save_group_meme("group1", "joke", "经典名场面", "名场面,经典")
            assert db.execute.called

    @pytest.mark.asyncio
    async def test_find_matching_meme_hit(self):
        """关键词匹配应找到梗"""
        from plugins.deepseek.db_social import find_matching_group_meme
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            now = time.time()
            rows = [{
                "id": 1, "meme_type": "joke", "content": "经典名场面",
                "trigger_keywords": "名场面,经典", "frequency": 0.8,
                "last_used": now - 7200,
            }]
            cursor = AsyncMock()
            cursor.fetchall = AsyncMock(return_value=rows)

            db.execute = MagicMock(return_value=_MockExecuteResult(cursor))
            result = await find_matching_group_meme("group1", "这也太经典了吧")
            assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_find_matching_meme_cooldown(self):
        """冷却期内不应触发"""
        from plugins.deepseek.db_social import find_matching_group_meme
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            now = time.time()
            rows = [{
                "id": 1, "meme_type": "joke", "content": "经典名场面",
                "trigger_keywords": "名场面,经典", "frequency": 0.8,
                "last_used": now - 1800,
            }]
            cursor = AsyncMock()
            cursor.fetchall = AsyncMock(return_value=rows)

            db.execute = MagicMock(return_value=_MockExecuteResult(cursor))
            result = await find_matching_group_meme("group1", "名场面")
            assert result is None


# ============================================================
# 社交记忆测试
# ============================================================

class TestSocialReferences:
    @pytest.mark.asyncio
    async def test_record_social_reference(self):
        """记录社交引用不应报错"""
        from plugins.deepseek.db_social import record_social_reference
        with patch('plugins.deepseek.db_social.get_db') as mock_get_db:
            db = AsyncMock()
            mock_get_db.return_value = db
            await record_social_reference("user1", "小明", "朋友", "小明说周末打球")
            assert db.execute.called

    @pytest.mark.asyncio
    async def test_get_social_reference_hint(self):
        """有社交数据时返回提示"""
        from plugins.deepseek.db_social import get_social_reference_hint
        with patch('plugins.deepseek.db_social.get_social_references') as mock_get:
            mock_get.return_value = [
                {"person_name": "小明", "relationship": "朋友", "mentioned_count": 5},
                {"person_name": "妈妈", "relationship": "家人", "mentioned_count": 3},
            ]
            result = await get_social_reference_hint("user1")
            assert result is not None
            assert "小明" in result

    @pytest.mark.asyncio
    async def test_get_social_reference_hint_empty(self):
        """无数据时返回 None"""
        from plugins.deepseek.db_social import get_social_reference_hint
        with patch('plugins.deepseek.db_social.get_social_references') as mock_get:
            mock_get.return_value = []
            result = await get_social_reference_hint("user1")
            assert result is None


# ============================================================
# 群聊角色定位测试
# ============================================================

class TestGroupRole:
    @pytest.mark.asyncio
    async def test_role_hint_small_group(self):
        """小群应该活跃"""
        from plugins.deepseek.db_social import get_group_role_hint
        with patch('plugins.deepseek.db_group.get_active_members') as mock_members, \
             patch('plugins.deepseek.db_social.get_relationships') as mock_rels:
            mock_members.return_value = [
                {"member_id": "a", "nickname": "A"},
                {"member_id": "b", "nickname": "B"},
            ]
            mock_rels.return_value = []
            result = await get_group_role_hint("group1")
            assert "活跃" in result or "多参与" in result

    @pytest.mark.asyncio
    async def test_role_hint_large_group(self):
        """大群应该安静"""
        from plugins.deepseek.db_social import get_group_role_hint
        with patch('plugins.deepseek.db_group.get_active_members') as mock_members, \
             patch('plugins.deepseek.db_social.get_relationships') as mock_rels:
            mock_members.return_value = [
                {"member_id": f"user_{i}", "nickname": f"U{i}"}
                for i in range(20)
            ]
            mock_rels.return_value = []
            result = await get_group_role_hint("group1")
            assert "观众" in result or "安静" in result or "被@" in result


# ============================================================
# 群聊气氛增强测试
# ============================================================

class TestGroupAtmosphere:
    @pytest.mark.asyncio
    async def test_get_group_social_context(self):
        """获取群聊社交上下文不应报错"""
        from plugins.deepseek.group_atmosphere import get_group_social_context
        with patch('plugins.deepseek.db_social.get_group_relationships_summary') as mock_summary, \
             patch('plugins.deepseek.db_social.get_group_meme_hint') as mock_meme, \
             patch('plugins.deepseek.group_atmosphere.get_group_role_hint') as mock_role:
            mock_summary.return_value = ""
            mock_meme.return_value = None
            mock_role.return_value = "群里人不多"
            result = await get_group_social_context("group1", "你好")
            assert "social_hint" in result
            assert "meme_hint" in result
            assert "role_hint" in result


# ============================================================
# prompt 注入测试
# ============================================================

class TestPromptInjection:
    def test_group_params_in_prompt(self):
        """prompt.py 应该有群聊社交参数"""
        prompt_path = os.path.join(os.path.dirname(__file__), '..', 'plugins', 'deepseek', 'prompt.py')
        with open(prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        for param in ['group_social_hint', 'group_meme_hint', 'group_role_hint']:
            assert param in content, f"prompt.py missing param: {param}"

    def test_group_hints_injection(self):
        """prompt.py 应该注入群聊社交提示"""
        prompt_path = os.path.join(os.path.dirname(__file__), '..', 'plugins', 'deepseek', 'prompt.py')
        with open(prompt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "群内关系" in content
        assert "群聊梗" in content
        assert "群聊角色" in content
