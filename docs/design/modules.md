---
title: Modules
---

# Modules

A module is a self-contained feature living entirely under `/extended/<name>/` on the
running device, managed by the `extended` CLI (`list`, `info`, `enable`, `disable`,
`start`, `stop`, `restart`). It replaces a build-time `overlays/firmware-extended/<NN-name>/`
overlay for any feature that doesn't need to patch shared/upstream files, so the feature can
be installed, enabled, disabled, or removed on a running printer without a firmware rebuild.

## Design Principles

Firmware overlays are applied once, at image-build time, and merged by numeric ordering —
there is no way to add, remove, or swap a feature without rebuilding the whole image, and no
real conflict model beyond "first file at this path wins." Modules exist to make the parts of
the firmware that are genuinely self-contained features — a camera stack, a VPN integration,
a settings tweak — composable the way `extended-pkg` already makes external dependencies
composable (see [Third-Party Integrations](third_party.md)): declared once, activated on
demand, removable without touching anything else.

Not every overlay qualifies. Anything that patches a shared/upstream file — vendor Klipper or
Moonraker source, a stock init script, `/etc/group`, a kernel module — has no module boundary
to exploit and stays a build-time overlay permanently. A module never overwrites an upstream
file; it only ever adds new paths under its own directory.

## Module Layout

```text
/extended/<module-name>/
├── module.conf                      # optional, DESCRIPTION="..." sourced as shell
├── disabled                         # marker file; presence = user explicitly disabled.
│                                     #   The only on/off gate at the module level.
├── bin/                             # module's own executables (private payload)
├── lib/                             # module's own libraries (private payload)
└── share/                           # things an external consumer reads or runs — never
    │                                 #   the module's own private payload
    ├── rc.d/
    │   └── <service-name>/          # a module may define as many services as it needs
    │       ├── start
    │       └── stop
    ├── firmware-config.d/
    │   └── config/*.yaml            # settings/actions merged into the Firmware Config UI
    ├── klipper.d/
    │   ├── activate                 # optional, sourced for every non-disabled module
    │   ├── config/*.cfg             # inert unless activate requests it
    │   └── extras/*.py              # inert unless activate requests it
    ├── moonraker.d/
    │   ├── activate
    │   ├── config/*.cfg
    │   └── components/*.py
    └── lmd.d/
        └── activate                 # lmd has no config/components equivalent
```

Two naming rules hold throughout, applied uniformly rather than special-cased:

- **`.d` marks a directory of drop-in pieces, once, at the outer service-named directory.**
  `klipper.d` mirrors the *system-level* `/etc/hooks/klipper.d/*.sh` dispatcher one level up
  (see [Service Hooks](service_hooks.md)). Content-type subdirectories underneath — `config`,
  `extras`, `components` — stay plain, never a second `.d`.
- **`share/` holds anything an external actor reads or runs**: Klipper, Moonraker, LMD, the
  Firmware Config UI, or the `extended` runtime itself via `rc.d`. `bin/` and `lib/` are
  strictly the module's own private payload — referenced only by the module's own `rc.d` or
  `activate` scripts, never invoked directly by the runtime.

## Activation Model

The only static gate at the module level is the `disabled` marker file. Everything else is
opt-in, on demand:

- **`config/`, `extras/`, and `components/` are inert data.** Nothing symlinks a module's
  `.cfg` files into Klipper's live config, or its `extras/`/`components/` files into
  Klipper's or Moonraker's own source tree, just because those directories exist. The only
  thing that makes them take effect is the module's own `share/<service>.d/activate` script
  explicitly requesting it.
