#!/usr/bin/env bash

GIT_URL=https://github.com/paxx12/v4l2-mpp.git
GIT_SHA=a8cd3ad5acc33bab3475aea151e5a50002b742bb

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

TARGET_DIR="$CACHE_DIR/v4l2-mpp"
cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Setting up cross-compilation environment..."
export CROSS_COMPILE=aarch64-linux-gnu-
export CC="${CROSS_COMPILE}gcc"
export CXX="${CROSS_COMPILE}g++"
export AR="${CROSS_COMPILE}ar"
export RANLIB="${CROSS_COMPILE}ranlib"
export STRIP="${CROSS_COMPILE}strip"

echo ">> Compiling dependencies..."
make -C "$TARGET_DIR" deps

echo ">> Compiling v4l2-mpp applications..."
make -C "$TARGET_DIR" install DESTDIR="$1"

echo ">> Validate binaries..."
stat "$1/usr/local/bin/capture-v4l2-jpeg-mpp" >/dev/null
stat "$1/usr/local/bin/capture-v4l2-raw-mpp" >/dev/null
stat "$1/usr/local/bin/stream-rtsp" >/dev/null
stat "$1/usr/local/bin/stream-webrtc" >/dev/null
stat "$1/usr/local/bin/stream-http.py" >/dev/null
stat "$1/usr/local/bin/control-v4l2.py" >/dev/null
echo ">> v4l2-mpp installation completed successfully."
