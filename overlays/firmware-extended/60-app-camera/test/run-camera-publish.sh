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

make -C "$ROOT_DIR/apps/v4l2-camera-publish" CROSS_COMPILE=aarch64-linux-gnu-
scp "$ROOT_DIR/apps/v4l2-camera-publish/libv4l-camera-publish.so" "$SSH_HOST":/usr/lib/libv4l/plugins/

ssh -t "$SSH_HOST" "
  /usr/local/bin/capture-raw-mpp --input-sock /tmp/capture-mipi-input.sock --jpeg-sock /tmp/capture-mipi-jpeg.sock --mjpeg-sock /tmp/capture-mipi-mjpeg.sock --h264-sock /tmp/capture-mipi-h264.sock &
  trap 'kill \$!' EXIT
  export V4L2_CAMERA_PUBLISH_PATH=/tmp/capture-mipi-input.sock
  export V4L2_CAMERA_PUBLISH_DEVICE=/dev/video11
  export V4L2_CAMERA_PUBLISH_DEBUG=1
  /usr/bin/unisrv
"
