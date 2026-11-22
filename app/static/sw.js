// Service worker «Цифрового деканата».
// Кеширует только общие статические файлы и страницу «Нет соединения».
// Страницы с личными данными (расписание, оценки, заявки) в кеш не попадают никогда:
// их всегда отдаёт сервер, который проверяет вход и права.
"use strict";

const CACHE = "dekanat-static-v8";
const ASSETS = [
  "/static/style.css?v=8",
  "/static/fonts/open-sans-cyrillic-wdth-normal.woff2",
  "/static/fonts/open-sans-latin-wdth-normal.woff2",
  "/static/app.js?v=4",
  "/static/logo.svg",
  "/static/icon-192.png",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Страницы — всегда из сети; без связи показываем понятную заглушку.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
    return;
  }

  // Статика — сначала из кеша (версия зашита в адрес ?v=…), иначе из сети.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
  }
});
