/**
 * TokenLens v2.0 — 前端看板 (Vanilla JS + Chart.js)
 * 新增: 表格排序 / 行内可视化 / 周期对比 / 增量指标
 */

// ─── 状态 ──────────────────────────────────────────
const STATE = {
  period: 'week',
  project: '',
  source: 'all',
  tz: 8,
};

// 表格排序状态
const SORT = {
  model: { key: 'cost', asc: false },
  session: { key: 'tokens', asc: false },
};

// 缓存上次数据用于对比 + resize 重建图表
var _prevStats = null;
var _compareData = null;
var _cachedChartData = null;  // { stats, trend, hourly, tools } for resize rebuild

const isMobile = window.matchMedia('(max-width: 768px)').matches;

// Chart.js 实例池（用于安全销毁）
const chartInstances = {};

// ─── 数字格式化 ────────────────────────────────────
function fmt(n) {
  if (n == null || isNaN(n)) return '—';
  if (isMobile) return fmtShort(n);
  return n.toLocaleString();
}

function fmtShort(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
  return String(Math.round(n));
}

function fmtCost(n) {
  if (n == null || isNaN(n)) return '—';
  if (isMobile) return '\xA5' + Math.round(n).toLocaleString();
  return '\xA5' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(p) {
  if (p == null || isNaN(p)) return '—';
  return (p * 100).toFixed(1) + '%';
}

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    var d = new Date(ts);
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
      + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch (e) { return (ts || '').slice(0, 16); }
}

// ─── Toast ─────────────────────────────────────────
var toastTimer;
function showToast(msg, isError) {
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.className = 'toast'; }, 3000);
}

// ─── Chart.js 可用性检查 ───────────────────────────
function hasChartJS() {
  return typeof Chart !== 'undefined';
}

function safeDestroyChart(key) {
  try {
    if (chartInstances[key]) {
      chartInstances[key].destroy();
      chartInstances[key] = null;
    }
  } catch (e) { chartInstances[key] = null; }
}

function destroyAllCharts() {
  Object.keys(chartInstances).forEach(function (k) { safeDestroyChart(k); });
}

// ─── 安全图表创建 ──────────────────────────────────
function safeCreateChart(key, canvasId, config) {
  safeDestroyChart(key);
  var canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  var errEl = document.getElementById(canvasId + '-error');
  try {
    if (!hasChartJS()) throw new Error('Chart.js 未加载');
    var ctx = canvas.getContext('2d');
    chartInstances[key] = new Chart(ctx, config);
    if (errEl) errEl.style.display = 'none';
    return chartInstances[key];
  } catch (e) {
    console.error('Chart init failed (' + key + '):', e);
    if (errEl) { errEl.style.display = 'flex'; errEl.textContent = '⚠️ 图表加载失败: ' + e.message; }
    return null;
  }
}

// ─── 颜色方案 ──────────────────────────────────────
function getChartColors() {
  var style = getComputedStyle(document.documentElement);
  var accent = (style.getPropertyValue('--tl-accent') || '#58a6ff').trim();
  return [
    accent,
    '#3fb950', '#d29922', '#f85149',
    '#a371f7', '#79c0ff', '#56d4dd', '#f778ba',
  ];
}

// ─── API ───────────────────────────────────────────
async function api(path, silent) {
  try {
    var resp = await fetch(window.location.origin + path);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } catch (e) {
    if (!silent) showToast('请求失败: ' + e.message, true);
    return null;
  }
}

// ─── 核心数据加载 ──────────────────────────────────
async function loadAll() {
  var p = STATE.period;
  var proj = STATE.project;
  var src = STATE.source;
  var baseParams = 'period=' + p + '&tz=' + STATE.tz + (proj ? '&project=' + encodeURIComponent(proj) : '');
  var t0 = performance.now();

  // 并行请求 9 个端点（新增 compare）
  var results = await Promise.all([
    api('/api/stats?' + baseParams),
    api('/api/models?' + baseParams + '&source=' + src),
    api('/api/cache-advice?period=' + p + '&tz=' + STATE.tz),
    api('/api/sessions?limit=50&' + baseParams),
    api('/api/trend?' + baseParams),
    api('/api/billing', true),
    api('/api/hourly?' + baseParams, true),
    api('/api/tools?' + baseParams, true),
    api('/api/stats/compare?' + baseParams, true),
  ]);

  var stats = results[0];
  var modelsData = results[1];
  var advice = results[2];
  var sessions = results[3];
  var trend = results[4];
  var billing = results[5];
  var hourly = results[6];
  var tools = results[7];
  var compare = results[8];

  // 保存对比数据
  _compareData = compare;

  // 渲染各模块
  renderPeriodRange(stats);
  renderKPIs(stats, billing, compare);
  renderModelsTable(modelsData);
  renderAdvice(advice);
  renderSessionsTable(sessions);
  renderAllCharts(stats, trend, hourly, tools);
  _cachedChartData = { stats: stats, trend: trend, hourly: hourly, tools: tools };
  updateShareCard(stats, p);
  updateFooter(stats);

  // 清除加载状态
  document.querySelectorAll('[aria-busy="true"]').forEach(function (el) {
    el.removeAttribute('aria-busy');
  });

  // 更新时间戳
  var now = new Date();
  document.getElementById('refresh-info').textContent = '更新于 ' + now.toLocaleTimeString('zh-CN');

  if (t0) {
    var elapsed = Math.round(performance.now() - t0);
    document.getElementById('refresh-info').textContent += ' (' + elapsed + 'ms)';
  }
}

