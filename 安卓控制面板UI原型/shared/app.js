/* ===== 林念念 Bot — Shared JavaScript ===== */
/* Theme Sync + Ripple + Toast + Bottom Nav + Utils + Back Navigation */
(function() {
  'use strict';

  /* === Back Navigation (global) === */
  // 所有 .title-bar .back 按钮统一走 NativeApp.goBack()
  document.addEventListener('click', function(e) {
    var backBtn = e.target.closest('.title-bar .back');
    if (backBtn) {
      e.preventDefault();
      if (window.NativeApp) {
        window.NativeApp.goBack();
      } else {
        window.history.back();
      }
    }
  });

  /* === Click Ripple Effect === */
  document.addEventListener('click', function(e) {
    var r = document.createElement('div');
    r.className = 'ripple';
    r.style.left = e.clientX + 'px';
    r.style.top = e.clientY + 'px';
    r.style.width = r.style.height = '14px';
    document.body.appendChild(r);
    r.addEventListener('animationend', function() { r.remove(); });
  });

  /* === Theme Sync (localStorage) === */
  function syncTheme() {
    var phone = document.querySelector('.phone');
    if (!phone) return;
    var t = localStorage.getItem('theme');
    if (t === 'dark') {
      phone.classList.add('dark-mode');
    } else if (t === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      phone.classList.add('dark-mode');
    }
  }
  syncTheme();

  /* === Font Size Sync (localStorage) === */
  function syncFontSize() {
    var phone = document.querySelector('.phone');
    if (!phone) return;
    var size = localStorage.getItem('fontSize') || '中';
    phone.setAttribute('data-font-size', size);
  }
  syncFontSize();

  /* Listen for font size changes from other tabs */
  window.addEventListener('storage', function(e) {
    if (e.key === 'fontSize') {
      var phone = document.querySelector('.phone');
      if (phone) phone.setAttribute('data-font-size', e.newValue || '中');
    }
  });

  /* === Chat Background Sync (localStorage) — 全局同步所有页面 === */
  var CHAT_BG_KEY = 'chatBg';
  function applyChatBgGlobal(bgData, phone, bgEl) {
    // Reset
    phone.style.background = '';
    if (!phone.classList.contains('dark-mode')) phone.style.background = '#FFF5F7';
    if (bgEl) {
      bgEl.style.backgroundImage = '';
      bgEl.style.backgroundColor = '';
      bgEl.style.opacity = '';
      bgEl.style.backgroundSize = '';
      bgEl.style.backgroundPosition = '';
    }
    if (!bgData || bgData.type === 'default' || !bgData.value) return;

    if (bgData.type === 'custom') {
      phone.style.background = 'url(' + bgData.value + ') center/cover no-repeat';
      if (bgEl) {
        bgEl.style.backgroundImage = 'url(' + bgData.value + ')';
        bgEl.style.backgroundSize = 'cover';
        bgEl.style.backgroundPosition = 'center';
        bgEl.style.opacity = '0.18';
      }
    } else if (bgData.type === 'dark') {
      phone.style.background = bgData.value;
      if (bgEl) { bgEl.style.backgroundColor = bgData.value; bgEl.style.opacity = '0.08'; }
    } else {
      phone.style.background = bgData.value;
      if (bgEl) { bgEl.style.backgroundImage = bgData.value; bgEl.style.opacity = '0.12'; }
    }
  }

  function loadChatBgGlobal() {
    var phone = document.querySelector('.phone');
    if (!phone) return;
    try {
      var raw = localStorage.getItem(CHAT_BG_KEY);
      if (raw) {
        applyChatBgGlobal(JSON.parse(raw), phone, document.getElementById('chatBg'));
      } else {
        // No saved bg — ensure default
        phone.style.background = '';
        if (!phone.classList.contains('dark-mode')) phone.style.background = '#FFF5F7';
      }
    } catch(e) {}
  }
  loadChatBgGlobal();

  // Background change from other tabs
  window.addEventListener('storage', function(e) {
    if (e.key === CHAT_BG_KEY) {
      var phone = document.querySelector('.phone');
      if (!phone) return;
      try {
        if (e.newValue) {
          applyChatBgGlobal(JSON.parse(e.newValue), phone, document.getElementById('chatBg'));
        } else {
          phone.style.background = '';
          if (!phone.classList.contains('dark-mode')) phone.style.background = '#FFF5F7';
        }
      } catch(err) {}
    }
  });

  /* Listen for system theme changes (auto mode) */
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
      var phone = document.querySelector('.phone');
      if (!phone) return;
      var t = localStorage.getItem('theme');
      if (t === 'auto') {
        if (e.matches) { phone.classList.add('dark-mode'); }
        else { phone.classList.remove('dark-mode'); }
      }
    });
  }

  /* === Bottom Nav — v9 Capsule + Directional Stretch + ARIA + Reduced Motion === */
  function initBottomNav() {
    var nav = document.getElementById('bottomNav');
    if (!nav) return;

    // --- Accessibility: ARIA attributes on nav container ---
    nav.setAttribute('role', 'navigation');
    nav.setAttribute('aria-label', '主要导航');

    // --- Detect reduced-motion preference ---
    var prefersReduced = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // --- Create capsule + 2 smear trail elements ---
    var capsule = document.createElement('div');
    capsule.className = 'nav-capsule';
    capsule.setAttribute('aria-hidden', 'true');

    var trail1 = document.createElement('div');
    trail1.className = 'nav-capsule-trail t1';
    trail1.setAttribute('aria-hidden', 'true');

    var trail2 = document.createElement('div');
    trail2.className = 'nav-capsule-trail t2';
    trail2.setAttribute('aria-hidden', 'true');

    nav.appendChild(trail2);
    nav.appendChild(trail1);
    nav.appendChild(capsule);

    // --- Track current capsule bounds for directional stretch ---
    var currentBounds = null;

    // --- Helper: set position/size on all 3 layers ---
    function setAllPos(l, t, w, h) {
      capsule.style.left = l + 'px';
      capsule.style.top = t + 'px';
      capsule.style.width = w + 'px';
      capsule.style.height = h + 'px';
      trail1.style.left = l + 'px';
      trail1.style.top = t + 'px';
      trail1.style.width = w + 'px';
      trail1.style.height = h + 'px';
      trail2.style.left = l + 'px';
      trail2.style.top = t + 'px';
      trail2.style.width = w + 'px';
      trail2.style.height = h + 'px';
    }

    // --- Capsule bounds: wrap entire icon + text with 3px padding ---
    function getItemBounds(item) {
      var navRect = nav.getBoundingClientRect();
      var itemRect = item.getBoundingClientRect();
      return {
        left: itemRect.left - navRect.left + 3,
        width: itemRect.width - 6,
        top: itemRect.top - navRect.top + 3,
        height: itemRect.height - 4
      };
    }

    // --- Disable CSS transitions (for instant snap) ---
    function noTransition() {
      capsule.style.transition = 'none';
      trail1.style.transition = 'none';
      trail2.style.transition = 'none';
    }

    // --- Restore CSS transitions ---
    function yesTransition() {
      capsule.style.transition = '';
      trail1.style.transition = '';
      trail2.style.transition = '';
    }

    // --- Instant snap (no animation) — used for init + reduced-motion ---
    function snapCapsule(item) {
      var b = getItemBounds(item);
      noTransition();
      setAllPos(b.left, b.top, b.width, b.height);
      capsule.offsetHeight; // force reflow
      yesTransition();
      currentBounds = b;
    }

    // ============================================================
    //  Directional Stretch Animation — 长→短 效果
    //  Phase 1 (CSS transition): 胶囊滑向目标 + 同时拉伸至 1.5×
    //  Phase 2 (setTimeout):     到位后压缩至正常宽度
    //  Stretch bias: 向右滑 → 右端多拉 | 向左滑 → 左端多拉
    // ============================================================
    var STRETCH_RATIO = 1.5;       // 拉伸倍率
    var SLIDE_DURATION = 380;      // 滑动时长 (ms) — 与 CSS left transition 一致
    var COMPRESS_DURATION = 420;   // 压缩时长 (ms) — 与 CSS width spring transition 一致
    var stretchTimer = null;       // Phase 2 定时器句柄
    var completeTimer = null;      // onComplete 延迟句柄

    function animateCapsule(item, onComplete) {
      // Cancel any in-flight stretch→compress sequence + pending callback
      if (stretchTimer) { clearTimeout(stretchTimer); stretchTimer = null; }
      if (completeTimer) { clearTimeout(completeTimer); completeTimer = null; }

      var b = getItemBounds(item);

      // Reduced motion → instant snap only
      if (prefersReduced) {
        snapCapsule(item);
        if (onComplete) { completeTimer = setTimeout(onComplete, 50); }
        return;
      }

      // --- Calculate directional stretch ---
      var stretchW = b.width * STRETCH_RATIO;
      var stretchL;
      if (currentBounds) {
        var moveDir = b.left - currentBounds.left;
        if (Math.abs(moveDir) < 4) {
          // Barely moving → center stretch
          stretchL = b.left - (stretchW - b.width) / 2;
        } else if (moveDir > 0) {
          // Moving right → bias stretch to the right (right end pulls forward)
          stretchL = b.left - (stretchW - b.width) * 0.2;
        } else {
          // Moving left → bias stretch to the left (left end pulls forward)
          stretchL = b.left - (stretchW - b.width) * 0.8;
        }
      } else {
        stretchL = b.left - (stretchW - b.width) / 2;
      }

      // Phase 1 — slide to target + expand to stretched (CSS transition)
      yesTransition();
      setAllPos(stretchL, b.top, stretchW, b.height);

      // Phase 2 — after slide lands, compress to final size
      stretchTimer = setTimeout(function() {
        stretchTimer = null;
        setAllPos(b.left, b.top, b.width, b.height);
        currentBounds = b;

        // onComplete fires AFTER compression CSS transition finishes
        // This ensures navigation and capsule animation end at exactly the same moment
        if (onComplete) {
          completeTimer = setTimeout(function() {
            completeTimer = null;
            onComplete();
          }, COMPRESS_DURATION);
        }
      }, SLIDE_DURATION);
    }

    // --- Robust initial snap: wait for full page load + triple rAF + delay fallback ---
    // window.load fires after ALL resources (images/fonts/styles/layout) are complete.
    // This is more reliable than document.fonts.ready which doesn't track system fonts.
    // Extra setTimeout(120ms) catches any late font substitution / text shaping.
    function initSnap() {
      var activeItem = nav.querySelector('.nav-item.active') || nav.querySelector('.nav-item');
      if (!activeItem) return;
      activeItem.setAttribute('aria-current', 'page');
      // Triple rAF ensures layout → paint → composite are all settled
      requestAnimationFrame(function() {
        requestAnimationFrame(function() {
          requestAnimationFrame(function() {
            snapCapsule(activeItem);
          });
        });
      });
    }

    // --- Setup ARIA on nav items ---
    var items = nav.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var label = (item.textContent || '').trim();
      if (label) item.setAttribute('aria-label', label);
    }

    // Primary: window.load (fonts + images + layout fully rendered)
    if (document.readyState === 'complete') {
      // Page already loaded → add delay for any late font substitution
      setTimeout(function() { initSnap(); }, 120);
    } else {
      window.addEventListener('load', function() {
        // Extra delay after load for font shaping / text layout to stabilize
        setTimeout(function() { initSnap(); }, 120);
      });
    }

    // --- ResizeObserver: recalibrate capsule when nav layout changes ---
    if (window.ResizeObserver) {
      var resizeObserver = new ResizeObserver(function() {
        var activeItem = nav.querySelector('.nav-item.active');
        if (activeItem) {
          snapCapsule(activeItem);
        }
      });
      resizeObserver.observe(nav);
    }

    // --- Click handler: stretch → spring → update ARIA → navigate ---
    for (var i = 0; i < items.length; i++) {
      items[i].addEventListener('click', function(e) {
        var href = this.getAttribute('href');
        if (!href || href === '#') return;

        // Already active → navigate normally
        if (this.classList.contains('active')) return;

        // Intercept navigation until animation completes
        e.preventDefault();

        // Icon spring bounce (skip if reduced-motion)
        if (!prefersReduced) {
          var svg = this.querySelector('svg');
          if (svg) {
            svg.style.animation = 'none';
            svg.offsetHeight;
            svg.style.animation = 'nav-spring 0.48s var(--ease-spring, cubic-bezier(0.34, 1.56, 0.64, 1))';
          }
        }

        // Update ARIA + active class
        for (var j = 0; j < items.length; j++) {
          items[j].classList.remove('active');
          items[j].removeAttribute('aria-current');
        }
        this.classList.add('active');
        this.setAttribute('aria-current', 'page');

        // Animate capsule — navigation fires as callback when compression completes
        var self = this;
        animateCapsule(this, function() {
          window.location.href = href;
        });
      });
    }

    // --- Listen for OS reduced-motion changes ---
    if (window.matchMedia) {
      window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', function(e) {
        prefersReduced = e.matches;
      });
    }
  }
  initBottomNav();

  /* === Global Toast Function === */
  window.showToast = function(msg, type) {
    type = type || '';
    var phone = document.querySelector('.phone');
    if (!phone) return;

    // Try static toast element first
    var toast = document.getElementById('toast');
    if (toast) {
      toast.textContent = msg;
      toast.className = 'toast show ' + type;
      clearTimeout(toast._timeout);
      toast._timeout = setTimeout(function() {
        toast.classList.remove('show');
      }, 2000);
      return;
    }

    // Create dynamic toast
    var t = document.createElement('div');
    t.className = 'toast show ' + type;
    t.textContent = msg;
    phone.appendChild(t);
    setTimeout(function() {
      t.classList.remove('show');
      setTimeout(function() { t.remove(); }, 300);
    }, 2000);
  };

  /* === Debounce Utility === */
  window.debounce = function(fn, delay) {
    var timer;
    return function() {
      var ctx = this, args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function() { fn.apply(ctx, args); }, delay);
    };
  };
})();
