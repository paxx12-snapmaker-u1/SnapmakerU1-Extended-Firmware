# Cloudflare Pages Functions

Server-side endpoints for the `snapmakeru1-extended-firmware.pages.dev` Pages
project, deployed by `.github/workflows/cloudflare_pages.yaml`. Wrangler picks
up a `functions/` directory that sits next to *its own working directory*, not
one nested inside the deployed static output — so the deploy step runs with
`workingDirectory: docs` and deploys `../_site` (the Jekyll build output),
letting this `docs/functions/` directory be uploaded alongside it.

## `GET /api/device/firmware/latest`

Consumed by the `firmware-imposter` `LD_PRELOAD` shim (see
`overlays/firmware-extended/38-feature-upgrade-firmware/`), which rewrites
`unisrv`'s `https://id.snapmaker.com/api/device/firmware/latest` check to this
same path on our own host when `upgrade.firmware` in `extended2.cfg` is
`stable` or `beta` (`none` and `snapmaker` both leave the launcher on stock,
unmodified behaviour). Deliberately mirrors the real path rather than a
custom one, since it's a drop-in replacement for the same request.

**Query params**

| Param     | Values           | Default  | Behaviour                                                        |
|-----------|------------------|----------|-------------------------------------------------------------------|
| `channel` | `stable`, `beta` | `stable` | `stable` = latest GitHub release only; `beta` = latest of either a release or a pre-release, whichever was published last |

The response mirrors the shape of Snapmaker's own `ApiDeviceFirmwareLatest`
response:

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

`authDevices` (a per-device auth allowlist in the real API) is intentionally
omitted; it is unrelated to this flow.

`url` points straight at the GitHub release asset. `unisrv` fetches it with
its own `curl` handle (a call the shim does not intercept), so it still
attaches its Snapmaker `Authorization: Bearer` header — modern libcurl (the
device ships 8.6.0) strips `Authorization` by default on any redirect that
changes host, so the token is not handed on to GitHub's storage backend.

## `GET /api/device/firmware/upgrade_desc`

The `note` URL above. Given `?id=<release id>`, generates the firmware
descriptor `unisrv` downloads after the initial check — dynamically, from
GitHub release metadata, rather than a file baked at release time:

```json
{
  "name": "v1.4.1-paxx12-20",
  "version": "1.4.1",
  "fullversion": "1.4.1-paxx12-20",
  "size": 290327624,
  "md5": null,
  "sha256": "8ddb1d6dc889f8c11d6ac708dd4858439b13e830d3bf93064e857433a70ff3c3",
  "release_notes": {
    "en-GB": ["Quick Actions panel in Firmware Config with in-place upgrade buttons...", "..."]
  }
}
```

- `size` and `sha256` come straight from the GitHub release-asset metadata
  (`digest: sha256:...`, added to the Releases API).
- `release_notes.en-GB` is scraped from the `## New Features and Key Changes`
  section of the release body (`extractSection()` in `_lib/github-releases.js`).
- **`md5` is a placeholder (`null`).** GitHub's API doesn't expose an MD5 for
  release assets, and hashing the ~250-300MB `.bin` on every request isn't
  implemented. The key is present because `unisrv`'s parser requires it to
  exist, but a real device will fail the post-download `CheckFirmwareFile`
  integrity check until this is filled in — auto-upgrade via this endpoint
  is not yet end-to-end functional.

## Caching

Both endpoints cache their response at Cloudflare's edge (Cache API, keyed by
the full request URL) for `CACHE_SECONDS` (5 minutes, `_lib/github-releases.js`),
so GitHub is called at most once per distinct `channel`/`id` per cache window.

## Configuration

| Env var        | Required | Purpose                                                              |
|----------------|----------|------------------------------------------------------------------------|
| `GITHUB_REPO`  | No       | `owner/repo` to query; defaults to `paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware` |
| `GITHUB_TOKEN` | No       | Sent as a `Bearer` token to the GitHub API to lift the 60 req/hour unauthenticated rate limit; set as a Pages secret if devices poll often enough to hit it |

Set both via the Cloudflare Pages dashboard (Settings → Environment
variables) for the `snapmakeru1-extended-firmware` project, not in this repo.
