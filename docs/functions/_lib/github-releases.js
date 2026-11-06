export const DEFAULT_REPO = "paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware";
export const CACHE_SECONDS = 60;

// How long `githubApi()` keeps a last-known-good copy of each GitHub API
// response, served only when a fresh call fails (rate-limited or
// unreachable). Releases change rarely, so a day-old answer beats an error.
export const STALE_SECONDS = 24 * 60 * 60;

// Tag of the single rolling pre-release that `.github/workflows/develop.yaml`
// overwrites (via `ncipollo/release-action`'s `allowUpdates`) on every push
// to `develop`. Its `name` carries the real build version; the tag itself
// stays fixed so CI keeps editing the same release instead of creating one
// per push.
export const DEVELOP_RELEASE_TAG = "rolling";

// Cached on `path` alone (ignoring the `Authorization` header, which affects
// only rate-limiting, not the data returned), separately from the outer
// per-endpoint response cache `cached()` below keys on the incoming device
// request. This is what lets `latest.js` and `upgrade_desc.js` — two
// separate device requests that each resolve the same release — share one
// upstream GitHub API call instead of doubling it.
//
// Every success is also kept under a separate key for `STALE_SECONDS`, and
// served when a fresh call fails: unauthenticated calls share GitHub's 60
// req/hour limit per IP with every other tenant on Cloudflare's egress IPs,
// so 403/429s happen even at low traffic.
async function githubApi(path, env) {
  const apiUrl = `https://api.github.com${path}`;
  const cacheKey = new Request(apiUrl);
  const staleUrl = new URL(apiUrl);
  staleUrl.searchParams.set("stale", "1");
  const staleKey = new Request(staleUrl.toString());
  const cache = caches.default;

  const hit = await cache.match(cacheKey);
  if (hit) return hit.json();

  const headers = {
    "User-Agent": "snapmakeru1-extended-firmware-worker",
    "Accept": "application/vnd.github+json"
  };
  if (env.GITHUB_TOKEN) headers.authorization = `Bearer ${env.GITHUB_TOKEN}`;

  let res;
  try {
    res = await fetch(apiUrl, { headers });
  } catch (err) {
    const stale = await cache.match(staleKey);
    if (stale) return stale.json();
    throw err;
  }

  if (!res.ok) {
    // A missing release is a real answer, not an outage, so never mask it.
    if (res.status !== 404) {
      const stale = await cache.match(staleKey);
      if (stale) return stale.json();
    }
    const err = new Error(`GitHub API ${path} returned ${res.status}`);
    err.status = res.status;
    throw err;
  }

  const body = JSON.stringify(await res.json());
  await Promise.all([
    cache.put(cacheKey, cachedJson(body, CACHE_SECONDS)),
    cache.put(staleKey, cachedJson(body, STALE_SECONDS)),
  ]);
  return JSON.parse(body);
}

function cachedJson(body, maxAge) {
  return new Response(body, {
    headers: {
      "content-type": "application/json",
      "cache-control": `public, max-age=${maxAge}`,
    },
  });
}

// `stable` uses GitHub's dedicated "latest release" endpoint, which already
// excludes drafts and pre-releases. `testing` takes whatever GitHub says is
// newest overall — release or pre-release, whichever was published last —
// skipping the rolling `develop` release, which is otherwise always the
// newest since CI overwrites it on every push. `develop` fetches that
// rolling release directly by its fixed tag.
export async function findRelease(channel, env) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;

  if (channel === "stable") {
    return getReleaseById("latest", env);
  }

  if (channel === "develop") {
    return getReleaseById(`tags/${DEVELOP_RELEASE_TAG}`, env);
  }

  const releases = await githubApi(`/repos/${repo}/releases?per_page=5`, env);
  const release = releases.find((r) => r.tag_name !== DEVELOP_RELEASE_TAG);
  if (!release) {
    const err = new Error("no releases found");
    err.status = 404;
    throw err;
  }
  return release;
}

export async function getReleaseById(id, env) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  return githubApi(`/repos/${repo}/releases/${id}`, env);
}

export function findAsset(release, prefix, suffix) {
  return release.assets.find((asset) => asset.name.startsWith(prefix) && asset.name.endsWith(suffix));
}

// Raw (trimmed, unfiltered) lines of a named `## <heading>` section of a
// release body, stopping at the next `##` heading.
function sectionLines(body, heading) {
  const lines = (body || "").split(/\r?\n/).map((line) => line.trim());
  const target = `## ${heading}`.toLowerCase();
  const startIndex = lines.findIndex((line) => line.toLowerCase() === target);
  if (startIndex === -1) return [];

  const items = [];
  for (let i = startIndex + 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^##\s/.test(line)) break;
    items.push(line);
  }
  return items;
}

// Pulls the bullet list out of a named `## <heading>` section of a release
// body, stopping at the next `##` heading.
export function extractSection(body, heading) {
  const items = [];
  for (const line of sectionLines(body, heading)) {
    const bullet = line.match(/^-\s+(.*)$/);
    if (bullet) items.push(bullet[1].trim());
  }
  return items;
}

// Reads back the `md5`/`sha256`/`size` row `.github/scripts/append_checksums.js`
// fills into the `## Checksums` table at build time (once — hashing the
// ~300MB `.bin` itself on every device request would be far too slow). One
// row per asset (`| <file> | <md5> | <sha256> | <size> |`), so `assetName`
// (the exact release-asset filename) picks the right one out.
export function extractChecksums(body, assetName) {
  for (const line of sectionLines(body, "Checksums")) {
    if (!line.startsWith("|") || !line.endsWith("|")) continue;

    const cells = line.slice(1, -1).split("|").map((cell) => cell.trim());
    if (cells.length !== 4 || cells[0] !== assetName) continue;

    const [, md5, sha256, size] = cells;
    if (!/^[0-9a-f]{32}$/.test(md5) || !/^[0-9a-f]{64}$/.test(sha256) || !/^\d+$/.test(size)) continue;

    return { md5, sha256, size: Number(size) };
  }
  return null;
}

export function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": `public, max-age=${CACHE_SECONDS}`,
    },
  });
}

// Caches every response — success or error alike — so a burst of identical
// requests (e.g. many printers on the same channel) only hits the GitHub API
// once per TTL, whichever TTL `jsonResponse()` picked for that status.
export function cached(cache, waitUntil, request, response) {
  waitUntil(cache.put(request, response.clone()));
  return response;
}
