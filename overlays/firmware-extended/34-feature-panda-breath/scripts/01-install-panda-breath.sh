#!/usr/bin/env bash

GIT_URL=https://github.com/justinh-rahb/pandabreath-klipper.git
GIT_SHA=2fc8c03b918519060f0a2cc6b40a56fbc232e74f

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
  "$ROOTFS_DIR/usr/local/share/firmware-config/tweaks/klipper/panda_breath.py"

echo ">> Panda Breath installation completed successfully."
