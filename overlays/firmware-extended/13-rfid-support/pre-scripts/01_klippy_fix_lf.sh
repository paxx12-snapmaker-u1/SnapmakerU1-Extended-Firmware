#!/usr/bin/env bash

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../../../..")"

dos2unix "$1/home/lava/klipper/klippy/extras/filament_detect.py" \
  "$1/home/lava/klipper/klippy/extras/fm175xx_reader.py"
