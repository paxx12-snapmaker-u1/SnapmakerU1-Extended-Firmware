#!/usr/bin/env bash

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

dos2unix "$1/home/lava/klipper/klippy/extras/filament_protocol.py" \
  "$1/home/lava/moonraker/moonraker/components/spoolman.py"