- **Extras and components are symlinked directly into the real vendor directory, not added
  to `PYTHONPATH`.** Klipper's loader (`klippy.py`'s `load_object`) hard-checks
  `os.path.exists()` against `klippy/extras/<name>.py` before ever importing anything, so
  `PYTHONPATH` is never consulted — a module's extra has to physically exist at that path.
  Moonraker's component loader imports `moonraker.components.<name>` as an ordinary package
  member, which is simplest to satisfy the same way rather than relying on namespace-package
  path resolution. Both directories live in the rootfs overlay, which is writable at
  runtime (see [Data Persistence](../data_persistence.md)), so `extended_activate_extras()`
  symlinks `$MODULE_DIR/share/klipper.d/extras/*.py` straight into
  `/home/lava/klipper/klippy/extras/`, and Moonraker's `extended_activate_components()` does
  the same into `/home/lava/moonraker/moonraker/components/` — the same real paths
  `38-feature-spoollink` already ships its extra/component to today, just linked at runtime
  instead of baked in at build time.
- **Symlinking refuses to overwrite anything the sweep didn't just clear.** Every symlink
  helper (`extended_activate_config`, `extended_activate_extras`, `extended_activate_components`)
  goes through a shared `_extended_link` that checks the destination first: if something is
  already there — a real file (a stock Klipper extra, a stock Moonraker component, a plain
  `.cfg`) or a symlink the sweep step didn't remove (pointing outside `/extended/`, e.g.
  another module or mechanism using the same name) — it logs an error and leaves it alone
  instead of silently clobbering it with `ln -sfn`. This is the only place two modules (or a
  module and something else) shipping a same-named file becomes visible; nothing else in the
  activation model detects that collision.
- **The per-service bridge script defines the activation helpers, then sources each
  non-disabled module's `activate`.** For Klipper, `/etc/hooks/klipper.d/00-extended-bridge.sh`
  defines `extended_activate_config()` (symlinks `$MODULE_DIR/share/klipper.d/config/*.cfg`
  into `printer_data/config/extended/klipper/`, under their original names) and
  `extended_activate_extras()` (as above), then loops over `/extended/*`, skipping disabled
  modules, sourcing each remaining one's `share/klipper.d/activate` with the service action
  as `$1`. Moonraker's bridge is the same shape (`extended_activate_components()` instead of
  `_extras`); LMD's has neither helper, just the `activate` sourcing.
- **A module's `activate` decides for itself what to call:**
  ```sh
  # share/klipper.d/activate
  [ "$1" = start ] || return 0
  extended_activate_config
  extended_activate_extras
  ```
  This is deliberately more expressive than a single module-wide on/off switch: a module can
  activate for Moonraker but not Klipper, or skip a helper conditionally — for example, only
  calling `extended_activate_extras` when a hardware probe succeeds.
- **`MODULE` and `MODULE_DIR` are exported before any module script runs** — `activate`,
  `share/rc.d/<service>/start`, `share/rc.d/<service>/stop`. `MODULE` is the module's
  directory name under `/extended/`; `MODULE_DIR` is its absolute path
  (`/extended/<MODULE>`). A module's scripts use these rather than hardcoding their own
  install path, so the same module directory works unmodified if copied under a different
  name. `rc.d` scripts additionally get `SERVICE`, the service's directory name under
  `rc.d/`, so a module with several services can tell which one it's being run as without
  hardcoding it either.
