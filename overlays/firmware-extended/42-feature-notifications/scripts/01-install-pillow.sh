#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

# The spool pictures (push_notifications_images.py) are drawn with Pillow.
# Apprise comes from 12-patch-moonraker.
echo ">> Installing Pillow for the Push Notifications spool pictures"
cache_pip.sh "$ROOTFS_DIR" "Pillow==11.3.0"
