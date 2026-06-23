/* ===== 林念念 Bot — 原生平台层 =====
 *
 * 职责：
 *   1. 检测是否运行在 Android 原生 WebView 环境
 *   2. 为 <html> 注入 data-platform 属性 → CSS 响应式覆盖
 *   3. 注入 window.NativeApp API 供其他模块调用
 *
 * 原生 WebView 会注入 window.NativeBridge 对象（通过 addJavascriptInterface）
 * 加载时机：必须最先加载（在所有其他 JS 之前）
 *
 * 返回键行为：
 *   由 MainActivity.onBackPressed() 统一管理：
 *     webView.canGoBack() → goBack()
 *     else → 双击退出（Toast + 2s 计时器）
 *   JS 层不干预 history 栈。
 */

(function() {
  'use strict';

  // ── 平台检测 ──
  var _isNative = false;
  try {
    if (window.NativeBridge && typeof window.NativeBridge.isNative === 'function') {
      _isNative = window.NativeBridge.isNative();
    }
  } catch (e) {
    _isNative = false;
  }

  // ── 注入全局 API ──
  window.NativeApp = {
    isNative: _isNative,
    platform: _isNative ? 'android' : 'browser',
  };

  // ── 响应式标记：<html data-platform="native"> ──
  if (_isNative) {
    document.documentElement.setAttribute('data-platform', 'native');

    // viewport-fit=cover（全面屏安全区域必需）
    var vp = document.querySelector('meta[name="viewport"]');
    if (vp) {
      var vpContent = vp.getAttribute('content') || '';
      if (vpContent.indexOf('viewport-fit=cover') === -1) {
        vp.setAttribute('content', vpContent + ', viewport-fit=cover');
      }
    }
  }

  // ── 导航辅助 ──
  window.NativeApp.navigateReplace = function(url) {
    location.replace(url);
  };

  window.NativeApp.navigate = function(url) {
    location.href = url;
  };

  window.NativeApp.goBack = function() {
    // history.length > 1 表示至少有一个可返回的页面（当前页不算）
    if (window.history.length > 1) {
      window.history.back();
    } else {
      // 无更多历史 → 委托原生层处理退出（双击退出）
      if (window.NativeBridge && typeof window.NativeBridge.exitApp === 'function') {
        window.NativeBridge.exitApp();
      }
    }
  };

  // ── 原生层回调钩子（MainActivity 在无历史可回退时调用）──
  window._nativeBackPressed = function() {
    // 原生层已处理双击退出 + Toast，此处仅做 JS 侧清理（如有）
    // 各页面可覆盖此函数实现自定义行为
  };

  console.log('[Native] 平台: ' + (window.NativeApp.isNative ? 'native(android)' : 'browser'));
})();
