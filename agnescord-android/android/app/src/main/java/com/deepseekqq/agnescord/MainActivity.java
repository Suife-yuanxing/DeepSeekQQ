package com.deepseekqq.agnescord;

import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.TextView;
import android.widget.Toast;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.splashscreen.SplashScreen;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsControllerCompat;

/**
 * 林念念 Bot — 原生 Android MainActivity
 *
 * 使用原生 WebView 加载本地 HTML 资产（assets/），
 * 无需 Capacitor/Cordova 等第三方桥接框架。
 */
public class MainActivity extends AppCompatActivity {

    private static final String TAG = "Agnescord";
    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // ── MUST be called before super.onCreate() for SplashScreen API ──
        SplashScreen.installSplashScreen(this);
        super.onCreate(savedInstanceState);

        // ── Edge-to-edge immersive mode ──
        Window window = getWindow();
        WindowCompat.setDecorFitsSystemWindows(window, false);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setStatusBarColor(android.graphics.Color.TRANSPARENT);
            window.setNavigationBarColor(android.graphics.Color.TRANSPARENT);
        }

        WindowInsetsControllerCompat controller =
            WindowCompat.getInsetsController(window, window.getDecorView());
        if (controller != null) {
            controller.setAppearanceLightStatusBars(false);  // dark status bar icons for light bg
        }

        // ── Create WebView with fallback for devices without WebView ──
        try {
            webView = new WebView(this);
        } catch (Exception e) {
            Log.e(TAG, "WebView creation failed — device may not have Android System WebView", e);
            // Fallback: show error message
            TextView errorView = new TextView(this);
            errorView.setText("需要 Android System WebView 才能运行\n\n请前往应用商店更新 WebView");
            errorView.setTextSize(16);
            errorView.setPadding(48, 48, 48, 48);
            errorView.setTextColor(0xFF1A1A2E);
            errorView.setBackgroundColor(0xFFFFF5F6);
            errorView.setGravity(android.view.Gravity.CENTER);
            setContentView(errorView);
            Toast.makeText(this, "请安装/启用 Android System WebView", Toast.LENGTH_LONG).show();
            return;
        }

        setContentView(webView);
        configureWebView(webView);

        // ── Add NativeBridge JS Interface ──
        webView.addJavascriptInterface(new NativeBridge(), "NativeBridge");

        // ── Back button handling (Android 16 dispatcher) ──
        setupBackHandling();

        // ── Load entry page ──
        webView.loadUrl("file:///android_asset/启动页.html");
    }

    // ────────────────────────────────────────
    //  WebView Configuration
    // ────────────────────────────────────────
    private void configureWebView(WebView wv) {
        WebSettings settings = wv.getSettings();

        // JavaScript
        settings.setJavaScriptEnabled(true);

        // DOM storage (required for localStorage JWT tokens)
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);

        // Viewport: match screen width
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);

        // Media
        settings.setMediaPlaybackRequiresUserGesture(false);

        // Mixed content: allow HTTP resources inside file:// page
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        // Allow file access (for assets)
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);

        // Text zoom: prevent system font size from breaking layout
        settings.setTextZoom(100);

        // Enable WebGL / hardware acceleration
        wv.setLayerType(android.view.View.LAYER_TYPE_HARDWARE, null);

        // ── Remote debugging (chrome://inspect) — diagnostic for white-screen issues ──
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.KITKAT) {
            WebView.setWebContentsDebuggingEnabled(true);
        }

        // Smooth scrolling
        wv.setOverScrollMode(WebView.OVER_SCROLL_NEVER);
        wv.setVerticalScrollBarEnabled(false);
        wv.setHorizontalScrollBarEnabled(false);

        // ── WebViewClient: keep navigation in-app ──
        wv.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // API < 24: let WebView handle all navigation in-app
                return false;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // API 24+: let WebView handle all navigation in-app
                // Return false = "WebView, you handle this URL yourself"
                // (Returning true + loadUrl causes redirect loops with location.replace)
                return false;
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                Log.e(TAG, "WebView error [" + errorCode + "]: " + description + " — " + failingUrl);
                showLoadError(view, "错误 " + errorCode + ": " + description, failingUrl);
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, android.webkit.WebResourceResponse errorResponse) {
                // API 23+: HTTP errors (e.g. 404 for a missing asset) are NOT reported by onReceivedError
                Log.e(TAG, "WebView HTTP error " + errorResponse.getStatusCode() + " — " + request.getUrl());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                Log.i(TAG, "onPageFinished: " + url);
                // Signal to JS that we're native + dump diagnostic state to logcat
                view.evaluateJavascript(
                    "if(window.NativeApp){window.NativeApp.isNative=true;window.NativeApp.platform='android';" +
                    "document.documentElement.setAttribute('data-platform','native');}" +
                    // Diagnostic: log JS environment so white-screen root cause surfaces in logcat
                    "console.log('[DIAG] url=' + location.href + ' API=' + (typeof API) + ' NativeApp=' + (typeof NativeApp) + " +
                    "' APP_CONFIG=' + (window.APP_CONFIG?window.APP_CONFIG.server_base:'undef') + " +
                    "' bodyHas=' + (document.body?document.body.children.length:'nobody'));",
                    null);
            }
        });

        // ── WebChromeClient: console logs, dialogs ──
        wv.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage cm) {
                android.util.Log.d("WebView", cm.message());
                return true;
            }
        });
    }

    // ────────────────────────────────────────
    //  Load Error Fallback (any page, not just index.html)
    // ────────────────────────────────────────
    private void showLoadError(WebView view, String title, String failingUrl) {
        // Skip non-main-resource errors (images/css/js) so a missing asset
        // doesn't blank the whole page — only blank if the HTML itself failed.
        if (failingUrl != null && !failingUrl.endsWith(".html")) {
            return;
        }
        Log.e(TAG, "Showing load-error fallback for: " + failingUrl);
        view.loadUrl("about:blank");
        view.evaluateJavascript(
            "document.body.innerHTML='<div style=\"padding:48px 24px;text-align:center;font-family:sans-serif;color:#3A2030;background:#FFF5F6;min-height:100vh;box-sizing:border-box\">" +
            "<h2 style=\"margin:0 0 12px\">页面加载失败</h2>" +
            "<p style=\"color:#6A5060;font-size:14px;margin:0 0 8px\">" + title + "</p>" +
            "<p style=\"color:#999;font-size:12px;margin:0 0 24px;word-break:break-all\">" + failingUrl + "</p>" +
            "<button onclick=\"location.reload()\" style=\"padding:12px 28px;border-radius:16px;border:none;background:#F472B6;color:#fff;font-size:14px;font-weight:700;cursor:pointer\">重试</button>" +
            "</div>';", null);
    }

    // ────────────────────────────────────────
    //  NativeBridge: exposed to JS as window.NativeBridge
    // ────────────────────────────────────────
    public class NativeBridge {

        @JavascriptInterface
        public boolean isNative() {
            return true;
        }

        @JavascriptInterface
        public String getPlatform() {
            return "android";
        }

        @JavascriptInterface
        public void exitApp() {
            runOnUiThread(() -> {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                    finishAndRemoveTask();
                } else {
                    finish();
                }
                System.exit(0);
            });
        }

        /**
         * Called by JS when WebView has no more history to go back.
         * Delegates to the back dispatcher (root page → minimize, not exit).
         */
        @JavascriptInterface
        public void onBackPressed() {
            runOnUiThread(() -> getOnBackPressedDispatcher().onBackPressed());
        }
    }

    // ────────────────────────────────────────
    //  Back Button Handling — Android 16 (SDK 36) robust handling
    //  使用 OnBackPressedDispatcher（现代方式，替代已废弃的 onBackPressed）：
    //    有历史 → webView.goBack()
    //    无历史（根页面）→ moveTaskToBack(true) 退到后台，不杀进程
    //  解决"侧边返回直接退出应用"：根页面返回键改为最小化而非退出。
    // ────────────────────────────────────────
    private void setupBackHandling() {
        getOnBackPressedDispatcher().addCallback(this, new androidx.activity.OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (webView != null && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    // 根页面：退到后台而非退出（避免误触退出 + 解决手势双触发直接退出）
                    moveTaskToBack(true);
                }
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.loadUrl("about:blank");
            webView.clearHistory();
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
