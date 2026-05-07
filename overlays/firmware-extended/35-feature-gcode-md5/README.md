# 35-feature-gcode-md5

Adds MD5 checksum verification for g-code files.

## What it does

When a g-code file has a `; MD5:<hexdigest>` comment on its first line,
Klipper verifies the hash before printing begins and cancels the print
with a clear error message if the file is corrupt or was truncated during
upload.

Files **without** a checksum header are allowed through unchanged.

## How enabling/disabling works

Firmware Config creates or removes a symlink at
`/oem/printer_data/config/extended/klipper/gcode_md5.cfg` pointing to the
canonical copy at
`/usr/local/share/firmware-config/tweaks/klipper/gcode_md5.cfg`.

The presence of that symlink is the enabled state. When it exists, Klipper
loads the plugin and the `PRINT_START` wrapper.

## How the hook works

`gcode_md5.cfg` wraps the existing `PRINT_START` macro using Klipper's
`rename_existing` pattern. The original `PRINT_START` is renamed to
`_PRINT_START_BASE` and called with all original parameters intact after
a successful check.

## Files installed

| Destination | Purpose                                             |
|---|-----------------------------------------------------|
| `/home/lava/klipper/klippy/extras/gcode_md5.py` | Klipper plugin (exposes `CHECK_MD5` command)        |
| `/usr/local/share/firmware-config/tweaks/klipper/gcode_md5.cfg` | Canonical config: symlinked into place when enabled |
| `/usr/local/share/firmware-config/functions/35_settings_tweaks_gcode_md5.yaml` | Firmware Config UI toggles                          |
| `/usr/local/share/firmware-config/tools/gcode-md5/add_md5.sh` | Slicer helper script: Linux / macOS                 |
| `/usr/local/share/firmware-config/tools/gcode-md5/add_md5.bat` | Slicer helper script: Windows                       |

## Slicer integration

The helper scripts are installed on the printer at
`/usr/local/share/firmware-config/tools/gcode-md5/` and are also available
for direct download from the repository:

- [add_md5.sh](root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.sh) — Linux / macOS
- [add_md5.bat](root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.bat) — Windows

Both scripts can be wired into all popular slicers as a
post-processing script so every export is stamped automatically.

## Runtime commands

| Command | Description |
|---|---|
| `CHECK_MD5 [FILENAME=...] [DELETE=True\|False]` | Manually verify a file |

## Credit

Ported from [DrA1ex/ff5m](https://github.com/DrA1ex/ff5m), which
implements the same feature for the Flashforge Adventurer 5M (Pro).
