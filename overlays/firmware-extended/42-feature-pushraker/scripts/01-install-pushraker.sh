#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

# pushraker (https://github.com/noon92/pushraker): push notifications as a
# Moonraker component. Pinned; a new firmware brings a new pushraker.
# Its dependencies (Apprise, requests, Pillow) are mostly in the image
# already (12-patch-moonraker, 61-app-remote-screen); pip adds what isn't.
PUSHRAKER_VERSION=0.2.4

echo ">> Installing pushraker $PUSHRAKER_VERSION via pip3"
cache_pip.sh "$ROOTFS_DIR" "pushraker==$PUSHRAKER_VERSION"

# The Klipper macros and the settings page come from the package: link them
# where the extended config and nginx expect them
PKG_DIR=$(chroot_firmware.sh "$ROOTFS_DIR" python3 -c \
  'import os, pushraker; print(os.path.dirname(pushraker.__file__))')
ln -sfn "$PKG_DIR/klipper/pushraker.cfg" "$ROOTFS_DIR/usr/local/share/pushraker/klipper.cfg"
ln -sfn "$PKG_DIR/web" "$ROOTFS_DIR/usr/local/share/pushraker/web"
test -e "$ROOTFS_DIR$PKG_DIR/klipper/pushraker.cfg"
test -e "$ROOTFS_DIR$PKG_DIR/web/index.html"

# Into the extended config (copied there at boot, see S49extended-config)
# as links, so they follow the firmware's pushraker
ln -sfn /usr/local/share/pushraker/klipper.cfg \
  "$ROOTFS_DIR/usr/local/share/firmware-config/extended/klipper/05_pushraker.cfg"
ln -sfn /usr/local/share/pushraker/moonraker.cfg \
  "$ROOTFS_DIR/usr/local/share/firmware-config/extended/moonraker/05_pushraker.cfg"
