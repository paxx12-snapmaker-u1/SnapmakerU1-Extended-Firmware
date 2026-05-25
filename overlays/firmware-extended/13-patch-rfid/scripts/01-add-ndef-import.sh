#!/usr/bin/env bash

set -euo pipefail

target="$1/home/lava/klipper/klippy/extras/filament_detect.py"

if grep -Fqx 'from . import filament_protocol_ndef' "$target"; then
  exit 0
fi

sed -i '/^from \. import filament_protocol$/a from . import filament_protocol_ndef' "$target"
