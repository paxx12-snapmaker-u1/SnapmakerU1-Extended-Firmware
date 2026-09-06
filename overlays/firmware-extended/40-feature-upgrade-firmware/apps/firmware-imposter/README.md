# firmware-imposter

A `libcurl` wrapper library that redirects or blocks the firmware-update
check made by `unisrv` instead of letting it reach `id.snapmaker.com`.

## Description

`unisrv` performs its firmware-update check over HTTPS using `libcurl`. It
issues a `GET` to `https://id.snapmaker.com/api/device/firmware/latest` and
parses the JSON response (`ApiDeviceFirmwareLatest`).

This library provides a drop-in replacement for `curl_easy_perform`. When
loaded via `LD_PRELOAD`, every perform reads the request's current URL with
`curl_easy_getinfo(CURLINFO_EFFECTIVE_URL)`. If that URL contains the
configured match string, the library either:

- rewrites it to `FW_UPDATE_REWRITE_URL` and replaces the request headers
  with a single `Authorization:` entry, which strips the `Bearer` token so it
  is never sent to the rewrite host — the real transfer then runs against
  the new URL with `unisrv`'s own write callback intact, so the JSON it
  receives is whatever the rewrite host returns; or
- if `FW_UPDATE_BLOCK` is set, fails the request immediately with
  `CURLE_COULDNT_CONNECT` without performing any transfer at all, so `unisrv`
  never reaches the network and reports no update available.

Everything happens inside `curl_easy_perform` — there is no per-handle
bookkeeping. The library only activates when `FW_UPDATE_REWRITE_URL` or
`FW_UPDATE_BLOCK` is set, and only inside the `unisrv` process (it checks
`/proc/self/exe`), so preloading it on the `lmd` launcher — which also starts
`gui` and `flow_calc_server` — leaves those processes untouched.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FW_IMPOSTER_DEBUG` | `0` | Set to `1` to enable debug logging to stderr |
| `FW_UPDATE_URL` | (none — must be set) | Substring matched against the request URL |
| `FW_UPDATE_REWRITE_URL` | (empty) | Replacement URL; ignored (and unnecessary) when `FW_UPDATE_BLOCK` is set |
| `FW_UPDATE_BLOCK` | `0` | Set to `1` to fail matching requests instead of rewriting them, disabling the update check entirely |

## Usage

```sh
LD_PRELOAD=/usr/local/lib/libfirmware-imposter.so \
  FW_UPDATE_URL="/api/device/firmware/latest" \
  FW_UPDATE_REWRITE_URL="https://snapmakeru1-extended-firmware.pages.dev/api/device/firmware/latest?channel=stable&version=1.5.1&build_version=0.9.0-paxx12-1-gabcdef0&build_profile=extended" \
  unisrv
```

## Notes

- The request to the rewrite host is sent without `unisrv`'s custom headers
  (the `Authorization: Bearer` token included), so no credentials leak. The
  host must therefore serve the firmware descriptor without authentication.
- Reading the pending URL before the transfer relies on
  `CURLINFO_EFFECTIVE_URL`, which returns the set URL in libcurl 8.x (the
  device ships libcurl/8.6.0).
