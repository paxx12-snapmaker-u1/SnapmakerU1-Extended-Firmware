export const DEFAULT_REPO = "paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware";
export const BIN_ASSET_SUFFIX = "_upgrade.bin";
export const CACHE_SECONDS = 300;

async function githubApi(path, env) {
  const headers = {
    "User-Agent": "snapmakeru1-extended-firmware-worker",
    "Accept": "application/vnd.github+json"
  };
  if (env.GITHUB_TOKEN) headers.authorization = `Bearer ${env.GITHUB_TOKEN}`;

  const res = await fetch(`https://api.github.com${path}`, { headers });
  if (!res.ok) {
    const err = new Error(`GitHub API ${path} returned ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// `stable` uses GitHub's dedicated "latest release" endpoint, which already
// excludes drafts and pre-releases. `beta` takes whatever GitHub says is
// newest overall — release or pre-release, whichever was published last.
export async function findRelease(channel, env) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;

  if (channel === "stable") {
    return githubApi(`/repos/${repo}/releases/latest`, env);
  }

  const releases = await githubApi(`/repos/${repo}/releases?per_page=1`, env);
  if (!releases.length) {
    const err = new Error("no releases found");
    err.status = 404;
    throw err;
  }
  return releases[0];
}

export async function getReleaseById(id, env) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  return githubApi(`/repos/${repo}/releases/${id}`, env);
}

export function findAsset(release, suffix) {
  return release.assets.find((asset) => asset.name.endsWith(suffix));
}

// Pulls the bullet list out of a named `## <heading>` section of a release
// body (GitHub-flavoured markdown), stopping at the next `##` heading.
//
// GitHub's release `body` uses CRLF line endings, and a trailing `\r` left
// on each line breaks `$`-anchored regexes (`.` doesn't match `\r`, so it
// can never reach the end-of-string anchor) — split on `\r?\n` and trim
// each line up front so every match below works against a clean string.
export function extractSection(body, heading) {
  if (!body) return [];

  const lines = body.split(/\r?\n/).map((line) => line.trim());
  const target = `## ${heading}`.toLowerCase();
  const startIndex = lines.findIndex((line) => line.toLowerCase() === target);
  if (startIndex === -1) return [];

  const items = [];
  for (let i = startIndex + 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^##\s/.test(line)) break;
    const bullet = line.match(/^-\s+(.*)$/);
    if (bullet) items.push(bullet[1].trim());
  }
  return items;
}
