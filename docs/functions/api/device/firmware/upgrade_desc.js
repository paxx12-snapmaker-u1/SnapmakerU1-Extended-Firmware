// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12
//
import {
  cached,
  extractChecksums,
  extractSection,
  getReleaseById,
  jsonResponse,
} from "../../../_lib/github-releases.js";

export async function onRequestGet(context) {
  const { request, env, waitUntil } = context;
  const url = new URL(request.url);
  const releaseId = url.searchParams.get("id") || "";
  const assetId = url.searchParams.get("asset_id") || "";

  const cache = caches.default;
  const hit = await cache.match(request);
  if (hit) return hit;

  if (!releaseId || !assetId) {
    return cached(cache, waitUntil, request, jsonResponse(
      { error: "missing required 'id'/'asset_id' query params" },
      400,
    ));
  }

  // Resolved directly by id — the exact release/asset `latest.js` already
  // picked for this device, not re-run through `channel`/`build_profile`
  // resolution (see that endpoint for why).
  let release;
  try {
    release = await getReleaseById(releaseId, env);
  } catch (err) {
    const status = err.status || 502;
    return cached(cache, waitUntil, request, jsonResponse({ error: String(err.message || err) }, status));
  }

  const binAsset = release.assets.find((asset) => String(asset.id) === assetId);

  if (!binAsset) {
    return cached(cache, waitUntil, request, jsonResponse(
      { error: `release '${release.tag_name}' has no asset '${assetId}'` },
      404,
    ));
  }

  // `.github/scripts/append_checksums.js` appends these to the release notes
  // at build time — real `md5`/`sha256`, computed once from the `.bin`
  // instead of hashing a ~300MB asset on every device request. Everything
  // else here (`release_notes` in particular) is read straight from the
  // live release body, so it can never go stale the way the static
  // `upgrade_desc.json` release asset this replaced used to.
  const checksums = extractChecksums(release.body, binAsset.name);

  if (!checksums) {
    return cached(cache, waitUntil, request, jsonResponse(
      { error: `release '${release.tag_name}' has no checksums for '${binAsset.name}'` },
      404,
    ));
  }

  let version = (release.name || release.tag_name).replace(/^(?:Rolling:\s*)?v/, "");
  let fullversion = version;

  // The 1.6.0-paxx12-22 release was built with a different versioning scheme
  // than the rest of the 1.6.x series, so we need to hardcode the fullversion
  // for that one release.
  if (version == "1.6.0-paxx12-22") {
    fullversion = "1.6.0.267_20260815150420";
  }

  const body = {
    name: release.name || release.tag_name,
    version,
    fullversion,
    size: checksums.size,
    md5: checksums.md5,
    sha256: checksums.sha256,
    release_notes: {
      "en-GB": extractSection(release.body, "New Features and Key Changes"),
    },
  };

  return cached(cache, waitUntil, request, jsonResponse(body));
}
