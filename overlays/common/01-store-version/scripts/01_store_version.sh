#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

ABBRV=$(git describe --abbrev --always)

if [[ -n "$GIT_VERSION" ]]; then
  # 0.9.0-paxx12-1-gabcdef0
  echo "${GIT_VERSION#v}-${ABBRV}" > "$ROOTFS_DIR/etc/BUILD_VERSION"
else
  # <git-branch-name>-<abbr>
  GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  echo "${GIT_BRANCH}-${ABBRV}" > "$ROOTFS_DIR/etc/BUILD_VERSION"
fi

if [[ -n "$PROFILE" ]]; then
  echo "$PROFILE" > "$ROOTFS_DIR/etc/BUILD_PROFILE"
fi