// ─── 周期范围显示 ──────────────────────────────────
function renderPeriodRange(stats) {
  var el = document.getElementById('period-range');
  if (!el) return;
  var pl = stats && stats.period_label;
  if (pl && pl.start && pl.end) {
    el.textContent = '📅 ' + pl.label + '（' + pl.start + ' ~ ' + pl.end + '）';
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

// ─── KPI 卡片（带动画计数） ────────────────────────
function animateValue(el, startVal, endVal, suffix, isFloat) {
  if (!el) return;
  var duration = 800;
  var startTime = null;
  if (startVal === endVal || endVal == null || isNaN(endVal)) {
    el.textContent = endVal != null ? (isFloat ? fmtCost(endVal) : fmt(endVal)) : '—';
    return;
  }
  function step(timestamp) {
    if (!startTime) startTime = timestamp;
    var progress = Math.min((timestamp - startTime) / duration, 1);
    // easeOutCubic
    var eased = 1 - Math.pow(1 - progress, 3);
    var current = startVal + (endVal - startVal) * eased;
    if (isFloat) {
      el.textContent = '\xA5' + current.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } else {
      el.textContent = Math.round(current).toLocaleString() + (suffix || '');
    }
    if (progress < 1) { requestAnimationFrame(step); }
  }
  requestAnimationFrame(step);
}

function renderKPIs(stats, billing, compare) {
  if (!stats) {
    ['tokens', 'cache', 'cost', 'sessions'].forEach(function (k) {
      document.getElementById('kpi-' + k).textContent = '—';
      var sub = document.getElementById('kpi-' + k + '-sub');
      if (sub) sub.textContent = '';
    });
    return;
  }

  var elTokens = document.getElementById('kpi-tokens');
  var elCache = document.getElementById('kpi-cache');
  var elCost = document.getElementById('kpi-cost');
  var elSessions = document.getElementById('kpi-sessions');
  var elCostSub = document.getElementById('kpi-cost-sub');

  // 提取当前值
  var prevTokens = parseInt(elTokens.getAttribute('data-value')) || 0;
  var prevCost = parseFloat(elCost.getAttribute('data-value')) || 0;

  var newTokens = stats.total_tokens || 0;
  var newCost = stats.total_cost || 0;
  var cacheRate = stats.overall_cache_hit_rate;

  elTokens.setAttribute('data-value', newTokens);
  elCost.setAttribute('data-value', newCost);

  animateValue(elTokens, prevTokens, newTokens, '', false);
  elCache.textContent = cacheRate != null ? fmtPct(cacheRate) : '—';
  elCache.title = cacheRate != null ? (cacheRate * 100).toFixed(2) + '%' : '';
  animateValue(elCost, prevCost, newCost, '', true);
  elCost.title = newCost != null ? '\xA5' + newCost.toFixed(4) : '';
  elSessions.textContent = fmt(stats.session_count || 0);

  // ─── 周期对比增量 ───
  var tokensSub = document.getElementById('kpi-tokens-sub');
  var cacheSub  = document.getElementById('kpi-cache-sub');
  var sessionsSub = document.getElementById('kpi-sessions-sub');

  if (compare && compare.delta) {
    var d = compare.delta;

    // Token 增量
    if (tokensSub && d.total_tokens != null) {
      var arrow = d.total_tokens >= 0 ? '↑' : '↓';
      var cls = d.total_tokens >= 0 ? 'delta-up' : 'delta-down';
      tokensSub.innerHTML = '<span class="' + cls + '">' + arrow + ' ' + Math.abs(d.total_tokens * 100).toFixed(0) + '%</span> vs 上周期';
      tokensSub.style.display = '';
    }

    // 缓存命中率增量
    if (cacheSub && d.cache_hit_rate != null) {
      var diffPct = (d.cache_hit_rate * 100).toFixed(1);
      var arrow2 = d.cache_hit_rate >= 0 ? '↑' : '↓';
      var cls2 = d.cache_hit_rate >= 0 ? 'delta-up' : 'delta-down';
      cacheSub.innerHTML = '<span class="' + cls2 + '">' + arrow2 + ' ' + Math.abs(diffPct) + 'pp</span>';
      cacheSub.style.display = '';
    }

    // 费用增量
    if (d.total_cost != null) {
      var costArrow = d.total_cost >= 0 ? '↑' : '↓';
      var costCls = d.total_cost >= 0 ? 'delta-up' : 'delta-down';
      if (elCostSub) {
        elCostSub.innerHTML = '<span class="' + costCls + '">' + costArrow + ' ' + Math.abs(d.total_cost * 100).toFixed(0) + '%</span> vs 上周期';
      }
    } else {
      if (elCostSub) elCostSub.textContent = '';
    }

    // 会话数增量
    if (sessionsSub && d.session_count != null) {
      var sArrow = d.session_count >= 0 ? '↑' : '↓';
      var sCls = d.session_count >= 0 ? 'delta-up' : 'delta-down';
      sessionsSub.innerHTML = '<span class="' + sCls + '">' + sArrow + ' ' + Math.abs(d.session_count * 100).toFixed(0) + '%</span>';
      sessionsSub.style.display = '';
    }
  }

  // 费用对比（本地估算 vs 官方余额）
  if (billing && billing.total_official_spend != null) {
    document.getElementById('billing-indicator').style.display = '';
    document.getElementById('billing-total').textContent = fmtCost(billing.total_official_spend);
    if (!compare || !compare.delta || compare.delta.total_cost == null) {
      elCostSub.textContent = '官方: ' + fmtCost(billing.total_official_spend)
        + (billing.discrepancy_pct != null ? ' (偏差 ' + billing.discrepancy_pct.toFixed(1) + '%)' : '');
      if (billing.is_first_run) {
        elCostSub.textContent += ' · 首次运行中';
      }
    }
  }
}

// ─── 表格排序 ────────────────────────────────────
function makeSortHandler(tableId, stateKey, dataArr, renderFn) {
  return function(key) {
    if (SORT[stateKey].key === key) {
      SORT[stateKey].asc = !SORT[stateKey].asc;
    } else {
      SORT[stateKey].key = key;
      SORT[stateKey].asc = false; // 默认降序
    }
    renderFn(dataArr);

    // 更新 th 样式
    var thead = document.querySelector('#' + tableId + ' thead');
    if (thead) {
      thead.querySelectorAll('th[data-sort]').forEach(function(th) {
        var sk = th.getAttribute('data-sort');
        th.classList.remove('sort-asc', 'sort-desc');
        if (sk === SORT[stateKey].key) {
          th.classList.add(SORT[stateKey].asc ? 'sort-asc' : 'sort-desc');
        }
      });
    }
  };
}

function sortedModels(models) {
  var key = SORT.model.key;
  var asc = SORT.model.asc;
  return models.slice().sort(function(a, b) {
    var va = a[key] || 0, vb = b[key] || 0;
    if (key === 'model') {
      va = a.model || '';
      vb = b.model || '';
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return asc ? va - vb : vb - va;
  });
}

function sortedSessions(sessions) {
  var key = SORT.session.key;
  var asc = SORT.session.asc;
  return sessions.slice().sort(function(a, b) {
    var va = a[key] || 0, vb = b[key] || 0;
    if (key === 'timestamp' || key === 'project' || key === 'primary_model') {
      va = a[key] || '';
      vb = b[key] || '';
      return asc ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return asc ? va - vb : vb - va;
  });
}

// ─── 模型对比表 ────────────────────────────────────
function renderModelsTable(data) {
  var tbody = document.getElementById('model-tbody');
  if (!data || !data.models || !data.models.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-state">暂无数据</td></tr>';
    return;
  }

  var models = sortedModels(data.models);

  // 计算列最大值（用于行内柱状图）
  var maxInput = 0, maxOutput = 0, maxCache = 0, maxTokens = 0;
  models.forEach(function(m) {
    var t = (m.input || 0) + (m.cache_read || 0) + (m.output || 0);
    if (m.input > maxInput) maxInput = m.input;
    if (m.output > maxOutput) maxOutput = m.output;
    if (m.cache_read > maxCache) maxCache = m.cache_read;
    if (t > maxTokens) maxTokens = t;
  });

  var totalCost = 0;
  models.forEach(function(m) { totalCost += (m.cost || 0); });

  tbody.innerHTML = models.map(function (m) {
    var lowSample = m.count < 300;
    var hrDisplay = lowSample ? '≈' + fmtPct(m.cache_hit_rate) : fmtPct(m.cache_hit_rate);
    var costPct = totalCost > 0 ? ((m.cost || 0) / totalCost * 100).toFixed(1) : '0';
    var sourceBadges = '';
    if (m.source_main > 0) sourceBadges += '<span class="badge badge-main">主</span> ';
    if (m.source_subagent > 0) sourceBadges += '<span class="badge badge-subagent">子</span>';
    var totalM = (m.input || 0) + (m.cache_read || 0) + (m.output || 0);

    function bar(val, max, color) {
      if (max <= 0) return '';
      var pct = Math.round(val / max * 100);
      return '<span class="inline-bar" style="width:' + pct + '%;background:' + color + '"></span>';
    }

    return '<tr>' +
      '<td><strong>' + esc(m.model) + '</strong></td>' +
      '<td class="num-cell" title="' + (m.input || 0).toLocaleString() + '">' + bar(m.input, maxInput, 'var(--tl-accent)') + fmt(m.input) + '</td>' +
      '<td class="num-cell" title="' + (m.cache_read || 0).toLocaleString() + '">' + bar(m.cache_read, maxCache, '#3fb950') + fmt(m.cache_read) + '</td>' +
      '<td class="num-cell" title="' + (m.output || 0).toLocaleString() + '">' + bar(m.output, maxOutput, '#d29922') + fmt(m.output) + '</td>' +
      '<td class="num-cell" title="' + totalM.toLocaleString() + '">' + bar(totalM, maxTokens, '#a371f7') + fmt(totalM) + '</td>' +
      '<td class="' + (lowSample ? 'low-sample' : '') + '" title="' + (lowSample ? '样本较少仅供参考' : '') + '">' + hrDisplay + '</td>' +
      '<td class="num-cell" title="\xA5' + (m.cost || 0).toFixed(4) + '">' + fmtCost(m.cost) + '<span class="cost-pct">' + costPct + '%</span></td>' +
      '<td class="num-cell">' + fmt(m.count) + '</td>' +
      '<td>' + (sourceBadges || '—') + '</td>' +
      '</tr>';
  }).join('');
}

// ─── 会话表 ────────────────────────────────────────
var _sortModelHandler = null;
var _sortSessionHandler = null;

function renderSessionsTable(data) {
  var tbody = document.getElementById('session-tbody');
  if (!data || !data.sessions || !data.sessions.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">暂无会话数据</td></tr>';
    return;
  }

  var sessions = sortedSessions(data.sessions);

  // 初始化排序处理器
  if (!_sortModelHandler) {
    _sortModelHandler = makeSortHandler('model-table', 'model', data.models || [], function(arr) {
      _sortModelHandler.dataArr = arr;
      renderModelsTable({ models: arr });
    });
  }
  if (!_sortSessionHandler) {
    _sortSessionHandler = makeSortHandler('session-table', 'session', data.sessions || [], function(arr) {
      _sortSessionHandler.dataArr = arr;
      renderSessionsTable({ sessions: arr, total: arr.length });
    });
  }
  if (_sortSessionHandler) _sortSessionHandler.dataArr = data.sessions;

  var maxTokens = 0, maxCost = 0;
  sessions.forEach(function(s) {
    if (s.tokens > maxTokens) maxTokens = s.tokens;
    if (s.cost > maxCost) maxCost = s.cost;
  });

  tbody.innerHTML = sessions.map(function (s) {
    var modelDisplay = esc(s.primary_model || '');
    if (s.models_used && s.models_used.length > 1) {
      modelDisplay += ' <span class="model-extra">+' + (s.models_used.length - 1) + '</span>';
    }
    function bar(val, max, color) {
      if (max <= 0) return '';
      var pct = Math.round(val / max * 100);
      return '<span class="inline-bar" style="width:' + pct + '%;background:' + color + '"></span>';
    }
    return '<tr>' +
      '<td title="' + esc(s.timestamp || '') + '">' + fmtTs(s.timestamp) + '</td>' +
      '<td title="' + esc(s.cwd || '') + '">' + esc(s.project) + '</td>' +
      '<td>' + modelDisplay + '</td>' +
      '<td class="num-cell">' + fmt(s.msg_count || 0) + '</td>' +
      '<td class="num-cell" title="' + (s.tokens || 0).toLocaleString() + '">' + bar(s.tokens, maxTokens, 'var(--tl-accent)') + fmt(s.tokens) + '</td>' +
      '<td class="num-cell" title="\xA5' + (s.cost || 0).toFixed(4) + '">' + bar(s.cost, maxCost, '#d29922') + fmtCost(s.cost) + '</td>' +
      '<td><a href="#" onclick="alert(\'Session: ' + esc(s.short_id) + '\\nProject: ' + esc(s.project) + '\\nModels: ' + esc((s.models_used || []).join(', ')) + '\\nMessages: ' + (s.msg_count || 0) + '\\nTokens: ' + fmt(s.tokens) + '\\nCost: ' + fmtCost(s.cost) + '\');return false;" title="详情">📋</a></td>' +
      '</tr>';
  }).join('');
}

// ─── AI 建议 ───────────────────────────────────────
function renderAdvice(data) {
  var severity = document.getElementById('advice-severity');
  var card = document.getElementById('advice-card');
  var warnings = document.getElementById('advice-warnings');
  var privacy = document.getElementById('advice-privacy');

  if (!data) {
    severity.textContent = '⚠️ 无法加载建议';
    card.className = 'advice-card severity-normal';
    return;
  }
  severity.textContent = (data.severity || '') + (data.advice ? ' — ' + data.advice : '');
  card.className = 'advice-card severity-normal';
  if (data.severity) {
    if (data.severity.indexOf('🔴') !== -1) card.className = 'advice-card severity-danger';
    else if (data.severity.indexOf('🟡') !== -1) card.className = 'advice-card severity-warn';
    else if (data.severity.indexOf('💎') !== -1) card.className = 'advice-card severity-great';
  }
  warnings.innerHTML = (data.warnings || []).map(function (w) {
    return '<p style="margin:0.25rem 0;font-size:0.85rem">⚠️ ' + esc(w) + '</p>';
  }).join('');
  privacy.innerHTML = '';
  if (data.llm_enhanced && data.llm_advice) {
    privacy.innerHTML = '💡 AI: ' + esc(data.llm_advice) + '<br><small>由 DeepSeek API 生成</small>';
  } else if (data.llm_enhanced === false) {
    privacy.innerHTML = '<small>规则引擎模式</small>';
  }
}

// ─── 所有图表 ──────────────────────────────────────
function renderAllCharts(stats, trend, hourly, tools) {
  if (!hasChartJS()) {
    ['chart-trend', 'chart-model-pie', 'chart-heatmap', 'chart-tools', 'chart-cost-trend'].forEach(function (id) {
      var errEl = document.getElementById(id + '-error');
      if (errEl) { errEl.style.display = 'flex'; errEl.textContent = '⚠️ Chart.js CDN 加载失败'; }
    });
    return;
  }

  renderTrendChart(trend);
  renderModelPieChart(stats);
  renderHeatmapChart(hourly);
  renderToolsChart(tools);
  renderCostTrendChart(trend);
}

// ─── 每日 Token 趋势（堆叠柱状图） ──────────────────
function renderTrendChart(trend) {
  var daily = (trend && trend.daily) ? trend.daily : [];
  if (!daily.length) {
    var errEl = document.getElementById('chart-trend-error');
    if (errEl) { errEl.style.display = 'flex'; errEl.textContent = '暂无趋势数据'; }
    return;
  }

  var colors = getChartColors();
  var labels = daily.map(function (d) { return d.date.slice(5); });
  var inputData = daily.map(function (d) { return d.input || 0; });
  var cacheData = daily.map(function (d) { return d.cache_read || 0; });
  var outputData = daily.map(function (d) { return d.output || 0; });

  safeCreateChart('trend', 'chart-trend', {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        { label: '输入 Token', data: inputData, backgroundColor: colors[0] + 'CC', borderColor: colors[0], borderWidth: 1, borderRadius: 4 },
        { label: '缓存读取', data: cacheData, backgroundColor: colors[1] + 'CC', borderColor: colors[1], borderWidth: 1, borderRadius: 4 },
        { label: '输出 Token', data: outputData, backgroundColor: colors[2] + 'CC', borderColor: colors[2], borderWidth: 1, borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { padding: 16, usePointStyle: true, color: '#8b949e', font: { size: isMobile ? 10 : 11 } } },
        tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + ': ' + fmt(ctx.raw); } } },
      },
      scales: {
        x: { stacked: true, ticks: { color: '#8b949e', font: { size: isMobile ? 9 : 10 }, maxRotation: 45 }, grid: { color: '#30363d44' } },
        y: { stacked: true, ticks: { color: '#8b949e', font: { size: isMobile ? 9 : 10 }, callback: function (v) { return fmtShort(v); } }, grid: { color: '#30363d44' } },
      },
    },
  });
}

