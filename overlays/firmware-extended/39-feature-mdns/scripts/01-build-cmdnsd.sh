#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

APP_DIR="$(dirname "${BASH_SOURCE[0]}")/../apps/cmdnsd"

echo ">> Cross-compiling cmdnsd..."
make -C "$APP_DIR" clean
make -C "$APP_DIR" CROSS_COMPILE=aarch64-linux-gnu- strip

echo ">> Installing cmdnsd..."
make -C "$APP_DIR" install DESTDIR="$ROOTFS_DIR"

echo ">> Validate binary..."
stat "$ROOTFS_DIR/usr/local/sbin/cmdnsd" >/dev/null
echo ">> cmdnsd installation completed successfully."
