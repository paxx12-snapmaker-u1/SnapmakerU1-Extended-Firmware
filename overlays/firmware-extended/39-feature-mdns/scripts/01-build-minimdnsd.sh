#!/usr/bin/env bash

GIT_URL=https://github.com/cnlohr/minimdnsd.git
GIT_SHA=dbff97c09da925a08293fee5c270799d533ce1a9

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/minimdnsd"
cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Cross-compiling minimdnsd..."
aarch64-linux-gnu-gcc -o "$TARGET_DIR/minimdnsd" "$TARGET_DIR/minimdnsd.c" \
  -Wall -pedantic -Os -flto -ffunction-sections -Wl,--gc-sections -fdata-sections

echo ">> Installing minimdnsd..."
install -d "$1/usr/local/bin"
install -m 755 "$TARGET_DIR/minimdnsd" "$1/usr/local/bin/minimdnsd"

echo ">> Validate binary..."
stat "$1/usr/local/bin/minimdnsd" >/dev/null
echo ">> minimdnsd installation completed successfully."