// ─── 模型用量饼图 ──────────────────────────────────
function renderModelPieChart(stats) {
  var models = (stats && stats.models) ? stats.models : [];
  if (!models.length) return;

  var colors = getChartColors();
  var top5 = models.slice(0, 5);
  var restTokens = 0;
  for (var i = 5; i < models.length; i++) {
    restTokens += (models[i].input || 0) + (models[i].cache_read || 0) + (models[i].output || 0);
  }

  var labels = top5.map(function (m) { return m.model; });
  var data = top5.map(function (m) {
    return (m.input || 0) + (m.cache_read || 0) + (m.output || 0);
  });

  if (restTokens > 0) { labels.push('其他'); data.push(restTokens); }

  safeCreateChart('modelPie', 'chart-model-pie', {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data, backgroundColor: colors,
        borderColor: '#0d1117', borderWidth: 2,
        hoverBorderColor: '#30363d',
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { padding: 14, usePointStyle: true, color: '#8b949e', font: { size: isMobile ? 10 : 11 } } },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
              var pct = total > 0 ? (ctx.raw / total * 100).toFixed(1) + '%' : '0%';
              return ctx.label + ': ' + fmt(ctx.raw) + ' (' + pct + ')';
            },
          },
        },
      },
    },
  });
}

// ─── 热力图（按小时的 Token 用量） ───
function renderHeatmapChart(hourly) {
  var hourLabels = ['0时','1时','2时','3时','4时','5时','6时','7时','8时','9时','10时','11时',
                    '12时','13时','14时','15时','16时','17时','18时','19时','20时','21时','22时','23时'];
  var data = (hourly && hourly.hourly && hourly.hourly.length === 24) ? hourly.hourly : new Array(24).fill(0);
  var maxVal = (hourly && hourly.max) ? hourly.max : (Math.max.apply(null, data) || 1);

  var colors = getChartColors();
  var accentRgb = hexToRgb(colors[0]) || { r: 88, g: 166, b: 255 };

  safeCreateChart('heatmap', 'chart-heatmap', {
    type: 'bar',
    data: {
      labels: hourLabels,
      datasets: [{
        label: 'Token 用量',
        data: data,
        backgroundColor: data.map(function (v) {
          var a = maxVal > 0 ? (0.15 + (v / maxVal) * 0.85) : 0.15;
          return 'rgba(' + accentRgb.r + ',' + accentRgb.g + ',' + accentRgb.b + ',' + Math.min(a, 1).toFixed(2) + ')';
        }),
        borderColor: colors[0] + '88',
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function (ctx) { return 'Token: ' + fmt(ctx.raw); } } },
      },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 9 }, maxRotation: 0, autoSkip: false }, grid: { color: '#30363d44' } },
        y: { ticks: { color: '#8b949e', font: { size: 9 }, callback: function (v) { return fmtShort(v); } }, grid: { color: '#30363d44' } },
      },
    },
  });
}

