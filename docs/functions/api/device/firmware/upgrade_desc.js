import {
  BIN_ASSET_SUFFIX,
  CACHE_SECONDS,
  extractSection,
  findAsset,
  getReleaseById,
} from "../../../_lib/github-releases.js";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": `public, max-age=${CACHE_SECONDS}`,
    },
  });
}

export async function onRequestGet(context) {
  const { request, env, waitUntil } = context;
  const url = new URL(request.url);
  const id = url.searchParams.get("id");

  if (!id || !/^\d+$/.test(id)) {
    return jsonResponse({ error: "missing or invalid 'id' query param" }, 400);
  }

  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;

  let release;
  try {
    release = await getReleaseById(id, env);
  } catch (err) {
    return jsonResponse({ error: String(err.message || err) }, 404);
  }

  const binAsset = findAsset(release, BIN_ASSET_SUFFIX);
  if (!binAsset) {
    return jsonResponse({ error: `release '${release.tag_name}' has no .bin asset` }, 404);
  }

  const fullversion = release.tag_name.replace(/^v/, "");
  const version = fullversion.split("-")[0];
  const sha256 = binAsset.digest?.startsWith("sha256:") ? binAsset.digest.slice(7) : null;

  const body = {
    name: release.name || release.tag_name,
    version,
    fullversion,
    size: binAsset.size,
    // Not computed yet — GitHub's release API doesn't expose an MD5 for
    // assets (only the sha256 `digest` below), and hashing the ~300MB
    // asset on every request is not implemented. The key is present
    // because unisrv's parser requires it, but its value is a placeholder.
    md5: null,
    sha256,
    release_notes: {
      "en-GB": extractSection(release.body, "New Features and Key Changes"),
    },
  };

  const response = jsonResponse(body);
  waitUntil(cache.put(request, response.clone()));
  return response;
}
