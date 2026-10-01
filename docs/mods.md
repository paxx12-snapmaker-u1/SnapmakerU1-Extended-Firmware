---
title: Mods
---

# Mods

`overlays/mods/<name>/` is a space for personal, experimental overlays that
aren't part of the maintained `firmware-extended` build. It exists so people
who maintain their own fork or feature set can capture that work as a single
composable overlay directory instead of carrying a whole separate fork.

## Naming

For personal mods, name the directory after your GitHub username, not after
the feature it adds:

```text
overlays/mods/<username>/
```

This keeps personal mods from colliding on a name and makes ownership clear.
Maintained project mods may use a feature name where the project publishes a
specific profile for them. These currently include `devel`, `qemu`, `afc`, and
`multiace`.

## Adding a mod

1. Create `overlays/mods/<username>/` with your own numbered overlays
   (`patches/`, `root/`, `scripts/`, `pre-scripts/`), following the same
   structure as any other overlay category — see
   [Overlay Structure](development.md#overlay-structure).
2. Build your own firmware with it:
   ```bash
   ./dev.sh make build PROFILE=extended-<username>
   ```
3. Mods are composable and apply in the order given, so they can be chained
   with each other and with maintained mods, e.g.
   `extended-<username>-devel`.

### Directory layout

A mod is just a directory of numbered overlays under your username, same as
any other overlay category. For example, a mod named `alice` that adds a
custom MOTD and disables a stock service might look like:

```text
overlays/mods/alice/
└── 01-custom-motd/
    ├── patches/
    │   └── etc/init.d/S50motd.patch
    ├── root/
    │   └── etc/motd
    └── scripts/
        └── 01-install-motd.sh
```

Each numbered directory (`01-custom-motd/`) is applied in this order:
`pre-scripts/` first, then `patches/`, then `root/` is copied in, then
`scripts/` runs last. Across multiple numbered directories in the same mod,
that whole sequence repeats in numeric order.

### Patches

Files under `patches/` are unified diffs (`diff -u` / `git diff` format),
applied with `patch -p1` from the firmware root. The path inside `patches/`
mirrors the path in the firmware, so `patches/etc/init.d/S50motd.patch`
patches `$ROOTFS_DIR/etc/init.d/S50motd`:

```diff
--- a/etc/init.d/S50motd
+++ b/etc/init.d/S50motd
@@ -1,4 +1,4 @@
 #!/bin/sh
-echo "Welcome to Snapmaker U1"
+echo "Welcome to Snapmaker U1 (alice's build)"
 exit 0
```

Generate one against the stock file (e.g. from `./dev.sh make extract`,
see [Extract Firmware](development.md#extract-firmware)), then trim it down
to just the hunks you need.

### Root files

Files under `root/` are copied as-is into the firmware root filesystem,
preserving both the path and the mode, e.g. `root/etc/motd` becomes
`$ROOTFS_DIR/etc/motd`. Use this for new files; use `patches/` to modify
files that already exist in stock firmware. Make sure any init script or
binary is already `chmod 755` in the mod's `root/` tree (and committed that
way) before it's copied in — the copy does not fix permissions for you.

### Scripts

Files under `scripts/` (and `pre-scripts/`, which run before `patches/` and
`root/` are applied) are executable shell scripts run inside the
`create_firmware.sh` environment, which exports `$ROOTFS_DIR`:

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

echo "Custom build by alice: $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$ROOTFS_DIR/etc/alice-build-info"
```

Use `scripts/` for anything a plain file copy or patch can't express, e.g.
values only known at build time, downloading and compiling a dependency
into the rootfs, or a package install step — not for dropping in a static
file, which belongs in `root/` instead.

## Rules

- Personal mods are not maintained by this project. Keeping them working
  against the latest firmware is the mod author's responsibility.
- Mods are not guaranteed to work together. If combining two mods breaks
  something, that's for the mods involved to sort out, not this repo.
- Mods are excluded from stable releases by default. The maintained
  `afc` and `multiace` profiles are available in rolling builds only; they
  remain experimental and are separate firmware images.
- A mod that reaches decent maturity can be promoted into
  `overlays/firmware-extended/` through a normal PR.
