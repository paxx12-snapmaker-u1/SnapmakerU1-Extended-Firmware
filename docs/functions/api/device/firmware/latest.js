// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

// Placeholder for #559 — resolving `?channel=stable/testing/develop` against
// GitHub releases isn't implemented yet. Every request gets an empty 200
// for now.
export async function onRequestGet() {
  return new Response(null, {
    status: 200,
    headers: { "cache-control": "no-store" },
  });
}
