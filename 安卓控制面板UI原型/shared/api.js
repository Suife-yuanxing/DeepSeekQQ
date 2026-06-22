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

  // ── 业务 API ──
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

  async function getProfile() {
    var r = await _fetch('/user/profile');
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

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

  async function listMessages(botId, cursor) {
    var url = '/messages?bot_id=' + botId + (cursor ? '&cursor=' + cursor : '');
    var r = await _fetch(url);
    if (!r.ok) throw new Error(await _parseError(r));
    return r.json();
  }

  // ── WebSocket 聊天 ──
  var _ws = null;
  var _wsHandlers = null;

  function openChatWs(handlers) {
    handlers = handlers || {};
    var token = getAccessToken();
    if (!token) throw new Error('未登录，无法建立 WS');
    // 关闭旧连接
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
        case 'pong':    break;  // 心跳响应
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

  // 生成 client_id（幂等去重用，S3）
  function newClientId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'c-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  return {
    // auth
    sendSms: sendSms, register: register, login: login, logout: logout,
    getProfile: getProfile,
    // bots
    listBots: listBots, createBot: createBot, getBot: getBot,
    // messages
    listMessages: listMessages,
    // ws
    openChatWs: openChatWs, sendMsg: sendMsg, closeChatWs: closeChatWs, wsReady: wsReady,
    newClientId: newClientId,
    // token / state
    isLoggedIn: isLoggedIn, getAccessToken: getAccessToken, getRefreshToken: getRefreshToken,
    setTokens: setTokens, clearTokens: clearTokens,
    getCurrentUser: getCurrentUser, setCurrentUser: setCurrentUser,
    getCurrentBotId: getCurrentBotId, setCurrentBotId: setCurrentBotId,
    // config
    getServerBase: _base,
  };
})();
