#!/usr/bin/env bash

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

FILENAME=opkg-k3.10-aarch64
URL=https://bin.entware.net/aarch64-k3.10/installer/opkg
BIN_SHA256=1c59274bd25080b869f376788795f5319d0d22e91f325f74ce98a7d596f68015

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$BIN_SHA256"

echo ">> Installing latest Entware installer"
install -d "$ROOTFS_DIR/usr/local/bin"
install -m 755 "$CACHE_DIR/$FILENAME" "$ROOTFS_DIR/usr/local/bin/opkg"
