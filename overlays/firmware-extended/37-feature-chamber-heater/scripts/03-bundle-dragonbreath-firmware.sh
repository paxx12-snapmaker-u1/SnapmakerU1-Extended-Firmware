#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @plastikman
#
# Bundle the pinned DragonBreath device firmware at build time.

VERSION=v1.1.20
FILENAME="dragonbreath-${VERSION}.bin"
URL="https://github.com/plastikman/DragonBreath/releases/download/${VERSION}/dragonbreath-${VERSION}.bin"
BIN_SHA256=a4c7671997a43b9d4bec78dcabd620e900a6a63915a7aca8395514b4bf2c909a

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

echo ">> Fetching + verifying DragonBreath ${VERSION} device firmware (build host)..."
cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$BIN_SHA256"

echo ">> Bundling DragonBreath firmware into the image..."
install -Dm644 "$CACHE_DIR/$FILENAME" "$ROOTFS_DIR/usr/local/share/chamber-heater/dragonbreath.bin"

echo ">> DragonBreath firmware bundled ($(stat -c%s "$CACHE_DIR/$FILENAME") bytes, ${VERSION})."
