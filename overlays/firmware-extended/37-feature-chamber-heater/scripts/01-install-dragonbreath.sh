#!/usr/bin/env bash

GIT_URL=https://github.com/plastikman/dragonbreath-klipper.git
# v2 helper (API v2 client) — dragonbreath-klipper#3 merged to main. Pairs with
# DragonBreath firmware >= v0.3.0 (flash firmware first, then restart klippy).
GIT_SHA=ce57757f9eb120ec4806d779b2e1f22ebb95f075

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
