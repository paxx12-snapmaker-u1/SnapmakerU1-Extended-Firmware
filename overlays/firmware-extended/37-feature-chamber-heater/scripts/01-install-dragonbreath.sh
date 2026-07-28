#!/usr/bin/env bash

GIT_URL=https://github.com/plastikman/dragonbreath-klipper.git
# v2 helper (API v2 client) — dragonbreath-klipper main @ #5: adds the fan-only
# filtration blower as [output_pin dragonbreath_filter]. Pairs with DragonBreath
# firmware >= v0.6.5 (the `filter` command). Flash firmware first, then restart
# klippy.
GIT_SHA=88a6dacf5d8cc692fb5c5a4c508325f5f82b0cce

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
