#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

VERSION=3.2.7
FILENAME=rsync-$VERSION.tar.gz
URL=https://download.samba.org/pub/rsync/binaries/centos-8-aarch64/$FILENAME
BIN_SHA256=2b8f21d006aaf94648bcc608717997cd34f27ba7f4b549f45d1a1dae63b78daa

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$BIN_SHA256" "$BUILD_DIR/rsync"

install -d "$ROOTFS_DIR/usr/local/bin"
install -m 755 "$BUILD_DIR/rsync/usr/local/bin/rsync" "$ROOTFS_DIR/usr/local/bin/rsync"
