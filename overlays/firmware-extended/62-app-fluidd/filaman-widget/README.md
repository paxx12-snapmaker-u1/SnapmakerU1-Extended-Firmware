# FilaMan widget — fluidd source patch

This is a source-level patch that adds a [FilaMan](https://github.com/ManuelW77/FilaMan)
spool-tracking widget to fluidd's own Vue UI (dashboard card, settings, AFC lane
integration, spool selection dialog). It comes from
[ManuelW77/fluidd](https://github.com/ManuelW77/fluidd), a fluidd fork that carries
this widget as an isolated, minimal delta on top of stock fluidd.

## Why this isn't wired into the build yet

`../pre-scripts/01-install-fluidd.sh` in this repo downloads the official
`fluidd-core/fluidd` release ZIP and never builds fluidd from source, so an
existing-patches mechanism here (see `../patches/`) only ever touches already-built
output (e.g. `index.html`). This patch touches Vue/TypeScript source, so applying it
requires building fluidd from source first — something this repo doesn't currently do.

This PR only adds the patch as a reference/starting point. Wiring it into the build
(fork fluidd as a submodule / build step, apply this patch, produce the bundle) is a
separate, larger change to the fluidd packaging in this repo and is intentionally left
out here.

## What's in the patch

- New: FilaMan dashboard card, API/store actions, spool-mapping utilities, FilaMan
  icons.
- Modified: `globals.ts` (component init ordering), AFC mixin (recognizes FilaMan as a
  spool-tracking backend alongside spoolman), spoolman store/dialog/settings (backend
  switch between `spoolman` and `filaman`), EN/DE locale strings.

## Base and validation

- Diffed against `fluidd-core/fluidd` tag `v1.37.2` — the exact version currently
  pinned in `../pre-scripts/01-install-fluidd.sh`.
- Applied cleanly (`git apply`) to a fresh `v1.37.2` checkout; `pnpm run type-check`
  and `pnpm run lint` both pass with zero errors on the patched tree.

## Applying it

```bash
git clone --branch v1.37.2 https://github.com/fluidd-core/fluidd.git
cd fluidd
git apply /path/to/filaman-widget.patch
pnpm install
pnpm run build
```

The resulting `dist/` can replace what `../pre-scripts/01-install-fluidd.sh` currently
downloads, once this repo has a source-build path for fluidd.
