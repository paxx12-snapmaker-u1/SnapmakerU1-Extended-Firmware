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

VERSION=v2.17.0
URL=https://github.com/mainsail-crew/mainsail/releases/download/$VERSION/mainsail.zip
SHA256=d010f4df25557d520ccdbb8e42fc381df2288e6a5c72d3838a5a2433c7a31d4e
FILENAME=mainsail-$VERSION.zip

rm -rf "$ROOTFS_DIR/opt/mainsail"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/opt/mainsail"