function hexToRgb(hex) {
  var m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return m ? { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) } : null;
}

// ─── 工具调用分布 ──────────────────────────────────
function renderToolsChart(tools) {
  var toolsList = (tools && tools.tools) ? tools.tools : [];
  var colors = getChartColors();

  if (!toolsList.length) {
    toolsList = [{ name: '暂无工具调用数据', count: 0 }];
  }

  var labels = toolsList.map(function (t) { return t.name; });
  var data = toolsList.map(function (t) { return t.count; });
  var bgColors = toolsList.map(function (_, i) { return colors[i % colors.length] + '99'; });
  var borderColors = toolsList.map(function (_, i) { return colors[i % colors.length]; });

  safeCreateChart('tools', 'chart-tools', {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: '调用次数',
        data: data,
        backgroundColor: bgColors,
        borderColor: borderColors,
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: function (ctx) { return '调用: ' + fmt(ctx.raw) + ' 次'; } } },
      },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: 9 } }, grid: { color: '#30363d44' } },
        y: { ticks: { color: '#8b949e', font: { size: 9 } }, grid: { display: false } },
      },
    },
  });
}

// ─── 每日费用趋势（折线+面积图） ───────────────────
function renderCostTrendChart(trend) {
  var daily = (trend && trend.daily) ? trend.daily : [];
  if (!daily.length) return;

  var colors = getChartColors();
  var labels = daily.map(function (d) { return d.date.slice(5); });
  var costData = daily.map(function (d) { return d.cost || 0; });
  var cumData = [];
  var cum = 0;
  costData.forEach(function (c) { cum += c; cumData.push(Math.round(cum * 100) / 100); });

  safeCreateChart('costTrend', 'chart-cost-trend', {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: '每日费用',
          data: costData,
          borderColor: colors[2],
          backgroundColor: colors[2] + '22',
          fill: true,
          tension: 0.3,
          pointRadius: isMobile ? 2 : 4,
          pointBackgroundColor: colors[2],
          yAxisID: 'y',
        },
        {
          label: '累计费用',
          data: cumData,
          borderColor: colors[0],
          backgroundColor: colors[0] + '11',
          fill: true,
          tension: 0.3,
          borderDash: [5, 5],
          pointRadius: isMobile ? 1 : 3,
          pointBackgroundColor: colors[0],
          yAxisID: 'y1',
        },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { padding: 16, usePointStyle: true, color: '#8b949e', font: { size: isMobile ? 10 : 11 } } },
        tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + ': ' + fmtCost(ctx.raw); } } },
      },
      scales: {
        x: { ticks: { color: '#8b949e', font: { size: isMobile ? 9 : 10 } }, grid: { color: '#30363d44' } },
        y: {
          type: 'linear', position: 'left',
          ticks: { color: colors[2], font: { size: isMobile ? 9 : 10 }, callback: function (v) { return fmtCost(v); } },
          grid: { color: '#30363d44' },
        },
        y1: {
          type: 'linear', position: 'right',
          ticks: { color: colors[0], font: { size: isMobile ? 9 : 10 }, callback: function (v) { return fmtCost(v); } },
          grid: { display: false },
        },
      },
    },
  });
}

