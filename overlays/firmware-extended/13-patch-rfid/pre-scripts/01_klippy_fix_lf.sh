#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../../../..")"

dos2unix "$1/home/lava/klipper/klippy/extras/filament_detect.py" \
  "$1/home/lava/klipper/klippy/extras/filament_protocol.py" \
  "$1/home/lava/klipper/klippy/extras/fm175xx_reader.py"
