#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

# Keep Mainsail's service worker away from /firmware-config/ and friends (HTTPS)
patch_sw_denylist.sh "$ROOTFS_DIR/home/lava/mainsail/sw.js" 'denylist:[/^\/(access|api|printer|server|websocket)/'
