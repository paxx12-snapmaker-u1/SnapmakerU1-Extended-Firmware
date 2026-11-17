#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @cosnerr

# librockit-export.so is preloaded into stock unisrv. It resolves the real
# librockit functions by name at run time, and only starts once unisrv calls
# RK_MPI_VI_EnableChn. If a stock update renames one of them, the camera
# stops working with nothing but a line in the unisrv log. Fail the build
# instead. This checks names only, not argument or struct layout.

CUR_DIR="$(realpath "$(dirname "$0")")"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

set -eo pipefail

NM="${CROSS_COMPILE:-aarch64-linux-gnu-}nm"
SOURCE="$CUR_DIR/../apps/rockit-export/main.c"

if ! command -v "$NM" > /dev/null; then
  echo "Error: $NM not found"
  exit 1
fi

# Every function main.c looks up with LOAD(field, "name").
SYMBOLS=$(sed -n 's/^ *LOAD([a-z_]*, "\([A-Za-z0-9_]*\)");.*/\1/p' "$SOURCE")
if [[ -z "$SYMBOLS" || "$(wc -l <<< "$SYMBOLS")" -ne "$(grep -c '^ *LOAD(' "$SOURCE")" ]]; then
  echo "Error: cannot read the LOAD() symbols from $SOURCE"
  exit 1
fi

LIBS=$(find "$1" -path "$1/cache" -prune -o -type f -name 'librockit*.so*' ! -name 'librockit-export.so' -print)
if [[ -z "$LIBS" ]]; then
  echo "Error: no librockit library found in $1"
  exit 1
fi

UNISRV=$(find "$1" -path "$1/cache" -prune -o -type f -name unisrv -print)
if [[ -z "$UNISRV" ]]; then
  echo "Error: unisrv not found in $1"
  exit 1
fi

echo ">> Checking librockit symbols used by librockit-export.so..."
echo "   librockit: ${LIBS//$'\n'/ }"
echo "   unisrv: ${UNISRV//$'\n'/ }"
# shellcheck disable=SC2086
DEFINED=$("$NM" -D --defined-only $LIBS 2>/dev/null | awk '{sub(/@.*/, "", $NF); print $NF}' || true)
MISSING=0
for symbol in $SYMBOLS; do
  if ! grep -qx "$symbol" <<< "$DEFINED"; then
    echo "Error: $symbol is not exported by: ${LIBS//$'\n'/ }"
    MISSING=1
  fi
done

# shellcheck disable=SC2086
IMPORTED=$("$NM" -D --undefined-only $UNISRV 2>/dev/null | awk '{sub(/@.*/, "", $NF); print $NF}' || true)
if ! grep -qx RK_MPI_VI_EnableChn <<< "$IMPORTED"; then
  echo "Error: $UNISRV does not import RK_MPI_VI_EnableChn"
  MISSING=1
fi

if [[ "$MISSING" -ne 0 ]]; then
  echo ">> Review apps/rockit-export/main.c against the new stock librockit and unisrv."
  exit 1
fi

echo ">> librockit symbols OK ($(wc -w <<< "$SYMBOLS") found)."