// ─── 分享卡片 ──────────────────────────────────────
function updateShareCard(stats, period) {
  var section = document.getElementById('share-section');
  if (!stats || !stats.models || !stats.models.length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = 'block';

  var periodNames = { day: '今天', week: '过去7天', month: '过去30天', '3month': '过去90天', year: '过去一年' };

  document.getElementById('sc-period').textContent = periodNames[period] || period;
  document.getElementById('sc-tokens').textContent = fmt(stats.total_tokens || 0);
  document.getElementById('sc-cache').textContent = fmtPct(stats.overall_cache_hit_rate);
  document.getElementById('sc-cost').textContent = fmtCost(stats.total_cost || 0);
  document.getElementById('sc-model').textContent = esc(stats.models[0]?.model || '—');
}

function downloadShareCard() {
  try {
    var card = document.getElementById('share-card');
    var canvas = document.getElementById('share-canvas');
    var ctx = canvas.getContext('2d');

    var rect = card.getBoundingClientRect();
    var scale = 2;
    canvas.width = rect.width * scale;
    canvas.height = rect.height * scale;

    // 绘制背景
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // 绘制卡片样式边框
    ctx.strokeStyle = '#30363d';
    ctx.lineWidth = 2 * scale;
    ctx.beginPath();
    ctx.roundRect(4 * scale, 4 * scale, canvas.width - 8 * scale, canvas.height - 8 * scale, 12 * scale);
    ctx.stroke();

    // 复制文字
    var textLines = [];
    card.querySelectorAll('.sc-row').forEach(function (row) {
      var label = (row.children[0]?.textContent || '').trim();
      var val = (row.children[1]?.textContent || '').trim();
      if (label && val) textLines.push({ label: label, val: val });
    });

    var title = (card.querySelector('.sc-title')?.textContent || '').trim();

    ctx.fillStyle = '#58a6ff';
    ctx.font = 'bold ' + (18 * scale) + 'px "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(title, canvas.width / 2, 40 * scale);

    ctx.textAlign = 'left';
    var y = 80 * scale;
    textLines.forEach(function (line) {
      ctx.fillStyle = '#8b949e';
      ctx.font = (12 * scale) + 'px "Microsoft YaHei", sans-serif';
      ctx.fillText(line.label, 30 * scale, y);
      ctx.fillStyle = '#58a6ff';
      ctx.font = 'bold ' + (13 * scale) + 'px "Cascadia Code", monospace';
      ctx.fillText(line.val, 140 * scale, y);
      y += 28 * scale;
    });

    // 水印
    ctx.fillStyle = '#30363d33';
    ctx.font = (10 * scale) + 'px monospace';
    ctx.textAlign = 'right';
    ctx.fillText('TokenLens · ' + new Date().toISOString().slice(0, 10), canvas.width - 20 * scale, canvas.height - 12 * scale);

    // 下载
    var link = document.createElement('a');
    link.download = 'tokenlens-report-' + new Date().toISOString().slice(0, 10) + '.png';
    link.href = canvas.toDataURL('image/png');
    link.click();
    showToast('分享卡片已下载');
  } catch (e) {
    showToast('下载失败: ' + e.message, true);
  }
}

function copyShareText() {
  var stats = null;
  // 从现有 DOM 提取
  var rows = document.querySelectorAll('#share-card .sc-row');
  var text = '🔍 TokenLens 用量报告\n' + '━'.repeat(20) + '\n';
  rows.forEach(function (row) {
    var label = (row.children[0]?.textContent || '').trim();
    var val = (row.children[1]?.textContent || '').trim();
    if (label && val) text += label + ': ' + val + '\n';
  });

  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(function () {
      showToast('已复制到剪贴板');
    }).catch(function () {
      showToast('复制失败', true);
    });
  } else {
    showToast('浏览器不支持剪贴板', true);
  }
}

