export const DEFAULT_REPO = "paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware";
export const BIN_ASSET_SUFFIX = "_upgrade.bin";
export const CACHE_SECONDS = 300;

async function githubApi(path, env) {
  const headers = {
    accept: "application/vnd.github+json",
    "user-agent": "snapmakeru1-extended-firmware-worker",
  };
  if (env.GITHUB_TOKEN) headers.authorization = `Bearer ${env.GITHUB_TOKEN}`;

  const res = await fetch(`https://api.github.com${path}`, { headers });
  if (!res.ok) {
    throw new Error(`GitHub API ${path} returned ${res.status}`);
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
  if (!releases.length) throw new Error("no releases found");
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
export function extractSection(body, heading) {
  if (!body) return [];

  const lines = body.split("\n");
  const startIndex = lines.findIndex((line) => line.trim().toLowerCase() === `## ${heading}`.toLowerCase());
  if (startIndex === -1) return [];

  const items = [];
  for (let i = startIndex + 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^##\s/.test(line.trim())) break;
    const bullet = line.match(/^\s*-\s+(.*)$/);
    if (bullet) items.push(bullet[1].trim());
  }
  return items;
}
