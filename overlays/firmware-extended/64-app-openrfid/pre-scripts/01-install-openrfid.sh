#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

GIT_URL=https://github.com/suchmememanyskill/OpenRFID.git
GIT_SHA=a13bc9e181374e182f61db4e69abf9fa1786cc29

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/OpenRFID"
cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing OpenRFID..."
cd "$TARGET_DIR"
make install DESTDIR="$ROOTFS_DIR/usr/local/share/openrfid"
echo ">> OpenRFID installation completed successfully."
