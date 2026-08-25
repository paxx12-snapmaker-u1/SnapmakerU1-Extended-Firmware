#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <rootfs> [pip params]"
  exit 1
fi

ROOTFS_DIR="$1"
shift

chroot_cmd() {
  chroot_firmware.sh "$ROOTFS_DIR" "$@"
}

chroot_cmd bash -c '
  pip3 install --no-index --find-links=/cache/pip "$@" ||
  (
    pip3 download -d /cache/pip "$@" &&
    pip3 install --no-index --find-links=/cache/pip "$@"
  )
' -- "$@"
