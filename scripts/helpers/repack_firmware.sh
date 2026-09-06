#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

echo "Repacking firmware..."
echo "$@"

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <upgrade.bin> <output.bin> <command> [...]"
  exit 1
fi

set -eo pipefail

IN="$(realpath "$1")"
OUT="$(realpath -m "$2")"
ROOT_DIR="$(realpath "$(dirname "$0")/../..")"
shift 2

TMP_DIR="$ROOT_DIR/tmp"
rm -rf "$TMP_DIR/"

echo ">> Unpacking firmware..."
"$ROOT_DIR/scripts/helpers/unpack_firmware.sh" "$IN" "$TMP_DIR"

echo ">> Running command: $*"
(
  cd "$TMP_DIR/rk-unpacked"
  "$@"
)

echo ">> Repacking firmware"
"$ROOT_DIR/scripts/helpers/pack_firmware.sh" "$TMP_DIR" "$OUT"

echo ">> Done: $OUT"
