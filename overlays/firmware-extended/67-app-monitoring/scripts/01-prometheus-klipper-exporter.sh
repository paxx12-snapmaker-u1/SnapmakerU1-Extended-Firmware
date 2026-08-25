#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -e

GIT_URL=https://github.com/scross01/prometheus-klipper-exporter.git
GIT_SHA=9eacec280108a4da8156b47c01c2862219d86ecd

TARGET_DIR="$CACHE_DIR/prometheus-klipper-exporter"

cache_git.sh "$TARGET_DIR" "$GIT_URL" "$GIT_SHA"

echo ">> Compiling prometheus-klipper-exporter..."
# Note: Requires Go toolchain in build environment
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go -C "$TARGET_DIR" build -o "$1/usr/local/bin/prometheus-klipper-exporter" .

echo ">> Validate binaries..."
stat "$1/usr/local/bin/prometheus-klipper-exporter" >/dev/null
