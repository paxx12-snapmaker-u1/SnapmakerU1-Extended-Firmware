#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

GIT_URL=https://github.com/paxx12/v4l2-mpp.git
GIT_SHA=10fc3b9d935d9c79bacc014839c05de4a004c4ac

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
file "$1/usr/local/bin/capture-v4l2-jpeg-mpp"
file "$1/usr/local/bin/capture-v4l2-raw-mpp"
file "$1/usr/local/bin/fake-service"
file "$1/usr/local/bin/stream-rtsp"
file "$1/usr/local/bin/stream-webrtc"
file "$1/usr/local/bin/stream-snap-mqtt.py"
file "$1/usr/local/bin/stream-http.py"

echo ">> Creating timelapse directory..."
mkdir -p "$1/userdata/.tmp_timelapse"

echo ">> Installing Python dependencies..."
"$ROOT_DIR/scripts/helpers/chroot_firmware.sh" "$1" /opt/venv/bin/pip3 install paho-mqtt

echo ">> v4l2-mpp installation completed successfully."
