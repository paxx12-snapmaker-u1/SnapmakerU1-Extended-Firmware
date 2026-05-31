#!/usr/bin/env bash

GIT_URL=https://github.com/suchmememanyskill/OpenRFID.git
GIT_SHA=ddd1609e9abe9cd37c4b8fa1a0e4307b976d5fd4

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/OpenRFID"
cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing OpenRFID..."
cd "$TARGET_DIR"
make install DESTDIR="$ROOTFS_DIR/usr/local/share/openrfid"
echo ">> OpenRFID installation completed successfully."