- **Every managed directory is swept before it's rebuilt, by target rather than by name.**
  None of the four directories the bridges write into are exclusively the module runtime's:
  `printer_data/config/extended/klipper/` is already globbed into `printer.cfg`
  (`[include extended/klipper/*.cfg]`) and gets symlinks from Firmware Config actions
  directly today (e.g. `34-feature-faulty-toolhead`'s per-toolhead bypass toggle);
  `klippy/extras/` and `moonraker/components/` are the vendor directories themselves, full of
  stock `.py` files. Before sourcing any module's `activate`, each bridge drops every symlink
  in its managed directories that is either dangling or points somewhere under `/extended/`
  — a plain file (vendor `.py`, or a real `.cfg`) is never touched, and neither is a symlink
  pointing elsewhere (such as `34-feature-faulty-toolhead`'s tweak files, which today live
  outside `/extended`). `activate` then recreates, under their original names, whatever the
  module still wants. This needs no bookkeeping between runs: a module that stopped calling
  the helper, or got disabled, simply doesn't recreate its files, and they were already
  swept. A module planning to symlink a config file that should persist across restarts
  without an `activate` re-asserting it every time (the shape `34-feature-faulty-toolhead`
  would take as a module) needs its target to resolve outside `/extended/` — e.g. by keeping
  the toggle's source file under its existing `firmware-config` share path rather than the
  module's own directory — or it will be swept on the next Klipper start along with
  everything else.
- **`firmware-config.d/config` is the deliberate exception to the `activate` step** — it has
  no `activate` and is always gathered for every non-disabled module, unconditionally on
  every run, because showing a settings panel has no functional side effect to gate, unlike
  physically changing what Klipper or Moonraker load. Its bridge
  (`/etc/hooks/firmware-config.d/00-extended-bridge.sh`, sourced by a small addition to
  `02-firmware-config`'s own `S99firmware-config`) also merges in the stock
  `usr/local/share/firmware-config/functions/` files under the same directory, since
  `firmware-config.py` only takes one `--functions-dir`. Unlike the klipper.d/moonraker.d
  bridges, this one doesn't gate the merge on `$1 = start`: `S99firmware-config`'s
  `restart()` calls its own `start`/`stop` shell functions directly rather than
  re-invoking the script (unlike `S60klipper`/`S61moonraker`, which do), so the hook only
  runs once per invocation and would otherwise skip the merge entirely on a plain
  `restart`.

### Helper Function Reference

| Helper | Defined by | Effect |
| --- | --- | --- |
| `extended_activate_config` | `klipper.d`/`moonraker.d` bridge | Symlinks `$MODULE_DIR/share/<service>.d/config/*.cfg` into `printer_data/config/extended/<service>/`, under their original names, swept and recreated on every `start`. |
| `extended_activate_extras` | `klipper.d` bridge | Symlinks `$MODULE_DIR/share/klipper.d/extras/*.py` directly into `/home/lava/klipper/klippy/extras/`, the real directory Klipper's loader reads — swept and recreated on every `start`. |
| `extended_activate_components` | `moonraker.d` bridge | Same idea as `extended_activate_extras`, for `$MODULE_DIR/share/moonraker.d/components` into `/home/lava/moonraker/moonraker/components/`. |

### Example: an unconditionally-activated module

A module whose config and extra should apply whenever it's enabled, no hardware check needed:

```sh
# share/klipper.d/activate
[ "$1" = start ] || return 0
extended_activate_config
extended_activate_extras
```

### Example: inert config, activated only by a Firmware Config action

Some settings shouldn't apply just because the module is enabled — e.g. a per-toolhead
thermistor bypass a user opts into individually. Here `share/klipper.d/config/` holds the
`.cfg` files but the module ships no `activate` that calls `extended_activate_config`, so the
bridge never touches them; a Firmware Config action links the chosen one in directly instead:

```yaml
# share/firmware-config.d/config/24_settings_bypass_toolhead1.yaml
actions:
  bypass_toolhead1:
    cmd: |
      ln -sf /extended/faulty-toolhead/share/klipper.d/config/faulty_toolhead1.cfg \
        /home/lava/printer_data/config/extended/klipper/faulty_toolhead1.cfg
      /etc/init.d/S60klipper restart
```

## Bootstrap: `01-system-utils`

Every mechanism above depends on `/extended` existing and on the stock service init
scripts sourcing the hooks that make it work. That bootstrap lives in
`overlays/firmware-extended/01-system-utils/` — the same overlay that already carries
`extended-pkg` (see [Third-Party Integrations](third_party.md)) and, merged on `develop`,
`patches/01-add-service-hooks.patch`, which makes `S60klipper`, `S61moonraker`, and
`S90lmd` source `/etc/hooks/{klipper,moonraker,lmd}.d/*.sh` in lexical order before
handling the service action (see [Service Hooks](service_hooks.md)). That patch and its
three `.keep` placeholder directories already exist and need no change; the module runtime
adds to this overlay rather than duplicating its role elsewhere, since it's already the
project's home for foundational, service-agnostic tooling.

New paths this overlay needs:

| Path | Purpose |
| --- | --- |
| `root/extended/.keep` | Makes `/extended` exist even with zero modules installed. Required before `extended list` or any bridge's `for MODULE_DIR in /extended/*` loop can run without erroring on a missing directory — and it's the mount point `docs/modules.md`'s "Adding Your Own Module" flow symlinks into. |
| `root/etc/hooks/firmware-config.d/.keep` | Scaffolds a fourth hooks directory alongside the three the existing patch already sources. `01-system-utils` owns all four placeholders as one cohesive set, even though sourcing this one from `S99firmware-config` belongs to `02-firmware-config` (it already owns that init script — see below). |

No changes to `patches/01-add-service-hooks.patch` itself are needed — it already sources
`/etc/hooks/{klipper,moonraker,lmd}.d/*.sh` unconditionally, and the bridge scripts that
populate those directories (`00-extended-bridge.sh` per service) are new *content* dropped
into directories that patch already creates, not a change to the patch.

Each `00-extended-bridge.sh` is deliberately self-sufficient rather than sourcing a shared
library: it defines its own copy of the `/extended/*` iteration (skipping disabled modules,
exporting `MODULE`/`MODULE_DIR`) and, for `klipper.d`/`moonraker.d`, its own
`extended_activate_config` symlink helper. This duplicates a few lines of shell across the
four bridges, but keeps each one readable and runnable in isolation — no shared file
elsewhere in the rootfs whose absence or edit could silently break every service's
activation at once.

The one new hook-sourcing point this design needs — `S99firmware-config` sourcing
`/etc/hooks/firmware-config.d/*.sh` before it computes its `--functions-dir` — is a direct
edit to `02-firmware-config`'s own `root/etc/init.d/S99firmware-config`, not to
`01-system-utils`: unlike `S60klipper`/`S61moonraker`/`S90lmd`, it's not vendor code, so it
needs no `.patch`, just the same sourcing loop added in place.

## What Cannot Become a Module

An overlay that patches a shared/upstream file has no module boundary to exploit and stays a
build-time overlay permanently:

- Kernel modules — load into kernel space, categorically not a userspace `/extended/<module>`
  payload.
- Patches to vendor Klipper/Moonraker source — a module can only *add* extras/components, by
  symlinking into `klippy/extras/`/`moonraker/components/` as described above, not alter
  existing upstream files.
- Patches to core init scripts (`S50dropbear`, `S60klipper`, `fluidd.cfg`'s hook macros,
  `/etc/group`) — these are the substrate the module runtime itself depends on, or system
  files with no runtime-managed equivalent.
- Anything that overwrites an existing upstream directory in place (e.g. replacing the
  default web frontend) rather than adding a new path.

A single overlay can be *partial*: most of its payload becomes a module, while a one-line
patch to a shared file (like adding a value to an existing Klipper section, or a Moonraker
`trusted_clients` entry) stays behind in a small build-time overlay alongside it — whichever
of the two [include] merge semantics applies decides whether the patch can be replaced by a
module's own `config/` fragment instead: Klipper's `[include]` merges per-option and
additively across files, so a module's fragment can safely override just one key; Moonraker's
`[include]` replaces the whole option on conflict, so a module's fragment touching an
already-set option would silently drop everything the base file set, and the patch should
stay.

## Documentation Requirements

User-facing instructions for adding, shipping, enabling, and disabling a module belong in
`docs/modules.md`, not here — this document is the design rationale, not the how-to. That
page should cover the "Adding Your Own Module" flow (copy to `/userdata/extended`, symlink
into `/extended`, reboot) and the "Shipping a Module With the Firmware" convention (an overlay
whose `root/` only ever installs under `root/extended/<module>/`), kept in sync with the
layout and activation model on this page.
