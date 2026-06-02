#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

VERSION=v1.37.1
URL=https://github.com/fluidd-core/fluidd/releases/download/$VERSION/fluidd.zip
SHA256=f08e9d438fdce472553e1ce46a9be62f5ababb4b0f64f65efbd4561d9379653c
FILENAME=fluidd-$VERSION.zip

rm -rf "$ROOTFS_DIR/home/lava/fluidd"

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR/home/lava/fluidd"
