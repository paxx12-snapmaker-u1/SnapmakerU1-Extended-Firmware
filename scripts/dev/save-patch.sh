#!/usr/bin/env bash

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <overlay> <patch-name> <file-changed-paths>"
  echo "Example: $0 camera-native 02-disable-wlan-power-save /path/to/02-disable-wlan-power-save.patch"
  exit 1
fi

set -xeo pipefail

EXTRACT_DIR="tmp/extracted-$(sed -n 's/^FIRMWARE_VERSION=//p' vars.mk)"

if [[ ! -d "$EXTRACT_DIR" ]]; then
  echo "$EXTRACT_DIR not found, run 'make extract' first" >&2
  exit 1
fi

if [[ ! -d "$EXTRACT_DIR/rootfs.original" ]]; then
  unsquashfs -d "$EXTRACT_DIR/rootfs.original" "$EXTRACT_DIR/rk-unpacked/rootfs.img"
fi

OVERLAY_NAME="$1"
PATCH_NAME="$2"
shift 2

if [[ ! -d "overlays/$OVERLAY_NAME/patches" ]]; then
  mkdir "overlays/$OVERLAY_NAME/patches"
fi

cd tmp/extracted

for patch_file; do
  diff -uNr {rootfs.original,rootfs}/"$patch_file"
done > "../../overlays/$OVERLAY_NAME/patches/$PATCH_NAME.patch"
