#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../../../..")"
ROOTFS_DIR="$(realpath "$1")"
ORG_ROOTFS_DIR="${ROOTFS_DIR}.org"

set -e

mkdir_chroot() {
  for dir; do
    echo ">> Creating directory $dir in chroot..."
    mkdir -p "$ROOTFS_DIR/$dir"
  done
}

copy_chroot() {
  local target="$1"
  shift

  echo ">> Copying to $target the $@..."
  for src; do
    cp -rv -L --remove-destination "$ORG_ROOTFS_DIR/$src" "$ROOTFS_DIR/$target"
  done
}

in_chroot() {
  "$ROOT_DIR/scripts/helpers/chroot_firmware.sh" "$ROOTFS_DIR" bash -c "$@"
}

echo ">> Copying Klipper from /home/lava/klipper to /opt/klipper..."
mkdir_chroot /opt/klipper
copy_chroot /opt/klipper/ /home/lava/klipper/.

echo ">> Installing klipper requirements..."
in_chroot '/opt/venv/bin/pip3 install --upgrade pip'
in_chroot '/opt/venv/bin/pip3 install -r /opt/klipper/scripts/klippy-requirements.txt'

# Additional modules not included in klippy-requirements.txt
echo ">> Installing additional klipper requirements..."
in_chroot '/opt/venv/bin/pip3 install paho-mqtt spidev cryptography "numpy<2.0"'

echo ">> Setting ownership to lava:lava..."
in_chroot 'chown -R lava:lava /opt/klipper'

echo ">> Klipper installation complete"
