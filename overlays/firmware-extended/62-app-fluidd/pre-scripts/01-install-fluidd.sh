#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

VERSION=v1.37.4
URL=https://github.com/fluidd-core/fluidd/releases/download/$VERSION/fluidd.zip
SHA256=df4502c53e25e8b030e1fc5314f5eccac3de1872fce0216092ea94178432423e
FILENAME=fluidd-$VERSION.zip

rm -rf "$ROOTFS_DIR/home/lava/fluidd"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/home/lava/fluidd"
