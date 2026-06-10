---
title: Firmware Upgrade Channels
---

# Firmware Upgrade Channels

Controls how the stock `unisrv` firmware-update check behaves, via the
`components.upgrade` setting in `extended/extended2.cfg`.

Stock firmware periodically asks `https://id.snapmaker.com/api/device/firmware/latest`
whether a new build is available. This project can redirect that check to a
community-run mirror, or disable it outright.

> **Note:** This only has an effect if the printer is connected to Snapmaker
> Cloud. `unisrv` only performs the firmware-update check while signed in, so
> `components.upgrade` is a no-op on a printer that doesn't use Snapmaker Cloud.

## Channels

```ini
[components]
upgrade: none   # or: stable, testing, develop
```

- **none** (default) — the update check fails immediately without making
  any network request at all, so the device never contacts any update host.
- **stable** — redirects the check to a Cloudflare Pages mirror tracking the
  latest tagged [GitHub release](https://github.com/paxx12/SnapmakerU1/releases)
  of this project.
- **testing** — same mirror, but tracks whichever of a release or pre-release
  is newest.
- **develop** — same mirror, but tracks the latest build off the `develop`
  branch — whatever is queued for the next release, ahead of any tag.

## What is being sent

When `stable`, `testing`, or `develop` is selected, `unisrv`'s update-check
request is rewritten to the mirror with the following query parameters:

| Parameter | Source | Example |
|-----------|--------|---------|
| `channel` | the selected value (`stable`/`testing`/`develop`) | `stable` |
| `version` | `/etc/VERSION` — stock firmware version | `1.5.1` |
| `build_version` | `/etc/BUILD_VERSION` — this project's `git describe` string | `0.9.0-paxx12-1-gabcdef0` |
| `build_profile` | `/etc/BUILD_PROFILE` — the build profile used | `extended` |

The device's own `Authorization: Bearer` token is stripped before the
request is sent, so no Snapmaker account credentials reach the mirror.
Nothing else from the request is forwarded — no printer serial number,
network info, or usage data.

When `none` is selected, no request is made at all: the check is failed
locally before any network I/O happens.

See [`overlays/firmware-extended/40-feature-upgrade-firmware`](https://github.com/paxx12/SnapmakerU1/tree/main/overlays/firmware-extended/40-feature-upgrade-firmware)
for the implementation.
