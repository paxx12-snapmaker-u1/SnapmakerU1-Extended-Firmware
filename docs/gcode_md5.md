---
title: G-code MD5 Verification
---

# G-code MD5 Verification

Verifies an MD5 checksum embedded in a g-code file before printing begins.
If the file has been corrupted or truncated Klipper will detect the mismatch, 
cancel the print, and report a clear error rather than starting a bad print.

## How it works

The feature uses an opt-in, file-by-file approach:

1. **Slicer host**: after slicing, run one of the helper scripts provided
   with this feature. The script computes an MD5 hash of the file and
   prepends `; MD5:<hash>` as the very first line.

2. **Printer**: when `PRINT_START` runs, `CHECK_MD5` reads that first line,
   re-hashes the rest of the file, and compares the two values. A mismatch
   triggers `CANCEL_PRINT` and a clear error message.

Files **without** a `; MD5:` header are printed normally so existing 
workflows and files are unaffected.

## How the hook works

When enabled, Firmware Config symlinks `gcode_md5.cfg` into Klipper's
extended config directory. That file wraps `PRINT_START` using Klipper's
`rename_existing` pattern: the original macro is preserved as
`_PRINT_START_BASE` and called with all its original parameters after a
successful check. When the feature is disabled, the symlink is removed and
Klipper has no knowledge of the plugin.

## Setup

### Step 1 — Enable the feature

Go to **Firmware Config → Tweaks → G-code MD5 Verification** and set it
to **Enabled**. Klipper will restart automatically.

### Step 2 — Download the helper script for your slicer host

The scripts are included with the firmware and are also available on GitHub:

- **Linux / macOS** — [add_md5.sh](../../overlays/firmware-extended/35-feature-gcode-md5/root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.sh)
- **Windows** — [add_md5.bat](../../overlays/firmware-extended/35-feature-gcode-md5/root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.bat)

If your printer is accessible over SSH, the scripts are also installed on
the printer itself at:
```
/usr/local/share/firmware-config/tools/gcode-md5/
```

### Step 3 — Automate with a post-processing script

#### Snapmaker Orca / OrcaSlicer / BambuStudio
**Process → Others → Post-processing Scripts:**
```
/full/path/to/add_md5.sh;
```

#### PrusaSlicer
**Print Settings → Output options → Post-processing scripts:**
```
/full/path/to/add_md5.sh;
```

The slicer passes the output file as the first argument automatically.

## Firmware Config settings

The following settings are available at **Firmware Config → Tweaks**:

| Setting | Default | Description |
|---|---|---|
| G-code MD5 Verification | Disabled | Enable or disable the feature. Klipper restarts automatically. |
| G-code MD5 — Delete Invalid Files | Enabled | Auto-delete corrupt files and their thumbnails on failure. Has no effect unless G-code MD5 Verification is enabled. |

## Manual console commands

```
# Verify the currently-loaded file
CHECK_MD5

# Verify a specific file
CHECK_MD5 FILENAME=/path/to/file.gcode

# Verify without deleting on failure (overrides config)
CHECK_MD5 FILENAME=/path/to/file.gcode DELETE=False
```

## Troubleshooting

**Print cancelled with "MD5 checksum mismatch"**

The file content does not match its checksum. Common causes:

- Incomplete upload — retry the upload
- File was edited after stamping — re-run `add_md5.sh` / `add_md5.bat`
- Disk or storage corruption — try re-uploading to a different path

**"No MD5 checksum found in G-code"**

The file has no `; MD5:<hash>` header. This is a warning, not an error. The
print continues normally. Stamp the file with the helper script if you
want it verified in the future.

**Files without a checksum print fine**

Expected behaviour. Only files that carry a `; MD5:` header are checked.

## Credit

Ported from [DrA1ex/ff5m](https://github.com/DrA1ex/ff5m), which implements
the same feature for the Flashforge Adventurer 5M (Pro).
