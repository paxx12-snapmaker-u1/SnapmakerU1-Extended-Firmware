# Add-on installers

Adds an **Add-ons** section to the firmware-config web UI
(`http://<printer-ip>/firmware-config/`) under **Settings**, with an
Installed / Not Installed toggle per add-on — same look and feel as the stock
settings, whose current value is always derived from the actual on-disk state.
Switching to *Installed* runs the install recipe (output streams into the
browser); switching to *Not Installed* runs it with `--uninstall`.

The recipes in `root/usr/local/bin/` are reproducible installers for tools
that aren't shipped in the firmware image but are useful on the Snapmaker U1.
They download and install **on the U1 itself, after flashing** — only the
recipe scripts and the firmware-config settings are baked into the image.

Each recipe can also be run without the web UI:

- on the printer: `install-spoolman.sh [--uninstall]` (they're on `PATH`)
- from a dev machine, without flashing this mod:
  `ssh root@<printer-ip> 'sh -s' < root/usr/local/bin/install-spoolman.sh`

All recipes are idempotent and safe to rerun. They require overlay
persistence (`/oem/.debug`) and refuse to run without it — otherwise
everything written to the root overlay is wiped on the next reboot. This
overlay also adds an **Overlay Persistence** toggle to firmware-config
(Settings > System) to manage that flag.

## Recipes

### install-helixscreen.sh

Installs [HelixScreen](https://helixscreen.org/) (native Klipper touchscreen
UI with a dedicated Snapmaker U1 backend: RFID spool recognition, per-slot
spool assignment, runout recovery), replacing the stock touchscreen UI via
the official installer. `--uninstall` restores the stock UI (returns after a
reboot).

Adds an extended-firmware-specific fixup: the official installer's boot hook
replaces `/etc/init.d/S99fb-http` and stops launching the Remote Screen
daemon, which would silently break `http://<printer-ip>/screen/`. The recipe
re-starts it after install, and the baked-in `/etc/init.d/S99fb-http-restore`
(in this overlay's `root/`) does the same on every boot.

### install-git.sh

Installs a working `git` binary (aarch64, from Debian's official arm64 .deb),
plus the `libcurl-gnutls.so.4 -> libcurl.so.4` ABI shim that Debian's
`git-remote-https` needs. HTTPS clones from GitHub/GitLab work after install.

Needed for AFC's `update-afc.sh -b DEV` and any git-based workflow on the U1.
`--uninstall` removes the binary, the git-core helpers, and both
compatibility symlinks.

### install-spoolman.sh

Installs [Donkie/Spoolman](https://github.com/Donkie/Spoolman) v0.23.1
(filament/spool tracking web UI + API) with the SQLite backend, an init.d
startup script, and the Moonraker `[spoolman]` component wired up.

Once installed, the AFC Klipper Add-On can pull material/color/vendor from
Spoolman when a lane is loaded, and Mainsail/Fluidd get the spool picker UI.

`--uninstall` stops the service and removes the init script, the Moonraker
`[spoolman]` config, and the application + venv — but keeps the spool
database, so a reinstall picks it up again.

After install:
- Spoolman UI: `http://<printer-ip>:7912`
- Data + SQLite DB: `/home/lava/printer_data/spoolman/` (persistent — bind
  mount from `/userdata`, not the overlay)
- Moonraker config: `/home/lava/printer_data/config/extended/moonraker/05_spoolman.cfg`

## Why the software isn't baked into the image

These tools are optional and per-user rather than part of the firmware
baseline. Keeping them as on-device install recipes:

- Reduces the base firmware image size.
- Lets users pick whether they want them installed.
- Keeps the deps (Debian git .deb, Spoolman zip, HelixScreen release) out of
  the firmware build pipeline and its licensing surface area.

The trade-off: anything installed this way lives on the persisted overlay, so
a Snapmaker firmware upgrade (which wipes the overlay and `/oem/.debug`)
removes it — rerun the recipe afterwards.

If one graduates to being "expected" on the extended firmware, it would
convert to a full overlay under `overlays/firmware-extended/` following the
pattern of `62-app-fluidd` (build-time download + rootfs baking).
