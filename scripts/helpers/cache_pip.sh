#!/usr/bin/env bash

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <rootfs> [pip params]"
  exit 1
fi

ROOTFS_DIR="$1"
shift

chroot_cmd() {
  chroot_firmware.sh "$ROOTFS_DIR" "$@"
}

chroot_cmd bash -c '
  pip3 install --no-index --find-links=/cache/pip "$@" ||
  (
    pip3 download -d /cache/pip "$@" &&
    pip3 install --no-index --find-links=/cache/pip "$@"
  )
' -- "$@"
