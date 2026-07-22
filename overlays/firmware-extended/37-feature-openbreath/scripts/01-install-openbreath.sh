#!/usr/bin/env bash

GIT_URL=https://github.com/plastikman/openbreath-klipper.git
GIT_SHA=85ce0379fb510d370ec92001738e664891125c48

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/openbreath-klipper"
LAVA_UID=1000
LAVA_GID=1000

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing OpenBreath Klipper extras..."
install -Dm644 -o "$LAVA_UID" -g "$LAVA_GID" "$TARGET_DIR/openbreath.py" \
  "$ROOTFS_DIR/home/lava/klipper/klippy/extras/openbreath.py"

echo ">> OpenBreath installation completed successfully."
