#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @plastikman
#
# Bundle the pinned DragonBreath device firmware at build time.

VERSION=v1.1.12
FILENAME="dragonbreath-${VERSION}.bin"
URL="https://github.com/plastikman/DragonBreath/releases/download/${VERSION}/dragonbreath-${VERSION}.bin"
BIN_SHA256=8f21c98f9cdad6da4cf8e3f1aaad2453d3526ffbb30b16e79abf6f8f09e883ec

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
