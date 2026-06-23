/* ===== 林念念 Bot — 客户端配置 ===== */
/* 服务器地址配置。内测期：HTTP + 公网 IP；生产换 HTTPS + 域名。
 *
 * 优先级：
 *   1. localStorage['server_base'] （用户在设置页手填，开发调试用）
 *   2. window.APP_CONFIG.server_base （Capacitor 打包时注入，见 capacitor.config.json）
 *   3. 同源（浏览器直接访问 8766 时，API 和页面同源）
 */
(function() {
  window.APP_CONFIG = window.APP_CONFIG || {};

  // 用户手动覆盖（localStorage 优先）
  var userOverride = localStorage.getItem('server_base');
  if (userOverride) {
    window.APP_CONFIG.server_base = userOverride.replace(/\/+$/, '');
    return;
  }

  // Capacitor 打包注入的默认值（构建时替换 <服务器公网IP>）
  if (!window.APP_CONFIG.server_base) {
    if (window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform()) {
      // APK 内：默认指向公网服务器（构建时填入真实 IP）
      window.APP_CONFIG.server_base = 'http://49.232.195.125:8766';
    } else {
      // 浏览器调试：同源（页面从 8766 来，API 也走 8766）
      window.APP_CONFIG.server_base = location.origin;
    }
  }
})();