// ─── 更新页脚 ──────────────────────────────────────
function updateFooter(stats) {
  var el = document.getElementById('footer-stats');
  if (!stats) return;
  el.textContent = (stats.models || []).length + ' 个模型 · ' + (stats.period || STATE.period);
}

// ─── 主题管理 ──────────────────────────────────────
function switchTheme(theme) {
  document.documentElement.setAttribute('data-theme-tokens', theme);
  try { localStorage.setItem('tokenlens-theme', theme); } catch (e) {}
  document.getElementById('theme-select').value = theme;
  // 重建所有图表以适应新主题色
  setTimeout(function () {
    if (hasChartJS()) loadAll();
  }, 100);
}

function loadTheme() {
  try {
    var saved = localStorage.getItem('tokenlens-theme');
    if (saved) {
      document.documentElement.setAttribute('data-theme-tokens', saved);
      document.getElementById('theme-select').value = saved;
    }
  } catch (e) {}
}

// ─── 时间周期切换 ──────────────────────────────────
function switchPeriod(period) {
  if (STATE.period === period) return; // 避免重复加载
  STATE.period = period;

  document.querySelectorAll('.tab-bar button').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.period === period);
  });

  // 清除图表避免切换时闪烁
  destroyAllCharts();
  setLoading(true);
  loadAll();
}

