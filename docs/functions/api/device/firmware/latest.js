import {
  BIN_ASSET_SUFFIX,
  CACHE_SECONDS,
  findAsset,
  findRelease,
} from "../../../_lib/github-releases.js";

const CHANNELS = ["stable", "beta"];

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": `public, max-age=${CACHE_SECONDS}`,
    },
  });
}

// Trims the fractional-seconds/`Z` suffix GitHub timestamps carry, to match
// the plain `YYYY-MM-DDTHH:MM:SS` shape of Snapmaker's own API responses.
function toApiTimestamp(iso) {
  return iso.replace(/\.\d+Z$/, "").replace(/Z$/, "");
}

export async function onRequestGet(context) {
  const { request, env, waitUntil } = context;
  const url = new URL(request.url);
  const channel = (url.searchParams.get("channel") || "stable").toLowerCase();

  if (!CHANNELS.includes(channel)) {
    return jsonResponse(
      { code: 400, msg: `invalid channel '${channel}', expected one of: ${CHANNELS.join(", ")}`, data: null },
      400,
    );
  }

  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;

  let release;
  try {
    release = await findRelease(channel, env);
  } catch (err) {
    return jsonResponse({ code: 404, msg: String(err.message || err), data: null }, 404);
  }

  const binAsset = findAsset(release, BIN_ASSET_SUFFIX);

  if (!binAsset) {
    return jsonResponse(
      { code: 404, msg: `release '${release.tag_name}' has no .bin asset`, data: null },
      404,
    );
  }

  const descUrl = new URL("/api/device/firmware/upgrade_desc", url.origin);
  descUrl.searchParams.set("id", release.id);

  const body = {
    code: 200,
    msg: "success",
    data: {
      id: release.id,
      name: release.name || release.tag_name,
      note: descUrl.toString(),
      url: binAsset.browser_download_url,
      status: 200,
      version: release.tag_name.replace(/^v/, ""),
      createDate: toApiTimestamp(release.created_at),
      modifiedDate: toApiTimestamp(release.published_at || release.created_at),
    },
  };

  const response = jsonResponse(body);
  waitUntil(cache.put(request, response.clone()));
  return response;
}
