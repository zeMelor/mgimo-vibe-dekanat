// Небольшие улучшения поверх обычных HTML-форм. Без JS всё тоже работает.
(function () {
  "use strict";

  // Индикатор загрузки при переходе по ссылкам и отправке форм
  function startLoading() { document.body.classList.add("is-loading"); }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || a.hasAttribute("download") || /\.ics$/.test(href)) return;
    startLoading();
  });
  window.addEventListener("pageshow", function () { document.body.classList.remove("is-loading"); });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) { e.preventDefault(); return; }
    if (form.hasAttribute("data-loading")) {
      var btn = form.querySelector("button[type=submit]");
      if (btn) {
        if (btn.disabled) { e.preventDefault(); return; }   // защита от двойной отправки
        btn.disabled = true;
        var text = btn.getAttribute("data-loading-text") || "Подождите…";
        btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>' + text;
      }
    }
    startLoading();
  });

  // Светлая или тёмная тема: выбор хранится в cookie, сервер сразу рисует нужную
  document.addEventListener("click", function (e) {
    if (!(e.target.closest && e.target.closest("[data-theme-toggle]"))) return;
    var root = document.documentElement;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    document.cookie = "theme=" + next + "; Path=/; Max-Age=31536000; SameSite=Lax" + (location.protocol === "https:" ? "; Secure" : "");
  });

  // Установка на главный экран (PWA)
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }
  var installBox = document.querySelector("[data-install]");
  if (installBox) {
    var installBtn = installBox.querySelector("[data-install-btn]");
    var deferredPrompt = null;
    var show = function (part) {
      installBox.hidden = false;
      var el = installBox.querySelector(part);
      if (el) el.hidden = false;
    };
    var standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
    if (standalone) {
      show("[data-install-done]");
    } else if (/iphone|ipad|ipod/i.test(navigator.userAgent)) {
      show("[data-install-intro]"); show("[data-install-ios]");
    }
    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();
      deferredPrompt = e;
      show("[data-install-intro]"); show("[data-install-btn]");
    });
    if (installBtn) installBtn.addEventListener("click", function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function () { deferredPrompt = null; installBtn.hidden = true; });
    });
    window.addEventListener("appinstalled", function () {
      if (installBtn) installBtn.hidden = true;
      show("[data-install-done]");
    });
  }

  // Кнопка печати электронной справки
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-print]")) window.print();
  });
})();