// ─── 项目/来源切换 ──────────────────────────────────
function onProjectChange() {
  STATE.project = document.getElementById('project-select').value;
  destroyAllCharts();
  setLoading(true);
  loadAll();
}

function onSourceChange() {
  STATE.source = document.getElementById('source-select').value;
  destroyAllCharts();
  setLoading(true);
  loadAll();
}

// ─── 刷新 ──────────────────────────────────────────
async function refreshAll() {
  setLoading(true);
  var resp = await api('/api/refresh');
  if (resp) {
    destroyAllCharts();
    await loadAll();
    showToast('数据已刷新 (' + resp.records + ' 条记录)');
  }
}

function setLoading(loading) {
  var els = document.querySelectorAll('.kpi-card, .chart-card, #advice-card');
  els.forEach(function (el) {
    if (loading) el.setAttribute('aria-busy', 'true');
    else el.removeAttribute('aria-busy');
  });
}

// ─── 移动端引导 ────────────────────────────────────
async function setupMobileGuide() {
  try {
    var network = await api('/api/network', true);
    if (!network || !network.mobile_url) return;

    var guide = document.getElementById('mobile-guide');
    var urlDisplay = document.getElementById('mobile-url');
    var qrWrap = document.getElementById('qr-code');

    guide.style.display = 'block';
    urlDisplay.textContent = network.mobile_url;

    if (typeof QRCode !== 'undefined' && qrWrap) {
      qrWrap.innerHTML = '';
      new QRCode(qrWrap, {
        text: network.mobile_url,
        width: isMobile ? 140 : 180,
        height: isMobile ? 140 : 180,
        colorDark: '#1f2328',
        colorLight: '#ffffff',
      });
    }
  } catch (e) { /* 静默忽略 */ }
}

