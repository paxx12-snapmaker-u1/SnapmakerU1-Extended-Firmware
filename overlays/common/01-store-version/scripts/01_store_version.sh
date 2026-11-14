#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

ABBRV=$(git describe --abbrev --always)

mv "$ROOTFS_DIR/etc/FULLVERSION" "$ROOTFS_DIR/etc/BASE_VERSION"

# Stock S49mqtt-broker takes the MQTT certificate start date from the text
# after the first `_` in /etc/FULLVERSION. That file is replaced below, so
# point the script at the stock version string in /etc/BASE_VERSION.
MQTT_BROKER="$ROOTFS_DIR/etc/init.d/S49mqtt-broker"
if [[ "$(grep -cF '$(cat /etc/FULLVERSION)' "$MQTT_BROKER")" != "1" ]]; then
  echo "Error: expected exactly one '\$(cat /etc/FULLVERSION)' in $MQTT_BROKER"
  exit 1
fi
sed -i 's#\$(cat /etc/FULLVERSION)#$(cat /etc/BASE_VERSION 2>/dev/null || cat /etc/FULLVERSION)#' "$MQTT_BROKER" || exit 1
grep -n 'cat /etc/BASE_VERSION' "$MQTT_BROKER" || exit 1

if [[ "$GIT_VERSION" == *"-$ABBRV" ]]; then
  # 0.9.0-paxx12-1-gabcdef0
  echo "${GIT_VERSION#v}" > "$ROOTFS_DIR/etc/FULLVERSION"
  echo "${GIT_VERSION#v}" > "$ROOTFS_DIR/etc/BUILD_VERSION"
elif [[ -n "$GIT_VERSION" ]]; then
  # 0.9.0-paxx12-1-gabcdef0
  echo "${GIT_VERSION#v}" > "$ROOTFS_DIR/etc/FULLVERSION"
  echo "${GIT_VERSION#v}-${ABBRV}" > "$ROOTFS_DIR/etc/BUILD_VERSION"
else
  # <git-branch-name>-<abbr>
  GIT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  echo "${GIT_BRANCH}" > "$ROOTFS_DIR/etc/FULLVERSION"
  echo "${GIT_BRANCH}-${ABBRV}" > "$ROOTFS_DIR/etc/BUILD_VERSION"
fi

if [[ -n "$PROFILE" ]]; then
  echo "$PROFILE" > "$ROOTFS_DIR/etc/BUILD_PROFILE"
fi
