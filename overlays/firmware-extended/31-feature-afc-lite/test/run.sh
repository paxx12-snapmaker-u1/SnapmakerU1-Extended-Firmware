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
scp "$ROOT_DIR/../../../scripts/dev/run-klipper.sh" "$ROOT_DIR/test/printer.cfg" "$1":/tmp/
ssh -t "$1" /tmp/run-klipper.sh /tmp/printer.cfg
