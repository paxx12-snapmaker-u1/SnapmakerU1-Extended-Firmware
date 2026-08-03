# firmware-imposter

A `libcurl` wrapper library that blocks the firmware-update check made by
`unisrv` instead of letting it reach `id.snapmaker.com`.

## Description

`unisrv` performs its firmware-update check over HTTPS using `libcurl`. It
issues a `GET` to `https://id.snapmaker.com/api/device/firmware/latest` and
parses the JSON response (`ApiDeviceFirmwareLatest`).

This library provides a drop-in replacement for `curl_easy_perform`. When
loaded via `LD_PRELOAD` with `FW_UPDATE_BLOCK=1` set, every perform reads the
request's current URL with `curl_easy_getinfo(CURLINFO_EFFECTIVE_URL)`. If
that URL contains the configured match string, the request fails immediately
with `CURLE_COULDNT_CONNECT` without performing any transfer at all, so
`unisrv` never reaches the network and reports no update available.

Everything happens inside `curl_easy_perform` — there is no per-handle
bookkeeping. The library only activates when `FW_UPDATE_BLOCK` is set, and
only inside the `unisrv` process (it checks `/proc/self/exe`), so preloading
it on the `lmd` launcher — which also starts `gui` and `flow_calc_server` —
leaves those processes untouched.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FW_IMPOSTER_DEBUG` | `0` | Set to `1` to enable debug logging to stderr |
| `FW_UPDATE_URL` | (none — must be set) | Substring matched against the request URL |
| `FW_UPDATE_BLOCK` | `0` | Set to `1` to fail matching requests, disabling the update check entirely |

## Usage

```sh
LD_PRELOAD=/usr/local/lib/libfirmware-imposter.so \
  FW_UPDATE_URL="/api/device/firmware/latest" \
  FW_UPDATE_BLOCK=1 \
  unisrv
```

## Notes

- Reading the pending URL before the transfer relies on
  `CURLINFO_EFFECTIVE_URL`, which returns the set URL in libcurl 8.x (the
  device ships libcurl/8.6.0).
