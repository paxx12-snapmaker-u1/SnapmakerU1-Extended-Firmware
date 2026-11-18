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

make -C "$ROOT_DIR/apps/v4l2-imposter" CROSS_COMPILE=aarch64-linux-gnu-
scp "$ROOT_DIR/apps/v4l2-imposter/libv4l2-imposter.so" "$SSH_HOST":/usr/local/lib/

ssh -t "$SSH_HOST" "
  export LD_PRELOAD=/usr/local/lib/libv4l2-imposter.so
  export V4L2_IMPOSTER_SOCKET_PATH=/tmp/capture-mipi-raw.sock
  export V4L2_IMPOSTER_DEVICE=/dev/video11
  export V4L2_IMPOSTER_WIDTH=1920
  export V4L2_IMPOSTER_HEIGHT=1080
  export V4L2_IMPOSTER_FORMAT=nv12
  export V4L2_IMPOSTER_DEBUG=1
  /usr/bin/unisrv -t MIPI -d
"
