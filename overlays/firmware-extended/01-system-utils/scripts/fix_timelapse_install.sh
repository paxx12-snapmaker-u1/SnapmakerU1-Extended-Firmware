#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @horzadome, @paxx12, @liberodark

GIT_URL=https://github.com/horzadome/snapmaker-u1-timelapse-recovery.git
GIT_SHA=8e2a2e50e8642a4f368e4e4794585b2a2d2e2857

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

cache_git.sh "$CACHE_DIR/snapmaker-u1-timelapse-recovery" "$GIT_URL" "$GIT_SHA"

echo ">> Installing Timelapse Recovery Tool..."
install -m 755 -d "$ROOTFS_DIR/usr/local/bin"
install -m 755 "$CACHE_DIR/snapmaker-u1-timelapse-recovery/fix_timelapse.py" "$ROOTFS_DIR/usr/local/bin/fix_timelapse"
