#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

GIT_URL=https://github.com/snapmaker/u1-klipper.git
GIT_SHA=10f2f69714c059f1dee0423db4f515b8a01f5388

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

CUR_DIR="$(realpath "$(dirname "$0")")"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/u1-klipper-dummy-at32f403a"
cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

# cache_git.sh reuses an existing checkout as-is on a local (non-CI) rebuild,
# so reset it to the pinned commit before patching - otherwise re-running
# this script would try to re-apply the patch onto an already-patched tree.
git -C "$TARGET_DIR" checkout -f "$GIT_SHA"
git -C "$TARGET_DIR" clean -fdx

echo ">> Applying dummy_at32f403a board patch..."
patch -F 0 --no-backup-if-mismatch -d "$TARGET_DIR" -p1 \
  < "$CUR_DIR/../klipper-patches/01_add_board.patch"

echo ">> Configuring dummy_at32f403a board..."
pushd "$TARGET_DIR" > /dev/null
printf 'CONFIG_MACH_DUMMY_AT32F403A=y\n' > .config
make olddefconfig

echo ">> Compiling klipper-dummy-at32f403a..."
make clean
make CROSS_PREFIX=aarch64-linux-gnu- -j"$(nproc)"
popd > /dev/null

echo ">> Installing klipper-dummy-at32f403a..."
install -d "$1/usr/local/bin"
install -m 755 "$TARGET_DIR/out/klipper.elf" "$1/usr/local/bin/klipper-dummy-at32f403a"

echo ">> Validate binary..."
stat "$1/usr/local/bin/klipper-dummy-at32f403a" >/dev/null

echo ">> dummy_at32f403a toolhead simulator build completed successfully."
