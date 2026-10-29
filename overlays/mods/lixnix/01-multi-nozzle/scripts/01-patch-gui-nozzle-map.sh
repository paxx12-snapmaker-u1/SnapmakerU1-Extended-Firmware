#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @LixNix

# Stock `gui` 2.0.0.205 loads the per-filament nozzle diameter in the
# filament -> toolhead mapping loop only for third-party slicers. Files from
# Snapmaker Orca match every filament against `nozzle_diameter_list[0]`, so
# mixed-nozzle jobs started from the touchscreen land on the wrong toolhead.
#
# Analysis, instruction changes and PATCHED_SHA256 by @LixNix (patch 06 of
# the U1 2.0.0.205 nozzle-map fix); ported here from their Python script.

STOCK_SHA256="bd2d80e9084c6709161fd9ca4130f06c57e89b00ffa596bc6c5706c4f1aebda4"
PATCHED_SHA256="47608f9a2b6af7bc6e39df323cf3dc6baf9110127c154905b57e135268a22eb6"

# offset, original bytes, new bytes (little-endian AArch64 words)
PATCHES=(
  # cbz w0, 0x182330 -> nop: always load nozzle_diameter_list[i]
  0x182324 60000034 1f2003d5
  # fmov d1, d9 -> fmov d1, d11: log the 0.4 default actually applied
  0x182340 2141601e 6141601e
)

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <rootfs-dir>"
    exit 1
fi

set -eo pipefail

GUI="$1/usr/bin/gui"

read_hex() {
    od -An -v -tx1 -j $(($1)) -N "$2" "$GUI" | tr -d ' \n'
}

ACTUAL_SHA256=$(sha256sum "$GUI" | awk '{print $1}')

if [[ "$ACTUAL_SHA256" != "$STOCK_SHA256" ]]; then
    echo "Error: $GUI SHA256 mismatch (expected stock 2.0.0.205), got $ACTUAL_SHA256"
    exit 1
fi

echo ">> Patching gui nozzle-diameter mapping"
for ((i = 0; i < ${#PATCHES[@]}; i += 3)); do
    OFFSET="${PATCHES[i]}"
    ORIGINAL="${PATCHES[i+1]}"
    NEW="${PATCHES[i+2]}"

    FOUND="$(read_hex "$OFFSET" $(( ${#ORIGINAL} / 2 )))"
    if [[ "$FOUND" != "$ORIGINAL" ]]; then
        echo "Error: $GUI at $OFFSET: expected $ORIGINAL, found $FOUND"
        exit 1
    fi

    echo "[+] $OFFSET: $ORIGINAL -> $NEW"
    printf "$(sed 's/../\\x&/g' <<< "$NEW")" | \
        dd of="$GUI" bs=1 seek=$((OFFSET)) conv=notrunc status=none
done

ACTUAL_SHA256=$(sha256sum "$GUI" | awk '{print $1}')

if [[ "$ACTUAL_SHA256" != "$PATCHED_SHA256" ]]; then
    echo "Error: patched $GUI SHA256 mismatch, got $ACTUAL_SHA256"
    exit 1
fi
