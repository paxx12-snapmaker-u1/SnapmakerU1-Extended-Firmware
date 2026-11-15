#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

CUR_DIR="$(realpath "$(dirname "$0")")"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

set -eo pipefail

echo ">> Setting up cross-compilation environment..."
export CROSS_COMPILE=aarch64-linux-gnu-

echo ">> Compiling gui-screen-fix..."
make -C "$CUR_DIR/../apps/gui-screen-fix" clean install clean DESTDIR="$1"

echo ">> Validate binaries..."
stat "$1/usr/local/lib/libgui-screen-fix.so" >/dev/null

echo ">> gui-screen-fix installation completed successfully."
