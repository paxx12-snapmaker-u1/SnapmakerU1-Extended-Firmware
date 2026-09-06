# Cloudflare Pages Functions

Server-side endpoints for `snapmakeru1-extended-firmware.pages.dev`, deployed
by `.github/workflows/cloudflare_pages.yaml`. Wrangler looks for `functions/`
next to its own working directory, not inside the deployed static output —
so the deploy step runs with `workingDirectory: docs` and deploys `../_site`,
letting this `docs/functions/` directory ship alongside it.

## `GET /api/device/firmware/latest`

Consumed by the `firmware-imposter` `LD_PRELOAD` shim
(`overlays/firmware-extended/38-feature-upgrade-firmware/`), which rewrites
`unisrv`'s real `.../api/device/firmware/latest` check to this same path on
our own host when `upgrade.firmware` in `extended2.cfg` is `stable` or
`beta` (`none`/`snapmaker` stay on stock). `?channel=stable` resolves the
latest GitHub release; `?channel=beta` resolves the latest of either a
release or pre-release, whichever is newer.

Mirrors Snapmaker's `ApiDeviceFirmwareLatest` shape (minus `authDevices`,
which is unrelated to this flow):

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": 342972029,
    "name": "v1.4.1-paxx12-20",
    "note": "https://snapmakeru1-extended-firmware.pages.dev/api/device/firmware/upgrade_desc?id=342972029",
    "url": "https://github.com/.../U1_extended_1.4.1-paxx12-20_upgrade.bin",
    "status": 200,
    "version": "1.4.1-paxx12-20",
    "createDate": "2026-06-21T20:57:51",
    "modifiedDate": "2026-06-23T15:21:32"
  }
}
```

`url` points straight at the GitHub asset. `unisrv` fetches it with its own
`curl` handle (not intercepted by the shim), so it still attaches its
Snapmaker `Authorization: Bearer` header — modern libcurl (device ships
8.6.0) strips that header by default on any cross-host redirect, so it
never reaches GitHub.

On failure, the status mirrors whatever GitHub returned (e.g. `403` on
rate-limit), falling back to `502` for network errors.

## `GET /api/device/firmware/upgrade_desc`

The `note` URL above. Given `?id=<release id>`, builds the descriptor
`unisrv` downloads next, dynamically from GitHub release metadata:

```json
{
  "name": "v1.4.1-paxx12-20",
  "version": "1.4.1",
  "fullversion": "1.4.1-paxx12-20",
  "size": 290327624,
  "md5": "deadbeefdeadbeefdeadbeefdeadbeef",
  "sha256": "8ddb1d6dc889f8c11d6ac708dd4858439b13e830d3bf93064e857433a70ff3c3",
  "release_notes": { "en-GB": ["Quick Actions panel...", "..."] }
}
```

`size`/`sha256` come from the asset's GitHub `digest`. `release_notes.en-GB`
is scraped from the release body's `## New Features and Key Changes`
section (`extractSection()` in `_lib/github-releases.js`). **`md5` is a
dummy value** — GitHub doesn't expose one and hashing the ~300MB `.bin` per
request isn't implemented, so a real device will fail the post-download
integrity check until this is filled in.

## Caching & config

Successful responses are cached at Cloudflare's edge (Cache API) for
`CACHE_SECONDS` (5 min, `_lib/github-releases.js`); errors get
`Cache-Control: no-store` and are never cached.

| Env var        | Required | Purpose                                                                    |
|----------------|----------|-------------------------------------------------------------------------------|
| `GITHUB_REPO`  | No       | `owner/repo`; defaults to `paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware`  |
| `GITHUB_TOKEN` | No       | Lifts GitHub's 60 req/hour unauthenticated rate limit                         |

Set both in the Cloudflare Pages dashboard (Settings → Environment
variables), not in this repo.
