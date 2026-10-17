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
our own host when `components.upgrade` in `extended2.cfg` is `stable`,
`testing`, or `develop` (`none` stays on stock). `?channel=stable` resolves
the latest GitHub release; `?channel=testing` resolves the latest of either
a release or pre-release, whichever is newer; `?channel=develop` resolves
the rolling `rolling`-tagged pre-release that
`.github/workflows/develop.yaml` overwrites (via `ncipollo/release-action`'s
`allowUpdates`) on every push to `develop`, so it always tracks the tip of
that branch ahead of any tag. See [`firmware_upgrade.md`](../firmware_upgrade.md)
for the user-facing description of the channels.

`develop`'s release keeps a fixed tag (`rolling`, named distinctly from the
`develop` branch to avoid the ambiguous-ref footgun of a tag and branch
sharing a name) so CI can keep editing the same release, so `release.name`
(not `tag_name`) is what carries the real build version — both endpoints
below read `release.name || release.tag_name` accordingly. Its `name` is
prefixed `Rolling: ` (e.g. `Rolling: v1.4.1-paxx12-20-gabcdef1`) to
distinguish it from `stable`/`testing` release names in the GitHub UI, so
both endpoints strip an optional leading `Rolling: ` along with the `v`
when deriving `fullversion`. `findRelease()` in `_lib/github-releases.js`
also excludes that tag from the `testing` lookup, otherwise the
constantly-refreshed `develop` release would always look like the newest
release-or-pre-release overall. Each push also force-moves the `rolling`
git tag itself to the new build's commit before the release step runs,
since GitHub only positions a release's target from a tag's current
commit — updating just the release (not the tag) would leave the tag,
and therefore the release, anchored to a stale commit.
Its body is generated each push from [`RELEASE.dev.md`](../../RELEASE.dev.md)
(the rolling-build notice, plus a `## New Features and Key Changes` section
the workflow fills in with every PR merged into `develop` that isn't in
`main` yet), before `.github/scripts/append_checksums.js` appends the
`## Checksums` section `upgrade_desc.js` reads from, described below.

`?build_profile=` (`extended`, `extended-afc`, or `extended-multiace`,
defaulting to `extended`)
picks which release asset to offer — `findAsset()` in
`_lib/github-releases.js` matches on the `U1_<profile>_` filename prefix CI
gives each profile and the shared `_upgrade.bin` suffix. The
`extended-multiace` image is rolling-only, like the experimental AFC image.
`?build_version=` is the device's own `/etc/BUILD_VERSION`
(`<fullversion>-<git abbrev>`, e.g. `1.4.1-paxx12-20-gabcdef1`); if it
already starts with the resolved release's version, the device is already
running this exact build and the endpoint responds with `data: null`
instead of an update descriptor (mirroring how stock Snapmaker's API
reports "no update available").