// ─── 工具函数 ──────────────────────────────────────
function esc(s) {
  if (!s) return '';
  var div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}

// ─── 窗口大小监听（只重建图表，不重新请求数据） ───
var resizeTimer;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    if (hasChartJS() && _cachedChartData) {
      destroyAllCharts();
      renderAllCharts(_cachedChartData.stats, _cachedChartData.trend, _cachedChartData.hourly, _cachedChartData.tools);
    }
  }, 500);
});

// ─── PWA 注册 ──────────────────────────────────────
if ('serviceWorker' in navigator) {
  // 简易离线缓存（可选）
}

// ─── 初始化表格排序 ────────────────────────────────
function initTableSort(tableId, key, handler, dataArr) {
  var table = document.getElementById(tableId);
  if (!table) return;
  var thead = table.querySelector('thead');
  if (!thead) return;
  thead.querySelectorAll('th[data-sort]').forEach(function(th) {
    th.style.cursor = 'pointer';
    th.addEventListener('click', function() {
      var sortKey = th.getAttribute('data-sort');
      if (sortKey) handler(sortKey);
    });
  });
  // 设置初始排序指示
  var activeTh = thead.querySelector('th[data-sort="' + SORT[key].key + '"]');
  if (activeTh) {
    activeTh.classList.add(SORT[key].asc ? 'sort-asc' : 'sort-desc');
  }
}

// ─── 初始化 ────────────────────────────────────────
async function init() {
  loadTheme();

  // 加载项目列表
  var health = await api('/api/health', true);
  if (health && health.projects_list) {
    var select = document.getElementById('project-select');
    health.projects_list.forEach(function (proj) {
      var opt = document.createElement('option');
      opt.value = proj; opt.textContent = proj;
      select.appendChild(opt);
    });
  }

  // 移动端引导（异步，不阻塞）
  setupMobileGuide();

  // 首屏加载
  await loadAll();

  // 初始化表格排序（在数据加载后）
  initTableSort('model-table', 'model', _sortModelHandler || function(){});
  initTableSort('session-table', 'session', _sortSessionHandler || function(){});
}

// 启动
document.addEventListener('DOMContentLoaded', init);
