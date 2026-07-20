#!/usr/bin/env bash

GIT_URL=https://github.com/plastikman/pandabreath-klipper.git
GIT_SHA=be56938a2e65836daede87444070a63af060da35

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/pandabreath-klipper"
LAVA_UID=1000
LAVA_GID=1000

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Installing Panda Breath Klipper extras..."
install -Dm644 -o "$LAVA_UID" -g "$LAVA_GID" "$TARGET_DIR/panda_breath.py" \
  "$ROOTFS_DIR/home/lava/klipper/klippy/extras/panda_breath.py"

echo ">> Panda Breath installation completed successfully."
