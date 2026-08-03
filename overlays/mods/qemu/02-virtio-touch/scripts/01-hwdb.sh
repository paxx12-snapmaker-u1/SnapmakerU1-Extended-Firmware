#!/usr/bin/env bash

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

echo ">> Rebuilding udev hwdb inside rootfs"
chroot_firmware.sh "$ROOTFS_DIR" /usr/bin/udevadm hwdb --update