Mirrors Snapmaker's `ApiDeviceFirmwareLatest` shape (minus `authDevices`,
which is unrelated to this flow):

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": 342972029,
    "name": "v1.4.1-paxx12-20",
    "note": "https://snapmakeru1-extended-firmware.pages.dev/api/device/firmware/upgrade_desc?id=342972029&asset_id=298541203",
    "url": "https://github.com/.../U1_extended_1.4.1-paxx12-20_upgrade.bin",
    "status": 200,
    "version": "1.4.1-paxx12-20",
    "createDate": "2026-06-21T20:57:51",
    "modifiedDate": "2026-06-23T15:21:32"
  }
}
```

When the device is already on this build, `data` is `null` instead (see
above) — no `note`/`url` to follow.

`url` points straight at the `.bin` GitHub release asset — `findAsset()` in
`_lib/github-releases.js` matches it on the `U1_<profile>_` filename prefix
CI gives each profile plus the `_upgrade.bin` suffix. `note`
points back at this same host's `GET /api/device/firmware/upgrade_desc`
(below), carrying `?id=`/`?asset_id=` (`release.id`/`binAsset.id` — not
`channel`/`build_profile` again) so it's pinned to the exact release and
asset just resolved here, rather than re-running channel resolution and
risking it land on something else if a new release publishes in between.
`unisrv` fetches both with its own `curl` handle (not intercepted by the
shim), so it still attaches its Snapmaker `Authorization: Bearer` header;
we just ignore it.

On failure, the status mirrors whatever GitHub returned (e.g. `403` on
rate-limit), falling back to `502` for network errors.

## `GET /api/device/firmware/upgrade_desc`

The `note` URL above. Takes `?id=`/`?asset_id=` (the release and asset ids
`latest.js` already resolved) and looks both up directly by id via
`getReleaseById()`, then builds the descriptor `unisrv` downloads on
demand — dynamically, from that release's live state, rather than a static
asset baked in at build time:

```json
{
  "name": "v1.4.1-paxx12-20",
  "version": "1.4.1-paxx12-20",
  "fullversion": "1.4.1-paxx12-20",
  "size": 290327624,
  "md5": "8f14e45fceea167a5a36dedd4bea2543",
  "sha256": "8ddb1d6dc889f8c11d6ac708dd4858439b13e830d3bf93064e857433a70ff3c3",
  "release_notes": { "en-GB": ["Quick Actions panel...", "..."] }
}
```

`unisrv` verifies the downloaded firmware's MD5 against `md5`, failing the
update on a mismatch, so it must be real — but hashing the ~300MB `.bin` on
every device request would be far too slow. Instead, each CI workflow that
publishes a release (`.github/workflows/develop.yaml`, `pre_release.yaml`)
hashes every `.bin` it just built, once, and passes them all in one call to
`.github/scripts/append_checksums.js`, which fills the
`<!-- CHECKSUMS_PLACEHOLDER -->` in that workflow's release-notes file
(`RELEASE.dev.md` for `develop`, `RELEASE.md` for `stable`/`testing`) with a
table — one row per built asset, keyed by its exact filename:

```markdown
## Checksums

| File | MD5 | SHA256 | Size |
| --- | --- | --- | --- |
| U1_extended_1.4.1-paxx12-20_upgrade.bin | 8f14e45fceea167a5a36dedd4bea2543 | 8ddb1d6dc889f8c11d6ac708dd4858439b13e830d3bf93064e857433a70ff3c3 | 290327624 |
```

`extractChecksums()` in `_lib/github-releases.js` reads a row back out of
`release.body` at request time. `release_notes.en-GB`, by contrast, is
scraped straight from the *live* `## New Features and Key Changes` section
of `release.body` on every request, via `extractSection()` — so unlike the
static `upgrade_desc.json` release asset this replaced, hand-editing release
notes in the GitHub UI after publishing (normal for `stable`/`testing`,
which start out as a draft with `- TBD` placeholder notes) is reflected
immediately, with nothing needing to re-run or re-sync anything.

## Caching & config

Two independent cache layers, both on Cloudflare's edge Cache API:

- Each endpoint caches its own full JSON response keyed on the incoming
  request URL (so per `channel`/`build_profile`/etc — see `cached()` in
  `_lib/github-releases.js`), success or error alike, for `CACHE_SECONDS`
  (1 min) — including errors (bad input, no matching release, GitHub
  rate-limited or unreachable), so a burst of devices hitting a broken or
  rate-limited state only causes one GitHub API resolution per TTL instead
  of one per request.
- `githubApi()` in `_lib/github-releases.js` separately caches each raw
  GitHub API call it makes, keyed on the GitHub API path alone
  (`CACHE_SECONDS`, successful responses only). This is what lets
  `latest.js` and `upgrade_desc.js` — two separate device requests that
  both resolve the same release — share one upstream GitHub API call
  instead of doubling it, even though their outer response caches (above)
  are keyed differently.

| Env var        | Required | Purpose                                                                    |
|----------------|----------|-------------------------------------------------------------------------------|
| `GITHUB_REPO`  | No       | `owner/repo`; defaults to `paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware`  |
| `GITHUB_TOKEN` | No       | Lifts GitHub's 60 req/hour unauthenticated rate limit                         |

Set both in the Cloudflare Pages dashboard (Settings → Environment
variables), not in this repo.
