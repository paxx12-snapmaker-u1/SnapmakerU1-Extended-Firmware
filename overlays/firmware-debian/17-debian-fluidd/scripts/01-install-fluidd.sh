#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

VERSION=v1.36.3
URL=https://github.com/fluidd-core/fluidd/releases/download/$VERSION/fluidd.zip
SHA256=c341e43065c3a08c22ff1b5122e9e8b934c278f39df782bb476b35eed1799c5c
FILENAME=fluidd-$VERSION.zip

rm -rf "$ROOTFS_DIR/opt/fluidd"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/opt/fluidd"
