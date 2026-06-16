"""Web 管理后台 — Phase 1: 状态查看 + 最近消息 + 配置摘要。

通过 NoneBot2 的 FastAPI driver 注册路由。
访问 /admin 查看仪表盘，/admin/api/* 获取 JSON 数据。

注意：所有 nonebot 导入延迟到 on_startup 回调中，
避免在测试环境（无 nonebot 运行时）导入失败。
"""

import json
import time
from datetime import datetime
from typing import Optional

# 延迟导入 nonebot（测试环境可能不可用）
try:
    from nonebot import get_driver
    from nonebot import logger as _admin_logger
    from nonebot.drivers import Request, Response
    _HAS_NONEBOT = True
except ImportError:
    _HAS_NONEBOT = False
    get_driver = None
    _admin_logger = None
    Request = None
    Response = None

from .config import BASE_URL
from .config import COMPRESS_MESSAGE_THRESHOLD
from .config import COMPRESS_TOKEN_THRESHOLD
from .config import IMAGE_GEN_API_KEY
from .config import MAX_MEMORY
from .config import MAX_REPLY_CHARS
from .config import MODEL
from .config import MUSIC_ENABLED
from .config import REMINDER_ENABLED
from .config import SEARCH_ENABLED
from .config import STICKER_ENABLED
from .config import TAVILY_API_KEY
from .config import VOICE_ENABLED_GROUP
from .config import VOICE_ENABLED_PRIVATE
from .config import WEATHER_ENABLED

# 使用代理以便测试环境可用
def _logger():
    if _admin_logger:
        return _admin_logger
    import logging
    return logging.getLogger("web_admin")

# 启动时间
START_TIME = time.time()

# 请求计数器
_request_counts: dict = {"total": 0, "errors": 0}
_last_request_time: float = 0.0


