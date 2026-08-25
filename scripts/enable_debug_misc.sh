#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <upgrade.bin> <temp-dir> <output.bin>"
  exit 1
fi

set -eo pipefail

ROOT_DIR="$(realpath "$(dirname "$0")/..")"
IN_FIRMWARE="$(realpath "$1")"
TEMP_DIR="$(realpath -m "$2")"
OUT_FIRMWARE="$(realpath -m "$3")"

rm -rf "$TEMP_DIR"

echo ">> Unpacking firmware"
"$ROOT_DIR/scripts/helpers/unpack_firmware.sh" "$IN_FIRMWARE" "$TEMP_DIR/"

echo ">> Enabling debug misc partition"
cp -v "$ROOT_DIR/tools/debug/debug_misc.img" "$TEMP_DIR/rk-unpacked/misc.img"

echo ">> Repacking firmware"
"$ROOT_DIR/scripts/helpers/pack_firmware.sh" "$TEMP_DIR" "$OUT_FIRMWARE"

echo ">> Done: $OUT_FIRMWARE"

# Alternative shorter version:
# "$ROOT_DIR/scripts/helpers/repack_firmware.sh" "$1" "$2" \
#   cp -v "$ROOT_DIR/tools/debug/debug_misc.img" "$TMP_DIR/rk-unpacked/misc.img"
