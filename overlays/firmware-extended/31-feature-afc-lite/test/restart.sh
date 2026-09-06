#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

ROOT_DIR="$(dirname "$(realpath "$0")")/.."

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <host>"
  exit 1
fi

set -xeo pipefail

scp -r "$ROOT_DIR/root/." "$1":/
ssh -t "$1" /etc/init.d/S60klipper restart
