/* ===== 林念念 Bot — Capacitor 平台层 =====
 *
 * 职责：
 *   1. 检测是否运行在 Capacitor 原生环境
 *   2. 为 <html> 注入 data-platform 属性 → CSS 响应式覆盖
 *   3. Android 返回键拦截（防止意外退出，Double-tap-to-exit）
 *   4. 注入 window.NativeApp API 供其他模块调用
 *
 * 加载时机：必须最先加载（在所有其他 JS 之前）
 */

(function() {
  'use strict';

  // ── 平台检测 ──
  var _isNative = false;
  try {
    _isNative = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  } catch (e) {
    _isNative = false;
  }

  // ── 注入全局 API ──
  window.NativeApp = {
    isNative: _isNative,
    platform: _isNative ? (window.Capacitor.getPlatform ? window.Capacitor.getPlatform() : 'android') : 'browser',
  };

  // ── 响应式标记：<html data-platform="capacitor"> ──
  if (_isNative) {
    document.documentElement.setAttribute('data-platform', 'capacitor');

    // 修复 iOS Safari 安全区域（预留）
    if (window.Capacitor.getPlatform && window.Capacitor.getPlatform() === 'ios') {
      document.documentElement.setAttribute('data-platform', 'capacitor ios');
    }

    // ── viewport-fit=cover（iOS 安全区域必需）──
    var vp = document.querySelector('meta[name="viewport"]');
    if (vp) {
      var vpContent = vp.getAttribute('content') || '';
      if (vpContent.indexOf('viewport-fit=cover') === -1) {
        vp.setAttribute('content', vpContent + ', viewport-fit=cover');
      }
    }

    // ── Capacitor StatusBar 适配（如可用） ──
    if (window.Capacitor.Plugins && window.Capacitor.Plugins.StatusBar) {
      try {
        window.Capacitor.Plugins.StatusBar.setOverlaysWebView({ overlay: true });
        window.Capacitor.Plugins.StatusBar.setStyle({ style: 'DARK' });
      } catch (e) { /* 忽略 */ }
    }
  }

  // ── Android 返回键拦截 ──
  // WebView 中 Android 返回键触发 popstate。策略：
  //   1. 每个页面加载时 push 一个虚拟 state（防止 history 为空时直接退出）
  //   2. 监听 popstate → 如果即将退出，显示 toast 并 push 回一个 state（Double-tap）
  //   3. 页面正常跳转使用 location.href（浏览器自动管理 history）
  var _exitToastTimer = null;

  function _showExitToast() {
    if (window.showToast) {
      window.showToast('再按一次退出林念念', 'warning');
    }
  }

  if (_isNative) {
    // 页面加载完成时，确保 history 至少有一个状态
    // 这样用户按返回键不会直接退出，而是先触发 popstate
    var _currentPage = location.pathname.split('/').pop() || 'index.html';

    // 如果 history 长度 <= 1，push 一个保护状态
    if (window.history.length <= 1) {
      window.history.pushState({ page: _currentPage, nav: true }, '', location.href);
    }

    // 全局 popstate 监听
    window.addEventListener('popstate', function(e) {
      if (e.state && e.state.nav) {
        // 这是我们的保护状态被 pop 了 → 用户到了 history 底部
        // 重新 push 保护状态，显示退出提示
        window.history.pushState({ page: _currentPage, nav: true }, '', location.href);

        if (_exitToastTimer) {
          // 第二次按返回 → 真正退出
          clearTimeout(_exitToastTimer);
          _exitToastTimer = null;
          // 移除保护状态，允许退出
          window.history.back();
        } else {
          _showExitToast();
          _exitToastTimer = setTimeout(function() {
            _exitToastTimer = null;
          }, 2000);
        }
      }
      // 如果 state 为 null（浏览器原生 history），让它正常回退
    });

    // 监听 Capacitor 的 backButton 事件（如果 @capacitor/app 已安装）
    if (window.Capacitor && window.Capacitor.EventListener) {
      try {
        window.Capacitor.EventListener.addListener('backButton', function() {
          // 如果 history 长度 > 1，正常回退
          if (window.history.length > 2) {
            window.history.back();
          } else {
            // 根页面：double-tap to exit
            if (_exitToastTimer) {
              clearTimeout(_exitToastTimer);
              _exitToastTimer = null;
              // 允许退出
              if (window.Capacitor.Plugins && window.Capacitor.Plugins.App) {
                window.Capacitor.Plugins.App.exitApp();
              }
            } else {
              _showExitToast();
              _exitToastTimer = setTimeout(function() {
                _exitToastTimer = null;
              }, 2000);
            }
          }
        });
      } catch (e) { /* @capacitor/app 未安装，忽略 */ }
    }
  }

  // ── 导航辅助 ──
  // 替换当前 history entry（不产生回退点）
  // 用于：启动页→登录页、登录页→首页 等不应回退的跳转
  window.NativeApp.navigateReplace = function(url) {
    location.replace(url);
  };

  // 正常导航（产生回退点）
  window.NativeApp.navigate = function(url) {
    location.href = url;
  };

  // 返回上一页
  window.NativeApp.goBack = function() {
    if (window.history.length > 2) {
      window.history.back();
    } else {
      // 根页面：提示退出
      if (_exitToastTimer) {
        clearTimeout(_exitToastTimer);
        _exitToastTimer = null;
        if (_isNative && window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.App) {
          window.Capacitor.Plugins.App.exitApp();
        }
      } else {
        _showExitToast();
        _exitToastTimer = setTimeout(function() {
          _exitToastTimer = null;
        }, 2000);
      }
    }
  };

  console.log('[Capacitor] 平台: ' + (window.NativeApp.isNative ? 'native(' + window.NativeApp.platform + ')' : 'browser'));
})();
