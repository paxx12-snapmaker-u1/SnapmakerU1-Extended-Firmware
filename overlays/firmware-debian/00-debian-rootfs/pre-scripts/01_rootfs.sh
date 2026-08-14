#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOTFS_DIR="$(realpath "$1")"

VERSION=20260111-cb35d27
FILENAME="debian-rootfs-trixie-$VERSION.tgz"
URL="https://github.com/Snapmaker-U1-Extended-Firmware/base-debian-os/releases/download/$VERSION/$FILENAME"
SHA256="1a2d1dec1039ddf55575b0c7c8d818278fa786c1723aa0437ff6547431007063"

set -e

if [[ ! -d "${ROOTFS_DIR}.org" ]]; then
  echo ">> Backuping rootfs..."
  mv "$ROOTFS_DIR"{,.org}
else
  echo ">> Cleaning rootfs..."
  rm -rf "$ROOTFS_DIR"
fi

echo ">> Downloading and extracting debian rootfs tarball..."
cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$ROOTFS_DIR"

# EXTRA_DEPS="iw net-tools wireless-tools"

# if [[ $(cat "$ROOTFS_DIR/.deps" || true) != "$EXTRA_DEPS" ]]; then
#   "$ROOT_DIR/scripts/helpers/chroot_firmware.sh" "$ROOTFS_DIR" bash -c "apt update -y && apt install -y $EXTRA_DEPS"
#   echo "$EXTRA_DEPS" > "$ROOTFS_DIR/.deps"
# fi

echo ">> Debian Bootstrap installed to $ROOTFS_DIR"
