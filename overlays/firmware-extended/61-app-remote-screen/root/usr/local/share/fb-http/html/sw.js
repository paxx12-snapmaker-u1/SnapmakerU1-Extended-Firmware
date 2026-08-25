// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

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
