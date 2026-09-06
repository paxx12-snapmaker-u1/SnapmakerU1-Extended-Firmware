#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

VERSION=8.17.0
FILENAME=curl-linux-aarch64-glibc-$VERSION.tar.xz
URL=https://github.com/stunnel/static-curl/releases/download/$VERSION/$FILENAME
BIN_SHA256=3c6562544e1a21cd37e9dec7c48c7a6d9a2f64da42fde69ba79e54014b911abb

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$BIN_SHA256" "$BUILD_DIR/curl"

install -d "$ROOTFS_DIR/usr/local/bin"
install -m 755 "$BUILD_DIR/curl/curl" "$ROOTFS_DIR/usr/local/bin/curl"
