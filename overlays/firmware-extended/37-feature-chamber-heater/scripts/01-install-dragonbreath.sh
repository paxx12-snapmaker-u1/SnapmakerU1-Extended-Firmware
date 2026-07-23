#!/usr/bin/env bash

GIT_URL=https://github.com/plastikman/dragonbreath-klipper.git
GIT_SHA=bfbf2613082f180074598183b477e12f3e189c6f

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/dragonbreath-klipper"
LAVA_UID=1000
LAVA_GID=1000

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing DragonBreath Klipper extras..."
install -Dm644 -o "$LAVA_UID" -g "$LAVA_GID" "$TARGET_DIR/dragonbreath.py" \
  "$ROOTFS_DIR/home/lava/klipper/klippy/extras/dragonbreath.py"

echo ">> DragonBreath installation completed successfully."
