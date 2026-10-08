#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

VERSION=8.22.0
FILENAME=curl-linux-aarch64-glibc-$VERSION.tar.xz
URL=https://github.com/stunnel/static-curl/releases/download/$VERSION/$FILENAME
BIN_SHA256=fa4de50f80fb2fbbf77a7bf8385891b6cb1a501a2e28b1ed18cf25e11fd66b66

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$BIN_SHA256" "$BUILD_DIR/curl"

install -d "$ROOTFS_DIR/usr/local/bin"
install -m 755 "$BUILD_DIR/curl/curl" "$ROOTFS_DIR/usr/local/bin/curl"
