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

echo ">> Copy printer data from original rootfs to new rootfs ..."
cp -rv "${ORG_ROOTFS_DIR}/home/lava/origin_printer_data" "${ROOTFS_DIR}/opt/lava/printer_data"
