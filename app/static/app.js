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

  // Кнопка печати электронной справки
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-print]")) window.print();
  });
})();
