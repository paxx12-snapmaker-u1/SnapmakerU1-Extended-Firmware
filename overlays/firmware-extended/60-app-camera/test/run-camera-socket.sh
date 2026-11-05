#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

ROOT_DIR="$(dirname "$(realpath "$0")")/.."

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <host> [additional options]"
  exit 1
fi

SSH_HOST="$1"
shift

set -xeo pipefail

make -C "$ROOT_DIR/apps/v4l2-camera-socket" CROSS_COMPILE=aarch64-linux-gnu-
scp "$ROOT_DIR/apps/v4l2-camera-socket/libv4l-camera-socket.so" "$SSH_HOST":/usr/lib/libv4l/plugins/

ssh -t "$SSH_HOST" "
  export V4L2_CAMERA_SOCKET_PATH=/tmp/capture-mipi-raw.sock
  export V4L2_CAMERA_SOCKET_DEVICE=/dev/video11
  export V4L2_CAMERA_SOCKET_WIDTH=1920
  export V4L2_CAMERA_SOCKET_HEIGHT=1080
  export V4L2_CAMERA_SOCKET_FORMAT=nv12
  export V4L2_CAMERA_SOCKET_DEBUG=1
  /usr/bin/unisrv -t MIPI -d
"
