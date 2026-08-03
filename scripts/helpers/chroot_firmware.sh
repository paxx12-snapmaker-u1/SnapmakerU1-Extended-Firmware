#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <rootfs> <cmd> [args...]"
  exit 1
fi

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

ROOTFS="$(realpath "$1")"
shift

cd "$ROOTFS"

cleanup() {
  rm -f ./etc/resolv.conf
  if [[ -e ./etc/resolv.conf.bak || -L ./etc/resolv.conf.bak ]]; then
    mv ./etc/resolv.conf.bak ./etc/resolv.conf
  fi
}

if [[ -e ./etc/resolv.conf || -L ./etc/resolv.conf ]]; then
  mv ./etc/resolv.conf ./etc/resolv.conf.bak
fi

trap 'cleanup' EXIT

echo "nameserver 1.1.1.1" > ./etc/resolv.conf
chroot "$ROOTFS" "$@"
