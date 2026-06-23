/* ===== 林念念 Bot — 客户端配置 ===== */
/* 服务器地址配置。内测期：HTTP + 公网 IP；生产换 HTTPS + 域名。
 *
 * 优先级：
 *   1. localStorage['server_base'] （用户在设置页手填，开发调试用）
 *   2. window.APP_CONFIG.server_base （托管时注入，见 server.py StaticFiles)
 *   3. Native/Browser 自动检测
 */
(function() {
  window.APP_CONFIG = window.APP_CONFIG || {};

  // 用户手动覆盖（localStorage 优先）
  var userOverride = localStorage.getItem('server_base');
  if (userOverride) {
    window.APP_CONFIG.server_base = userOverride.replace(/\/+$/, '');
    return;
  }

  // 预注入的默认值（构建时替换 <服务器公网IP>）
  if (!window.APP_CONFIG.server_base) {
    // 直接检测原生注入的 NativeBridge（addJavascriptInterface 注入，
    // 一定先于任何 <script> 存在），不依赖 native.js 是否已加载/执行时序。
    // 修复：12 个页面未加载 native.js 导致 window.NativeApp 不存在 →
    // 走 location.origin(=file://) 分支 → API 请求打到 file:///api/v1/ 全失败。
    var _isNative = false;
    try {
      if (window.NativeBridge && typeof window.NativeBridge.isNative === 'function') {
        _isNative = window.NativeBridge.isNative();
      }
    } catch (e) { _isNative = false; }
    if (_isNative || (window.NativeApp && window.NativeApp.isNative)) {
      // APK 内：默认指向公网服务器
      window.APP_CONFIG.server_base = 'http://129.211.7.67:8766';
    } else {
      // 浏览器调试：同源（页面从 8766 来，API 也走 8766）
      window.APP_CONFIG.server_base = location.origin;
    }
  }
})();
