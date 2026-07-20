#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

echo ">> Checking for fluidd nginx configurations..."
if [[ ! -f "$ROOTFS_DIR/etc/nginx/sites-available/fluidd" ]]; then
  echo "ERROR: fluidd not found at $ROOTFS_DIR/etc/nginx/sites-available/fluidd"
  exit 1
fi
if [[ ! -L "$ROOTFS_DIR/etc/nginx/sites-enabled/fluidd" ]]; then
  echo "ERROR: fluidd not found at $ROOTFS_DIR/etc/nginx/sites-enabled/fluidd"
  exit 1
fi

VERSION=v2.18.2
URL=https://github.com/mainsail-crew/mainsail/releases/download/$VERSION/mainsail.zip
SHA256=df2ba7c301f7bfc8ac9f122741a6ba08356d679ecfa1f62f898d0337802d5de5
FILENAME=mainsail-$VERSION.zip

rm -rf "$ROOTFS_DIR/home/lava/mainsail"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/home/lava/mainsail"
