#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

VERSION=v1.37.2
URL=https://github.com/fluidd-core/fluidd/releases/download/$VERSION/fluidd.zip
SHA256=e42d4e8b14a3a0b20573485c882cc4dcfac33d9fbd946c8803a942be282e2b6e
FILENAME=fluidd-$VERSION.zip

rm -rf "$ROOTFS_DIR/home/lava/fluidd"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/home/lava/fluidd"