def _json_response(data: dict, status: int = 200) -> Response:
    """构建 JSON 响应。"""
    return Response(
        content=json.dumps(data, ensure_ascii=False, default=str),
        status_code=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _html_response(html: str, status: int = 200) -> Response:
    """构建 HTML 响应。"""
    return Response(
        content=html,
        status_code=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


# ============================================================
# API 端点
# ============================================================

async def api_status(request):  # type: ignore
    """GET /admin/api/status — Bot 运行状态。"""
    try:
        from .db_core import get_db
        db = await get_db()

        # 会话数
        async with db.execute("SELECT COUNT(DISTINCT session_id) FROM memories") as cur:
            row = await cur.fetchone()
            session_count = row[0] if row else 0

        # 消息总数
        async with db.execute("SELECT COUNT(*) FROM memories") as cur:
            row = await cur.fetchone()
            message_count = row[0] if row else 0

        # 记忆标签数
        async with db.execute("SELECT COUNT(*) FROM memory_tags WHERE confidence >= 0.15") as cur:
            row = await cur.fetchone()
            tag_count = row[0] if row else 0

        # 活跃用户数（最近24h有消息）
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM affection WHERE last_interaction > ?",
            (time.time() - 86400,)
        ) as cur:
            row = await cur.fetchone()
            active_users = row[0] if row else 0

        uptime = time.time() - START_TIME

        return _json_response({
            "status": "running",
            "uptime_seconds": round(uptime),
            "uptime_display": _format_uptime(uptime),
            "sessions": session_count,
            "messages": message_count,
            "memory_tags": tag_count,
            "active_users_24h": active_users,
            "requests": _request_counts,
            "start_time": datetime.fromtimestamp(START_TIME).isoformat(),
        })
    except Exception as e:
        _logger().error(f"[Admin] status 错误: {e}")
        return _json_response({"error": str(e)}, 500)


async def api_health(request: Request = None):
    """GET /health — 健康检查端点，验证服务和数据库连接。

    轻量级访问控制：可选的 health_token 查询参数限制访问。
    """
    # 2.6: 轻量级访问控制 — 配置了 HEALTH_TOKEN 则必须匹配
    import os
    health_token = os.getenv("HEALTH_TOKEN", "").strip()
    if health_token and request:
        token = request.query.get("token", "")
        if token != health_token:
            return _json_response({"status": "denied"}, 401)
    try:
        from .db_core import get_db
        db = await get_db()
        async with db.execute("SELECT 1") as cur:
            await cur.fetchone()
        return _json_response({"status": "ok", "db": "connected"})
    except Exception as e:
        return _json_response({"status": "degraded", "db": str(e)}, 503)


async def api_messages(request):  # type: ignore
    """GET /admin/api/messages?limit=50&session_id=xxx — 最近消息。"""
    try:
        limit = min(int(request.query.get("limit", 50)), 200)
        session_id = request.query.get("session_id", "")

        from .db_core import get_db
        db = await get_db()

        if session_id:
            async with db.execute(
                "SELECT role, content, timestamp FROM memories "
                "WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT role, content, timestamp, session_id FROM memories "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ) as cur:
                rows = await cur.fetchall()

        messages = []
        for row in rows:
            msg = {
                "role": row["role"],
                "content": row["content"][:200],
                "timestamp": row["timestamp"],
                "time": datetime.fromtimestamp(row["timestamp"]).strftime("%m-%d %H:%M:%S"),
            }
            if not session_id:
                msg["session_id"] = row["session_id"]
            messages.append(msg)

        return _json_response({"messages": messages, "count": len(messages)})
    except Exception as e:
        _logger().error(f"[Admin] messages 错误: {e}")
        return _json_response({"error": str(e)}, 500)


async def api_config(request):  # type: ignore
    """GET /admin/api/config — 当前配置摘要（脱敏）。"""
    return _json_response({
        "model": MODEL,
        "base_url": BASE_URL,
        "max_memory": MAX_MEMORY,
        "max_reply_chars": MAX_REPLY_CHARS,
        "compress_token_threshold": COMPRESS_TOKEN_THRESHOLD,
        "compress_message_threshold": COMPRESS_MESSAGE_THRESHOLD,
        "features": {
            "sticker": STICKER_ENABLED,
            "search": SEARCH_ENABLED,
            "reminder": REMINDER_ENABLED,
            "weather": WEATHER_ENABLED,
            "music": MUSIC_ENABLED,
            "voice_private": VOICE_ENABLED_PRIVATE,
            "voice_group": VOICE_ENABLED_GROUP,
            "image_gen": bool(IMAGE_GEN_API_KEY),
            "tavily_search": bool(TAVILY_API_KEY),
        }
    })


async def api_templates(request):  # type: ignore
    """GET /admin/api/templates — 列出所有提示词模板。"""
    try:
        from .prompt_templates import get_template
        from .prompt_templates import list_templates
        templates = list_templates()
        result = {}
        for name, source in templates.items():
            content = get_template(name)
            result[name] = {
                "source": source,
                "length": len(content) if content else 0,
                "preview": content[:100] + "..." if content and len(content) > 100 else content,
            }
        return _json_response({"templates": result, "count": len(result)})
    except (ImportError, AttributeError, ValueError, TypeError, KeyError) as e:
        return _json_response({"error": str(e)}, 500)


async def api_heat(request):  # type: ignore
    """GET /admin/api/heat — 群聊热度状态。"""
    try:
        from .group_heat import heat_manager
        result = {}
        for group_id, gh in heat_manager._groups.items():
            result[group_id] = {
                "heat": round(gh.heat, 2),
                "state": gh.state,
                "message_count": gh.message_count,
                "last_message": datetime.fromtimestamp(gh.last_message_time).strftime("%m-%d %H:%M:%S"),
            }
        return _json_response({"groups": result, "count": len(result)})
    except (ImportError, AttributeError, ValueError, TypeError) as e:
        return _json_response({"error": str(e)}, 500)


async def api_compression(request):  # type: ignore
    """GET /admin/api/compression — 上下文压缩统计。"""
    try:
        from .context_compressor import get_compression_stats
        return _json_response(get_compression_stats())
    except (ImportError, AttributeError, ValueError, TypeError) as e:
        return _json_response({"error": str(e)}, 500)


async def api_tokens(request):  # type: ignore
    """GET /admin/api/tokens — Token 使用量与成本统计。"""
    try:
        from .token_tracker import get_tracker
        return _json_response(get_tracker().get_stats())
    except (ImportError, AttributeError, ValueError, TypeError) as e:
        return _json_response({"error": str(e)}, 500)


def _sanitize_search_query(query: str) -> str:
    """净化搜索输入：限制长度、过滤 LIKE 通配符，防止 SQL 注入式搜索。"""
    # 限制最大 100 字符
    query = query.strip()[:100]
    # 过滤 LIKE 通配符（% _ \），转义后保留原字符含义
    for char in ("\\", "%", "_"):
        query = query.replace(char, f"\\{char}")
    return query


async def api_search_messages(request):  # type: ignore
    """GET /admin/api/search?q=关键词&limit=50 — 对话搜索。"""
    try:
        raw_query = request.query.get("q", "").strip()
        if not raw_query or len(raw_query) < 2:
            return _json_response({"error": "搜索词至少2个字符"}, 400)
        if len(raw_query) > 100:
            return _json_response({"error": "搜索词不能超过100个字符"}, 400)

        query = _sanitize_search_query(raw_query)
        limit = min(int(request.query.get("limit", 50)), 200)

        from .db_core import get_db
        db = await get_db()
        async with db.execute(
            "SELECT role, content, timestamp, session_id FROM memories "
            "WHERE content LIKE ? ESCAPE '\\' ORDER BY timestamp DESC LIMIT ?",
            (f"%{query}%", limit)
        ) as cur:
            rows = await cur.fetchall()

        messages = []
        for row in rows:
            messages.append({
                "role": row["role"],
                "content": row["content"][:300],
                "timestamp": row["timestamp"],
                "time": datetime.fromtimestamp(row["timestamp"]).strftime("%m-%d %H:%M:%S"),
                "session_id": row["session_id"],
            })

        return _json_response({"query": query, "messages": messages, "count": len(messages)})
    except Exception as e:
        return _json_response({"error": str(e)}, 500)


async def api_emotion_history(request):  # type: ignore
    """GET /admin/api/emotion?user_id=xxx&limit=50 — 情绪历史。"""
    try:
        user_id = request.query.get("user_id", "").strip()
        limit = min(int(request.query.get("limit", 50)), 200)

        from .db_core import get_db
        db = await get_db()

        if user_id:
            async with db.execute(
                "SELECT emotion_label, valence, arousal, trigger_text, timestamp "
                "FROM emotion_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT user_id, emotion_label, valence, arousal, timestamp "
                "FROM emotion_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ) as cur:
                rows = await cur.fetchall()

        history = []
        for row in rows:
            entry = {
                "emotion": row["emotion_label"],
                "valence": row["valence"],
                "arousal": row["arousal"],
                "timestamp": row["timestamp"],
                "time": datetime.fromtimestamp(row["timestamp"]).strftime("%m-%d %H:%M:%S"),
            }
            if not user_id:
                entry["user_id"] = row["user_id"]
            if row["trigger_text"]:
                entry["trigger"] = row["trigger_text"][:100]
            history.append(entry)

        # 当前 bot 情绪
        from .db_mood import get_bot_mood
        bot_mood = await get_bot_mood()

        return _json_response({
            "history": history,
            "count": len(history),
            "bot_current": {
                "dominant": bot_mood.get("dominant", "平静"),
                "valence": bot_mood.get("valence", 0.0),
                "arousal": bot_mood.get("arousal", 0.2),
                "reason": bot_mood.get("trigger_reason", ""),
            }
        })
    except Exception as e:
        return _json_response({"error": str(e)}, 500)


async def api_memory_viz(request):  # type: ignore
    """GET /admin/api/memory-viz?user_id=xxx — 记忆可视化数据。"""
    try:
        user_id = request.query.get("user_id", "").strip()
        if not user_id:
            return _json_response({"error": "需要 user_id 参数"}, 400)

        from .db_core import get_db
        db = await get_db()

        # 记忆标签分布
        async with db.execute(
            "SELECT tag_type, COUNT(*) as cnt, AVG(confidence) as avg_conf "
            "FROM memory_tags WHERE user_id = ? AND confidence >= 0.15 "
            "GROUP BY tag_type",
            (user_id,)
        ) as cur:
            tag_rows = await cur.fetchall()

        # 好感度
        async with db.execute(
            "SELECT score, level, title, total_chats, streak_days FROM affection WHERE user_id = ?",
            (user_id,)
        ) as cur:
            aff_row = await cur.fetchone()

        # 共同回忆
        async with db.execute(
            "SELECT event_type, COUNT(*) as cnt FROM shared_memories WHERE user_id = ? GROUP BY event_type",
            (user_id,)
        ) as cur:
            shared_rows = await cur.fetchall()

        # 偏好摘要
        from .db_preferences import get_user_preferences
        prefs = await get_user_preferences(user_id)

        return _json_response({
            "user_id": user_id,
            "memory_tags": [{"type": r["tag_type"], "count": r["cnt"], "avg_confidence": round(r["avg_conf"], 3)} for r in tag_rows],
            "affection": {
                "score": round(aff_row["score"], 1) if aff_row else 0,
                "level": aff_row["level"] if aff_row else 0,
                "title": aff_row["title"] if aff_row else "陌生人",
                "total_chats": aff_row["total_chats"] if aff_row else 0,
                "streak_days": aff_row["streak_days"] if aff_row else 0,
            } if aff_row else None,
            "shared_memories": [{"type": r["event_type"], "count": r["cnt"]} for r in shared_rows],
            "preferences": {k: dict(v) for k, v in prefs.items()},
        })
    except Exception as e:
        return _json_response({"error": str(e)}, 500)


# ============================================================
# HTML 仪表盘
# ============================================================

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>念念 Bot 管理后台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#1a1a2e;color:#e0e0e0;padding:20px}
h1{color:#e94560;margin-bottom:20px}
h2{color:#f0a050;margin:24px 0 12px;font-size:16px}
.card{background:#16213e;border-radius:8px;padding:16px;margin-bottom:16px;border:1px solid #0f3460}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.stat{background:#0f3460;border-radius:6px;padding:12px;text-align:center}
.stat .label{font-size:12px;color:#a0a0b0}
.stat .value{font-size:24px;font-weight:bold;color:#e94560;margin-top:4px}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px}
.feature{padding:8px 12px;border-radius:4px;font-size:13px}
.feature.on{background:#1a4a1a;color:#4caf50}
.feature.off{background:#4a1a1a;color:#f44336}
.msg-list{max-height:400px;overflow-y:auto}
.msg{padding:8px 0;border-bottom:1px solid #0f3460;font-size:13px}
.msg .role{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;margin-right:8px}
.msg .role.user{background:#0f3460;color:#4fc3f7}
.msg .role.assistant{background:#4a2040;color:#f06292}
.msg .time{color:#666;font-size:11px;margin-right:8px}
.msg .content{color:#ccc}
.btn{padding:6px 16px;border-radius:4px;border:none;cursor:pointer;font-size:13px;margin-right:8px;margin-bottom:8px}
.btn-primary{background:#e94560;color:#fff}
.btn-secondary{background:#0f3460;color:#e0e0e0}
.btn:hover{opacity:0.8}
.tab-content{display:none}
.tab-content.active{display:block}
pre{background:#0a0a1a;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px}
</style>
</head>
<body>
<h1>🐱 念念 Bot 管理后台</h1>
<div style="margin-bottom:12px;font-size:13px;color:#888">
  认证状态: <span id="auth-status">🔒</span>
  <button class="btn btn-secondary" onclick="setToken()" style="font-size:11px;padding:2px 8px">设置Token</button>
</div>

<div id="status-card" class="card">
  <h2>运行状态</h2>
  <div class="stat-grid">
    <div class="stat"><div class="label">运行时间</div><div class="value" id="uptime">-</div></div>
    <div class="stat"><div class="label">活跃会话</div><div class="value" id="sessions">-</div></div>
    <div class="stat"><div class="label">总消息数</div><div class="value" id="messages">-</div></div>
    <div class="stat"><div class="label">记忆标签</div><div class="value" id="tags">-</div></div>
    <div class="stat"><div class="label">24h活跃用户</div><div class="value" id="active_users">-</div></div>
  </div>
</div>

<div class="card">
  <h2>功能开关</h2>
  <div class="feature-grid" id="features"></div>
</div>

<div class="card">
  <h2>
    最近消息
    <button class="btn btn-secondary" onclick="refreshMessages()">刷新</button>
    <button class="btn btn-primary" onclick="toggleAutoRefresh()" id="auto-btn">自动刷新: 关</button>
  </h2>
  <div class="msg-list" id="msg-list">加载中...</div>
</div>

<div class="card">
  <h2>群聊热度</h2>
  <pre id="heat-data">加载中...</pre>
</div>

<div class="card">
  <h2>Token 用量 & 成本</h2>
  <div class="stat-grid">
    <div class="stat"><div class="label">今日调用</div><div class="value" id="token-today-calls">-</div></div>
    <div class="stat"><div class="label">今日费用</div><div class="value" id="token-today-cost">-</div></div>
    <div class="stat"><div class="label">本月费用</div><div class="value" id="token-month-cost">-</div></div>
    <div class="stat"><div class="label">缓存命中率</div><div class="value" id="token-cache">-</div></div>
  </div>
</div>

<div class="card">
  <h2>对话搜索 <input type="text" id="search-input" placeholder="输入关键词搜索..." style="padding:6px;width:200px;background:#0a0a1a;color:#e0e0e0;border:1px solid #0f3460;border-radius:4px"> <button class="btn btn-primary" onclick="doSearch()">搜索</button></h2>
  <div class="msg-list" id="search-results" style="max-height:300px">输入关键词搜索对话历史</div>
</div>

<div class="card">
  <h2>情绪历史 <input type="text" id="emotion-user-input" placeholder="用户ID(留空=全部)" style="padding:6px;width:150px;background:#0a0a1a;color:#e0e0e0;border:1px solid #0f3460;border-radius:4px;margin-left:8px"> <button class="btn btn-secondary" onclick="loadEmotion()">查询</button></h2>
  <div id="bot-current-mood" style="margin-bottom:8px;font-size:14px"></div>
  <pre id="emotion-data" style="max-height:300px;overflow-y:auto">点击查询加载</pre>
</div>

<div class="card">
  <h2>压缩状态</h2>
  <pre id="compression-data">加载中...</pre>
</div>

<script>
let authToken = localStorage.getItem('admin_token') || '';
let autoRefresh = false;
let autoTimer = null;

// 首次访问时提示输入 Token
if (!authToken) {
  const input = prompt('请输入 Admin Token（设置后保存在浏览器本地）：');
  if (input && input.trim()) {
    authToken = input.trim();
    localStorage.setItem('admin_token', authToken);
  }
}

async function fetchJSON(url) {
  const headers = {};
  if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
  const r = await fetch(url, { headers });
  if (r.status === 401 || r.status === 403) {
    localStorage.removeItem('admin_token');
    authToken = '';
    alert('认证失败，请刷新页面重新输入 Token');
    throw new Error('Unauthorized');
  }
  return r.json();
}

function setToken() {
  const input = prompt('请输入新的 Admin Token：', authToken);
  if (input && input.trim()) {
    authToken = input.trim();
    localStorage.setItem('admin_token', authToken);
    alert('Token 已保存，刷新页面生效');
  }
}

async function loadStatus() {
  const d = await fetchJSON('/admin/api/status');
  document.getElementById('uptime').textContent = d.uptime_display || '-';
  document.getElementById('sessions').textContent = d.sessions || 0;
  document.getElementById('messages').textContent = d.messages || 0;
  document.getElementById('tags').textContent = d.memory_tags || 0;
  document.getElementById('active_users').textContent = d.active_users_24h || 0;
}

async function loadConfig() {
  const d = await fetchJSON('/admin/api/config');
  const grid = document.getElementById('features');
  grid.innerHTML = Object.entries(d.features || {}).map(([k,v]) =>
    `<div class="feature ${v?'on':'off'}">${k}: ${v?'✅':'❌'}</div>`
  ).join('');
}

async function refreshMessages() {
  const d = await fetchJSON('/admin/api/messages?limit=30');
  const list = document.getElementById('msg-list');
  list.innerHTML = (d.messages || []).map(m =>
    `<div class="msg"><span class="time">${m.time}</span><span class="role ${m.role}">${m.role==='user'?'用户':'Bot'}</span><span class="content">${escHtml(m.content)}</span></div>`
  ).join('') || '<div class="msg">暂无消息</div>';
}

async function loadHeat() {
  const d = await fetchJSON('/admin/api/heat');
  document.getElementById('heat-data').textContent = JSON.stringify(d, null, 2);
}

async function loadCompression() {
  const d = await fetchJSON('/admin/api/compression');
  document.getElementById('compression-data').textContent = JSON.stringify(d, null, 2);
}

function escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  document.getElementById('auto-btn').textContent = '自动刷新: ' + (autoRefresh ? '开' : '关');
  if (autoRefresh) {
    autoTimer = setInterval(() => { loadStatus(); refreshMessages(); loadHeat(); }, 5000);
  } else {
    clearInterval(autoTimer);
  }
}

async function loadTokens() {
  try {
    const d = await fetchJSON('/admin/api/tokens');
    document.getElementById('token-today-calls').textContent = d.today?.calls ?? '-';
    document.getElementById('token-today-cost').textContent = '$' + (d.today?.cost_usd ?? 0).toFixed(4);
    document.getElementById('token-month-cost').textContent = '$' + (d.month?.cost_usd ?? 0).toFixed(4);
    document.getElementById('token-cache').textContent = ((d.cache_hit_rate ?? 0) * 100).toFixed(0) + '%';
  } catch(e) { console.error(e); }
}

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q || q.length < 2) { alert('搜索词至少2个字符'); return; }
  const d = await fetchJSON('/admin/api/search?q=' + encodeURIComponent(q) + '&limit=30');
  const el = document.getElementById('search-results');
  el.innerHTML = (d.messages || []).map(m =>
    `<div class="msg"><span class="time">${m.time}</span><span class="role ${m.role}">${m.role==='user'?'用户':'Bot'}</span><span class="content">${escHtml(m.content)}</span></div>`
  ).join('') || '<div class="msg">无结果</div>';
}

async function loadEmotion() {
  const uid = document.getElementById('emotion-user-input').value.trim();
  const url = uid ? '/admin/api/emotion?user_id=' + uid + '&limit=30' : '/admin/api/emotion?limit=30';
  const d = await fetchJSON(url);
  document.getElementById('bot-current-mood').innerHTML = '<b>Bot当前:</b> ' +
    (d.bot_current?.dominant || '?') + ' (V:' + (d.bot_current?.valence||0).toFixed(2) +
    ' A:' + (d.bot_current?.arousal||0).toFixed(2) + ') ' +
    (d.bot_current?.reason ? '(' + d.bot_current.reason + ')' : '');
  document.getElementById('emotion-data').textContent = (d.history || []).map(h =>
    `[${h.time}] ${h.emotion || '?'} V:${(h.valence||0).toFixed(2)} A:${(h.arousal||0).toFixed(2)}` +
    (h.user_id ? ' user:' + h.user_id : '') + (h.trigger ? ' ← ' + h.trigger : '')
  ).join('\n') || '无记录';
}

loadStatus(); loadConfig(); refreshMessages(); loadHeat(); loadCompression(); loadTokens();
document.getElementById('auth-status').textContent = authToken ? '🔒 已认证' : '🔓 未认证';
</script>
</body>
</html>"""


async def admin_dashboard(request):  # type: ignore
    """GET /admin — 管理后台仪表盘。"""
    return _html_response(_DASHBOARD_HTML)


# ============================================================
# 注册路由
# ============================================================

# 使用与 startup.py 相同的注册模式
if _HAS_NONEBOT:
    driver = get_driver()

    # ── C-1: Admin 认证中间件（模块级别 — 必须在应用启动前注册）──
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from .middleware.auth import ADMIN_API_KEY, is_admin_path, check_admin_key_configured

        app = driver.server_app
        if app and isinstance(app, FastAPI):
            _admin_config_done = check_admin_key_configured()

            @app.middleware("http")
            async def admin_auth_middleware(request, call_next):
                """Admin 端点 Bearer Token 认证中间件 + 访问审计日志。"""
                from .middleware.auth import ADMIN_API_KEY as _ADMIN_KEY, is_admin_path as _is_admin

                if not _ADMIN_KEY:
                    if _is_admin(request.url.path):
                        return JSONResponse(
                            {"error": "管理后台未启用，请设置 ADMIN_API_KEY 环境变量"},
                            status_code=503,
                        )
                elif _is_admin(request.url.path):
                    peer = request.client.host if request.client else "unknown"
                    path = request.url.path
                    auth_header = request.headers.get("Authorization", "")
                    if not auth_header.startswith("Bearer "):
                        _admin_logger.warning(f"[审计] Admin 访问拒绝（无Token）: {peer} → {path}")
                        return JSONResponse(
                            {"error": "需要 Bearer Token 认证"},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                    token = auth_header[7:]
                    if token != _ADMIN_KEY:
                        _admin_logger.warning(f"[审计] Admin 访问拒绝（Token无效）: {peer} → {path}")
                        return JSONResponse(
                            {"error": "Token 无效"},
                            status_code=403,
                        )
                    _admin_logger.info(f"[审计] Admin 访问: {peer} → {path}")
                return await call_next(request)
    except (ImportError, ValueError, TypeError) as e:
        _admin_logger.warning(f"[Admin] 认证中间件注册失败: {e}")

    @driver.on_startup
    async def _register_admin_routes():
        """在 driver 启动后注册管理后台路由（仅 API 路由，中间件已在模块级别注册）。"""
        try:
            from fastapi import FastAPI, HTTPException

            app = driver.server_app
            if app and isinstance(app, FastAPI):
                # API 端点（response_model=None 避免 FastAPI 对 NoneBot Response 类型报错）
                app.add_api_route("/admin/api/status", api_status, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/messages", api_messages, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/config", api_config, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/templates", api_templates, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/heat", api_heat, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/compression", api_compression, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/tokens", api_tokens, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/search", api_search_messages, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/emotion", api_emotion_history, methods=["GET"], response_model=None)
                app.add_api_route("/admin/api/memory-viz", api_memory_viz, methods=["GET"], response_model=None)

                # HTML 仪表盘
                app.add_api_route("/admin", admin_dashboard, methods=["GET"], response_model=None)
                app.add_api_route("/admin/", admin_dashboard, methods=["GET"], response_model=None)

                from .middleware.auth import ADMIN_API_KEY as _AK
                _admin_logger.info(f"[Admin] 管理后台已注册: http://{SERVER_HOST}:8082/admin (认证: {'启用' if _AK else '未配置'})")
            else:
                _admin_logger.warning("[Admin] FastAPI app 不可用，管理后台未注册")
        except (ImportError, ValueError, TypeError) as e:
            _admin_logger.warning(f"[Admin] 注册失败（非关键错误）: {e}")

    @driver.on_startup
    async def _register_health():
        """在 driver 启动后独立注册健康检查端点（不受 admin 注册失败影响）。"""
        try:
            from fastapi import FastAPI
            from fastapi import Request as FastAPIRequest
            app = driver.server_app
            if app and isinstance(app, FastAPI):
                app.add_api_route("/health", api_health, methods=["GET"], response_model=None)
                _admin_logger.info(f"[Health] 健康检查已注册: http://{SERVER_HOST}:8082/health")
        except (ImportError, ValueError, TypeError) as e:
            _admin_logger.warning(f"[Health] 注册失败: {e}")
else:
    # 测试环境：注册空回调，避免模块导入失败
    _admin_logger = None


def _format_uptime(seconds: float) -> str:
    """格式化运行时间。"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if days: parts.append(f"{days}天")
    if hours: parts.append(f"{hours}小时")
    parts.append(f"{minutes}分钟")
    return " ".join(parts)
