#!/usr/bin/env bash

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOTFS_DIR="$1"

echo ">> Wrapping unisrv binary..."
mv "$ROOTFS_DIR/usr/bin/unisrv" "$ROOTFS_DIR/usr/bin/unisrv.org-camera"
