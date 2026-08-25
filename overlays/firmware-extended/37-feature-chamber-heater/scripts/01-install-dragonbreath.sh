#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @plastikman

GIT_URL=https://github.com/plastikman/dragonbreath-klipper.git
# v2 helper (API v2 client) — dragonbreath-klipper main @ #6: fan-only filtration
# blower as [output_pin dragonbreath_filter] (#5) + deferred urllib/uuid imports
# so an unconfigured/idle install doesn't pull in ssl/http.client/email (#6).
# Pairs with DragonBreath firmware >= v0.6.5 (the `filter` command). Flash
# firmware first, then restart klippy. Validated on the U1 (module loads clean,
# [dragonbreath] object publishes).
GIT_SHA=c5f531b656599e3574571e534c83ec39a78de0b5

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/dragonbreath-klipper"
LAVA_UID=1000
LAVA_GID=1000

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing DragonBreath Klipper extras..."
install -Dm644 -o "$LAVA_UID" -g "$LAVA_GID" "$TARGET_DIR/dragonbreath.py" \
  "$ROOTFS_DIR/home/lava/klipper/klippy/extras/dragonbreath.py"

echo ">> DragonBreath installation completed successfully."
