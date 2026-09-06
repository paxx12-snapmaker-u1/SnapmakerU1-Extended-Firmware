#!/usr/bin/env bash

GIT_URL=https://github.com/AFCProject/AFC-Klipper-Add-On.git
GIT_REF=main

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/AFC-Klipper-Add-On"
LAVA_UID=1000
LAVA_GID=1000
DEST_DIR="$ROOTFS_DIR/home/lava/AFC-Klipper-Add-On"

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_REF"
# Pull to verify that local main is up to date with remote
git -C "$TARGET_DIR" pull

echo ">> Installing AFC-Klipper-Add-On..."
rm -rf "$DEST_DIR"
cp -a "$TARGET_DIR" "$DEST_DIR"
rm -rf "$DEST_DIR/.git"
chown -R "$LAVA_UID:$LAVA_GID" "$DEST_DIR"

echo ">> AFC-Klipper-Add-On installation completed successfully."
