// Service Worker for Snapmaker U1 Remote Screen PWA
const CACHE_NAME = 'u1-screen-v1';

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(clients.claim());
});

// Basic fetch handler - no caching to avoid stale content
self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
