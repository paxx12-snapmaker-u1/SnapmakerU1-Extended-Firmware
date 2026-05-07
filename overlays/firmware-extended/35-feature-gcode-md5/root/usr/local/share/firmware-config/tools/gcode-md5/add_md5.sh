#!/bin/bash
#
# add_md5.sh — Stamp an MD5 checksum onto a g-code file.
#
# Usage: add_md5.sh <file.gcode>
#
# Prepends "; MD5:<hash>" as the very first line of the file.
# The hash covers everything EXCEPT that header line, so the printer-side
# Klipper plugin skips the header and hashes the rest identically.
#
# Run this on your slicer host after slicing and before uploading.
# It can be added as a post-processing script in your slicer:
#   Snapmaker Orca / OrcaSlicer  : Process -> Others -> Post-processing Scripts
#   PrusaSlicer : Print Settings -> Output options -> Post-processing scripts
#
# Ported from DrA1ex/ff5m (https://github.com/DrA1ex/ff5m).
# License: GNU GPLv3

set -euo pipefail

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------
if [ $# -ne 1 ]; then
    echo "Usage: $0 <file.gcode>" >&2
    exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
    echo "Error: file not found: $FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Find an md5 utility (macOS uses md5, Linux uses md5sum)
# ---------------------------------------------------------------------------
if command -v md5sum &>/dev/null; then
    HASH=$(md5sum "$FILE" | awk '{print $1}')
elif command -v md5 &>/dev/null; then
    HASH=$(md5 -q "$FILE")
else
    echo "Error: neither md5sum nor md5 found on PATH." >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Prepend the header line using a temp file
# ---------------------------------------------------------------------------
HEADER="; MD5:${HASH}"
TMPFILE=$(mktemp "${FILE}.XXXXXX")

printf '%s\n' "$HEADER" > "$TMPFILE"
cat "$FILE" >> "$TMPFILE"
mv "$TMPFILE" "$FILE"

echo "Stamped ${FILE}: ${HASH}"
