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

  // «Что если»: прогноз итога семестра по ползунку. Без JS блок скрыт, ведомость работает как раньше.
  // Ширина полосы — классами w-0…w-100, как и на сервере (строгая CSP, без inline-стилей).
  function setWidth(el, part, total) {
    var pct = total ? Math.max(0, Math.min(1, part / total)) * 100 : 0;
    el.className = el.className.replace(/\bw-\d+\b/g, "").replace(/\s+/g, " ").trim() + " w-" + Math.round(pct / 5) * 5;
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-whatif]"), function (box) {
    var earned = +box.getAttribute("data-earned");
    var total = +box.getAttribute("data-total");
    var admission = +box.getAttribute("data-admission");
    var credit = box.getAttribute("data-credit") === "1";
    var range = box.querySelector("input[type=range]");
    var pick = box.querySelector("[data-whatif-pick]");
    var plus = box.querySelector(".whatif-plus");
    var res = box.querySelector("[data-whatif-res]");
    var chips = box.querySelectorAll("[data-whatif-set]");
    if (!range || !pick || !plus || !res || !total) return;
    var remaining = +range.max;
    var strong = Math.ceil(total * 0.75);   // как на сервере: от 75 % — «отлично идёт»

    function render() {
      var n = +range.value, sum = earned + n, tone, text;
      var head = "Итог семестра — <b>" + sum + " из " + total + "</b>";
      if (sum < admission) {
        tone = "bad";
        text = head + ": до допуска не хватит " + (admission - sum) + " б." +
          (earned + remaining < admission ? " Даже с максимумом допуск не набрать: нужна отработка, обратитесь в деканат." : "");
      } else if (credit) {
        tone = "good";
        text = head + ": допуск к зачёту есть.";
      } else if (sum < strong) {
        tone = "ok";
        text = head + ": допуск есть." +
          (strong - earned <= remaining ? " Для темпа на «отлично» нужно от " + (strong - earned) + " б." : "");
      } else {
        tone = "good";
        text = head + ": допуск есть, это темп на «отлично».";
      }
      pick.textContent = n;
      setWidth(plus, sum, total);
      box.classList.remove("whatif-good", "whatif-ok", "whatif-bad");
      box.classList.add("whatif-" + tone);
      res.innerHTML = text;   // только числа и наш текст, без ввода пользователя
      Array.prototype.forEach.call(chips, function (c) {
        c.classList.toggle("is-on", +c.getAttribute("data-whatif-set") === n);
      });
    }

    range.addEventListener("input", render);
    Array.prototype.forEach.call(chips, function (c) {
      c.addEventListener("click", function () { range.value = c.getAttribute("data-whatif-set"); render(); });
    });
    box.hidden = false;
    render();
  });

  // Карточка «Новое в кабинете»: видна, пока студент её не скроет. Выбор хранится только в этом браузере.
  var news = document.querySelector("[data-whatsnew]");
  if (news) {
    var newsKey = "dk-whatsnew-" + news.getAttribute("data-whatsnew");
    var dismissed = false;
    try { dismissed = window.localStorage.getItem(newsKey) === "1"; } catch (err) { /* приватный режим */ }
    if (!dismissed) news.hidden = false;
    var newsClose = news.querySelector("[data-whatsnew-close]");
    if (newsClose) newsClose.addEventListener("click", function () {
      news.hidden = true;
      try { window.localStorage.setItem(newsKey, "1"); } catch (err) { /* не страшно: просто покажем снова */ }
    });
  }

  // Кнопка печати электронной справки
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-print]")) window.print();
  });
})();
