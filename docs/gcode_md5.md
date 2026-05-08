---
title: G-code MD5 Verification
---

# G-code MD5 Verification

Verifies an MD5 checksum embedded in a g-code file before printing begins. If
the file has been corrupted or truncated, Klipper will detect the mismatch, 
cancel the print, and, by default, delete the file.

## How it works

The feature requires two one-time setup steps in your slicer:

1. **Post-processing script**: a helper script is added to your slicer's
   post-processing configuration. After every slice, the slicer runs the
   script automatically, which computes an MD5 hash of the output file and
   prepends `; MD5:<hash>` as its very first line.

2. **Machine Start G-code**: `CHECK_MD5` is added as the first command in
   your slicer's Machine Start G-code. When a print begins, Klipper reads
   the `; MD5:` header, re-hashes the rest of the file, and compares the
   two values. A mismatch triggers `CANCEL_PRINT` and, by default,
   deletion of the corrupted file.

Files without a `; MD5:` header are printed normally so existing
workflows and files are unaffected.

## Setup

### Step 1 — Enable the feature

Go to **[Firmware Config](firmware_config.md) → Tweaks → G-code MD5 Verification** and set it
to **Enabled**. Klipper will restart automatically.

### Step 2 — Add the post-processing script to your slicer

The post-processing script stamps every exported file with a checksum
automatically. Download the script for your platform:

- **Linux / macOS** — [add_md5.sh](../overlays/firmware-extended/35-feature-gcode-md5/root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.sh)
- **Windows** — [add_md5.bat](../overlays/firmware-extended/35-feature-gcode-md5/root/usr/local/share/firmware-config/tools/gcode-md5/add_md5.bat)

If your printer is accessible over SSH, the scripts are also installed on
the printer itself at:
```
/usr/local/share/firmware-config/tools/gcode-md5/
```

Then add it to your slicer's post-processing configuration:

#### Snapmaker Orca / OrcaSlicer / BambuStudio
*Process → Others → Post-processing Scripts:*
```
/full/path/to/add_md5.sh;
```

#### PrusaSlicer
*Print Settings → Output options → Post-processing scripts:*
```
/full/path/to/add_md5.sh;
```

The slicer passes the output file as the first argument automatically.

### Step 3 — Add CHECK_MD5 to your slicer's Machine Start G-code

Add `CHECK_MD5` as the very first line of your slicer's Machine Start
G-code, before `PRINT_START`. This is what triggers the verification on
the printer when a print begins.

#### Snapmaker Orca / OrcaSlicer / BambuStudio
*Printer Settings → Machine G-code → Machine Start G-code:*
```
CHECK_MD5
PRINT_START ...
```

#### PrusaSlicer
*Printer Settings → Custom G-code → Start G-code:*
```
CHECK_MD5
PRINT_START ...
```

## Delete Invalid Files Option
By default, if `CHECK_MD5` detects a corrupted file, it will delete the
file to avoid it being accidentally used.

You can use `CHECK_MD5 DELETE=False` in your slicer to disable.

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

- Incomplete upload: retry the upload
- File was edited after stamping: re-run `add_md5.sh` / `add_md5.bat`
- Disk or storage corruption

**"No MD5 checksum found in G-code"**

The file has no `; MD5:<hash>` header. This is a warning, not an error. The
print continues normally. Stamp the file with the helper script if you
want it verified in the future.

**Files without a checksum print fine**

Expected behaviour. Only files that carry a `; MD5:` header are checked.

## Credit

Ported from [DrA1ex/ff5m](https://github.com/DrA1ex/ff5m), which implements
the same feature for the Flashforge Adventurer 5M (Pro).
