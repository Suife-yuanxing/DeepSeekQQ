/* ===== 林念念 Bot — API 客户端（fetch + JWT 自动刷新 + WebSocket）=====
 *
 * 依赖：shared/config.js（先于本文件加载，注入 window.APP_CONFIG.server_base）
 *
 * 对接后端 8766：
 *   - REST: <server_base>/api/v1/*
 *   - WS:   <server_base>/api/v1/chat/ws（子协议 bearer.<jwt>，S5）
 *
 * F6 JWT 自动刷新：access(15min) 过期 → 401 → 自动用 refresh(7d) 换新 → 重试原请求；
 *   refresh 也过期 → 清 token 跳登录页。并发请求只刷新一次。
 *
 * v3 (2026-06-23): 扩展 ~60 个 API 函数，覆盖全部 27 原型页面。
 */
var API = (function() {
  'use strict';

  function _base() {
    return (window.APP_CONFIG && window.APP_CONFIG.server_base) || location.origin;
  }
  function _apiBase() { return _base() + '/api/v1'; }
  function _wsBase() {
    // http→ws, https→wss
    return _base().replace(/^http/, 'ws') + '/api/v1';
  }

  // ── Token 持久化（localStorage，内测足够）──
  function getAccessToken() { return localStorage.getItem('access_token'); }
  function getRefreshToken() { return localStorage.getItem('refresh_token'); }
  function setTokens(access, refresh) {
    localStorage.setItem('access_token', access);
    if (refresh) localStorage.setItem('refresh_token', refresh);
  }
  function clearTokens() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('current_user');
    localStorage.removeItem('current_bot_id');
  }
  function isLoggedIn() { return !!getAccessToken(); }
  function getCurrentUser() {
    try { var u = localStorage.getItem('current_user'); return u ? JSON.parse(u) : null; }
    catch (e) { return null; }
  }
  function setCurrentUser(u) { localStorage.setItem('current_user', JSON.stringify(u)); }
  function getCurrentBotId() {
    var v = localStorage.getItem('current_bot_id');
    return v ? parseInt(v, 10) : null;
  }
  function setCurrentBotId(id) { localStorage.setItem('current_bot_id', String(id)); }

  // ── 401 自动刷新（并发只刷一次）──
  var _refreshing = null;
  function refreshAccessToken() {
    var refresh = getRefreshToken();
    if (!refresh) return Promise.resolve(false);
    if (_refreshing) return _refreshing;
    _refreshing = (async function() {
      try {
        var r = await fetch(_apiBase() + '/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!r.ok) { clearTokens(); return false; }
        var d = await r.json();
        if (d.access_token) {
          localStorage.setItem('access_token', d.access_token);
          return true;
        }
        clearTokens();
        return false;
      } catch (e) {
        clearTokens();
        return false;
      }
    })();
    var p = _refreshing;
    p.then(function() { _refreshing = null; });
    return p;
  }

  // ── fetch 封装 ──
  async function _fetch(path, options) {
    options = options || {};
    var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    var token = getAccessToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    var resp = await fetch(_apiBase() + path, Object.assign({}, options, { headers: headers }));
    // 401 → 尝试刷新一次重试
    if (resp.status === 401 && !options._retried) {
      var ok = await refreshAccessToken();
      if (ok) {
        options._retried = true;
        return _fetch(path, options);
      }
      // 刷新失败 → 跳登录
      clearTokens();
      if (!location.pathname.endsWith('登录页.html') && !location.pathname.endsWith('注册页.html')) {
        location.href = '登录页.html';
      }
      throw new Error('未登录');
    }
    return resp;
  }

  // ── multipart 上传（不设 Content-Type，让浏览器自动设 boundary）──
  async function _upload(path, formData, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    var token = getAccessToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    var resp = await fetch(_apiBase() + path, Object.assign({}, options, { method: 'POST', headers: headers, body: formData }));
    if (resp.status === 401 && !options._retried) {
      var ok = await refreshAccessToken();
      if (ok) { options._retried = true; return _upload(path, formData, options); }
      clearTokens();
      if (!location.pathname.endsWith('登录页.html') && !location.pathname.endsWith('注册页.html')) {
        location.href = '登录页.html';
      }
      throw new Error('未登录');
    }
    return resp;
  }

  // 解析后端标准错误体 {detail: {code, message}} 或 {detail: "string"}
  async function _parseError(resp) {
    try {
      var d = await resp.json();
      if (d.detail) {
        if (typeof d.detail === 'string') return d.detail;
        return d.detail.message || d.detail.code || '请求失败';
      }
      return d.message || ('HTTP ' + resp.status);
    } catch (e) {
      return 'HTTP ' + resp.status;
    }
  }

  // ================================================================
  //  认证
  // ================================================================

  async function sendSms(phone) {
    var r = await _fetch('/auth/sms', { method: 'POST', body: JSON.stringify({ phone: phone }) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function register(phone, code, nickname, password) {
    var r = await _fetch('/auth/register', {
      method: 'POST', body: JSON.stringify({ phone: phone, code: code, nickname: nickname, password: password }),
    });
    if (!r.ok) throw new Error(await _parseError(r));
    var d = await r.json();
    setTokens(d.access_token, d.refresh_token);
    if (d.user) setCurrentUser(d.user);
    return d;
  }

  async function login(phone, code, password) {
    var r = await _fetch('/auth/login', {
      method: 'POST', body: JSON.stringify({ phone: phone, code: code, password: password }),
    });
    if (!r.ok) throw new Error(await _parseError(r));
    var d = await r.json();
    setTokens(d.access_token, d.refresh_token);
    if (d.user) setCurrentUser(d.user);
    return d;
  }

  async function logout() {
    try {
      await _fetch('/auth/logout', {
        method: 'POST', body: JSON.stringify({ refresh_token: getRefreshToken() }),
      });
    } catch (e) { /* 忽略，本地清 token 即可 */ }
    clearTokens();
  }

  async function changePassword(oldPassword, newPassword) {
    var r = await _fetch('/auth/change-password', {
      method: 'POST', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  用户 Profile
  // ================================================================

  async function getProfile() {
    var r = await _fetch('/user/profile');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateProfile(data) {
    var r = await _fetch('/user/profile', { method: 'PATCH', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    var d = await r.json();
    setCurrentUser(d);
    return d;
  }

  async function uploadAvatar(file) {
    var fd = new FormData();
    fd.append('file', file);
    var r = await _upload('/user/avatar', fd);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  用户设置
  // ================================================================

  async function getUserSettings() {
    var r = await _fetch('/user/settings');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateUserSettings(data) {
    var r = await _fetch('/user/settings', { method: 'PATCH', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  数据权限
  // ================================================================

  async function getDataPermissions() {
    var r = await _fetch('/user/data-permissions');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateDataPermissions(data) {
    var r = await _fetch('/user/data-permissions', { method: 'PUT', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  黑名单
  // ================================================================

  async function getBlacklist() {
    var r = await _fetch('/user/blacklist');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function addToBlacklist(blockedUserId, blockedName, reason) {
    var r = await _fetch('/user/blacklist', {
      method: 'POST',
      body: JSON.stringify({ blocked_user_id: blockedUserId, blocked_name: blockedName || '', reason: reason || '' }),
    });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function removeFromBlacklist(blockedUserId) {
    var r = await _fetch('/user/blacklist/' + blockedUserId, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error(await _parseError(r));
    return { ok: true };
  }

  // ================================================================
  //  Bot CRUD
  // ================================================================

  async function listBots() {
    var r = await _fetch('/bots');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function createBot(data) {
    var r = await _fetch('/bots', { method: 'POST', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getBot(id) {
    var r = await _fetch('/bots/' + id);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateBot(id, data) {
    var r = await _fetch('/bots/' + id, { method: 'PUT', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function deleteBot(id) {
    var r = await _fetch('/bots/' + id, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error(await _parseError(r));
    return { ok: true };
  }

  async function uploadBotAvatar(id, file) {
    var fd = new FormData();
    fd.append('file', file);
    var r = await _upload('/bots/' + id + '/avatar', fd);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function clearBotMemory(id) {
    var r = await _fetch('/bots/' + id + '/memory', { method: 'DELETE' });
    if (!r.ok) throw new Error(await _parseError(r));
    return { ok: true };
  }

  async function getBotTemplates() {
    var r = await _fetch('/bot-templates');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  Bot 能力配置
  // ================================================================

  async function getBotAbilities(id) {
    var r = await _fetch('/bots/' + id + '/abilities');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateBotAbilities(id, data) {
    var r = await _fetch('/bots/' + id + '/abilities', { method: 'PUT', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  仪表盘
  // ================================================================

  async function getDashboard() {
    var r = await _fetch('/dashboard');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getBotDashboard(botId) {
    var r = await _fetch('/dashboard/bot/' + botId);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  消息
  // ================================================================

  async function listMessages(botId, cursor) {
    var url = '/messages?bot_id=' + botId + (cursor ? '&cursor=' + cursor : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function searchMessages(q, botId, dateFrom, dateTo) {
    var qs = ['q=' + encodeURIComponent(q)];
    if (botId) qs.push('bot_id=' + botId);
    if (dateFrom) qs.push('date_from=' + dateFrom);
    if (dateTo) qs.push('date_to=' + dateTo);
    var r = await _fetch('/messages/search?' + qs.join('&'));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  WebSocket 聊天
  // ================================================================

  var _ws = null;
  var _wsHandlers = null;

  function openChatWs(handlers) {
    handlers = handlers || {};
    var token = getAccessToken();
    if (!token) throw new Error('未登录，无法建立 WS');
    if (_ws) { try { _ws.close(); } catch (e) {} _ws = null; }

    var url = _wsBase() + '/chat/ws';
    var ws;
    try {
      ws = new WebSocket(url, ['bearer.' + token]);
    } catch (e) {
      if (handlers.onError) handlers.onError('WebSocket 建立失败: ' + e.message);
      return null;
    }
    _ws = ws;
    _wsHandlers = handlers;

    ws.onopen = function() { if (handlers.onOpen) handlers.onOpen(); };
    ws.onmessage = function(e) {
      var f;
      try { f = JSON.parse(e.data); } catch (err) { return; }
      switch (f.type) {
        case 'token':   if (handlers.onToken) handlers.onToken(f.text); break;
        case 'done':    if (handlers.onDone) handlers.onDone(f); break;
        case 'typing':  if (handlers.onTyping) handlers.onTyping(); break;
        case 'ack':     if (handlers.onAck) handlers.onAck(f); break;
        case 'error':   if (handlers.onError) handlers.onError(f.message || '服务端错误'); break;
        case 'pong':    break;
        case 'read_ack': break;
      }
    };
    ws.onerror = function() { if (handlers.onError) handlers.onError('WebSocket 错误'); };
    ws.onclose = function(ev) {
      if (_ws === ws) _ws = null;
      if (handlers.onClose) handlers.onClose(ev);
    };
    return ws;
  }

  function sendMsg(botId, text, clientId) {
    if (!_ws || _ws.readyState !== 1) throw new Error('WebSocket 未连接');
    _ws.send(JSON.stringify({ type: 'msg', bot_id: botId, text: text, client_id: clientId }));
  }

  function closeChatWs() {
    if (_ws) { try { _ws.close(); } catch (e) {} _ws = null; }
  }

  function wsReady() { return _ws && _ws.readyState === 1; }

  function newClientId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'c-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  // ================================================================
  //  通知
  // ================================================================

  async function getNotifications(params) {
    params = params || {};
    var qs = [];
    if (params.type) qs.push('type=' + encodeURIComponent(params.type));
    if (params.unread != null) qs.push('unread=' + (params.unread ? '1' : '0'));
    if (params.cursor) qs.push('cursor=' + encodeURIComponent(params.cursor));
    if (params.limit) qs.push('limit=' + params.limit);
    var url = '/notifications' + (qs.length ? '?' + qs.join('&') : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getUnreadCount() {
    var r = await _fetch('/notifications/unread-count');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function markNotificationRead(id) {
    var r = await _fetch('/notifications/' + id + '/read', { method: 'PATCH' });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function markAllNotificationsRead() {
    var r = await _fetch('/notifications/read-all', { method: 'PATCH' });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  数据面板统计
  // ================================================================

  async function getStatsSummary(botId) {
    var r = await _fetch('/stats/' + botId + '/summary');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsRelation(botId) {
    var r = await _fetch('/stats/' + botId + '/relation');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsMood(params) {
    params = params || {};
    var qs = [];
    if (params.bot_id) qs.push('bot_id=' + params.bot_id);
    if (params.days) qs.push('days=' + params.days);
    var url = '/stats/mood' + (qs.length ? '?' + qs.join('&') : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsMoodDetail(date) {
    var r = await _fetch('/stats/mood/' + date);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsTopics(params) {
    params = params || {};
    var qs = [];
    if (params.bot_id) qs.push('bot_id=' + params.bot_id);
    if (params.days) qs.push('days=' + params.days);
    var url = '/stats/topics' + (qs.length ? '?' + qs.join('&') : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsActiveHours(params) {
    params = params || {};
    var qs = [];
    if (params.bot_id) qs.push('bot_id=' + params.bot_id);
    if (params.days) qs.push('days=' + params.days);
    var url = '/stats/active-hours' + (qs.length ? '?' + qs.join('&') : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsUserProfile(botId) {
    var r = await _fetch('/stats/user-profile?bot_id=' + botId);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getStatsAchievements(botId) {
    var r = await _fetch('/stats/achievements?bot_id=' + botId);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function shareStats(botId) {
    var r = await _fetch('/stats/' + botId + '/share', { method: 'POST' });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  通道管理
  // ================================================================

  async function getQQStatus() {
    var r = await _fetch('/channel/qq/status');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getQQStats() {
    var r = await _fetch('/channel/qq/stats/today');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getQQRecentMessages(limit) {
    var r = await _fetch('/channel/qq/recent-messages?limit=' + (limit || 3));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getQQSettings() {
    var r = await _fetch('/channel/qq/settings');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function updateQQSettings(data) {
    var r = await _fetch('/channel/qq/settings', { method: 'PUT', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getWechatStatus() {
    var r = await _fetch('/channel/wechat/status');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  额度
  // ================================================================

  async function getQuota(botId) {
    var r = await _fetch('/quota?bot_id=' + (botId || ''));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getQuotaStatus() {
    var r = await _fetch('/quota/status');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  API Key 管理
  // ================================================================

  async function getApiKeys() {
    var r = await _fetch('/api-keys');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function createApiKey(data) {
    var r = await _fetch('/api-keys', { method: 'POST', body: JSON.stringify(data) });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function revokeApiKey(id) {
    var r = await _fetch('/api-keys/' + id + '/revoke', { method: 'POST' });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getApiKeyUsageSummary() {
    var r = await _fetch('/api-keys/usage-summary');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getApiKeyUsage(id, range) {
    var r = await _fetch('/api-keys/' + id + '/usage?range=' + (range || '7d'));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getApiKeyEndpoints(id, range) {
    var r = await _fetch('/api-keys/' + id + '/endpoints?range=' + (range || '30d'));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  管理员
  // ================================================================

  async function getSystemMetrics() {
    var r = await _fetch('/admin/system-metrics');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminUsers(page, size) {
    var r = await _fetch('/admin/users?page=' + (page || 1) + '&size=' + (size || 20));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminBots() {
    var r = await _fetch('/admin/bots');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminTokensRanking(period) {
    var r = await _fetch('/admin/tokens/ranking?period=' + (period || 'month'));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminTokens(period) {
    var r = await _fetch('/admin/tokens?period=' + (period || 'month'));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminLogs(level, limit) {
    var r = await _fetch('/admin/logs?level=' + (level || 'info') + '&limit=' + (limit || 50));
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminBackups() {
    var r = await _fetch('/admin/backups');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function createBackup() {
    var r = await _fetch('/admin/backup', { method: 'POST' });
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  async function getAdminMetrics() {
    var r = await _fetch('/admin/metrics');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ================================================================
  //  App 版本
  // ================================================================

  async function getAppVersion() {
    var r = await _fetch('/app/version');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ── 服务器可用性探测（用于启动页判断离线模式）──
  async function _ping() {
    try {
      var ctrl = new AbortController();
      var t = setTimeout(function() { ctrl.abort(); }, 3000);
      var r = await fetch(_apiBase() + '/auth/ping', { method: 'GET', signal: ctrl.signal });
      clearTimeout(t);
      return r.ok;
    } catch (e) { return false; }
  }


  return {
    // auth
    _ping: _ping,
    sendSms: sendSms, register: register, login: login, logout: logout,
    changePassword: changePassword,
    // profile
    getProfile: getProfile, updateProfile: updateProfile, uploadAvatar: uploadAvatar,
    // settings
    getUserSettings: getUserSettings, updateUserSettings: updateUserSettings,
    // data permissions
    getDataPermissions: getDataPermissions, updateDataPermissions: updateDataPermissions,
    // blacklist
    getBlacklist: getBlacklist, addToBlacklist: addToBlacklist, removeFromBlacklist: removeFromBlacklist,
    // bots
    listBots: listBots, createBot: createBot, getBot: getBot,
    updateBot: updateBot, deleteBot: deleteBot,
    uploadBotAvatar: uploadBotAvatar, clearBotMemory: clearBotMemory,
    getBotTemplates: getBotTemplates,
    getBotAbilities: getBotAbilities, updateBotAbilities: updateBotAbilities,
    // messages
    listMessages: listMessages, searchMessages: searchMessages,
    // ws
    openChatWs: openChatWs, sendMsg: sendMsg, closeChatWs: closeChatWs, wsReady: wsReady,
    newClientId: newClientId,
    // dashboard
    getDashboard: getDashboard, getBotDashboard: getBotDashboard,
    // notifications
    getNotifications: getNotifications, getUnreadCount: getUnreadCount,
    markNotificationRead: markNotificationRead, markAllNotificationsRead: markAllNotificationsRead,
    // stats
    getStatsSummary: getStatsSummary, getStatsRelation: getStatsRelation,
    getStatsMood: getStatsMood, getStatsMoodDetail: getStatsMoodDetail,
    getStatsTopics: getStatsTopics, getStatsActiveHours: getStatsActiveHours,
    getStatsUserProfile: getStatsUserProfile, getStatsAchievements: getStatsAchievements,
    shareStats: shareStats,
    // channels
    getQQStatus: getQQStatus, getQQStats: getQQStats,
    getQQRecentMessages: getQQRecentMessages,
    getQQSettings: getQQSettings, updateQQSettings: updateQQSettings,
    getWechatStatus: getWechatStatus,
    // quota
    getQuota: getQuota, getQuotaStatus: getQuotaStatus,
    // api keys
    getApiKeys: getApiKeys, createApiKey: createApiKey, revokeApiKey: revokeApiKey,
    getApiKeyUsageSummary: getApiKeyUsageSummary,
    getApiKeyUsage: getApiKeyUsage, getApiKeyEndpoints: getApiKeyEndpoints,
    // admin
    getSystemMetrics: getSystemMetrics,
    getAdminUsers: getAdminUsers, getAdminBots: getAdminBots,
    getAdminTokensRanking: getAdminTokensRanking, getAdminTokens: getAdminTokens,
    getAdminLogs: getAdminLogs, getAdminBackups: getAdminBackups,
    createBackup: createBackup, getAdminMetrics: getAdminMetrics,
    // app
    getAppVersion: getAppVersion,
    // token / state
    isLoggedIn: isLoggedIn, getAccessToken: getAccessToken, getRefreshToken: getRefreshToken,
    setTokens: setTokens, clearTokens: clearTokens,
    getCurrentUser: getCurrentUser, setCurrentUser: setCurrentUser,
    getCurrentBotId: getCurrentBotId, setCurrentBotId: setCurrentBotId,
    // config
    getServerBase: _base,
  };
})();
