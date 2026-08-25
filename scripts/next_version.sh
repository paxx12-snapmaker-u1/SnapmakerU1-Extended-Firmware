#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

ROOT_DIR="$(realpath "$(dirname "$0")/..")"

source "$ROOT_DIR/vars.mk"

CODENAME="$1"
VERSION="$2"

if [[ -n "$VERSION" ]]; then
  echo "v$FIRMWARE_VERSION-$CODENAME-$VERSION"
  exit 0
fi

lastVer=$(git tag --sort version:refname --list "*-$CODENAME-*" | tail -n1)
buildVer=1

if [[ -n "$lastVer" ]]; then
  newVer=(${lastVer//-/ })
  buildVer="$((${newVer[-1]}+1))"
fi

echo "v$FIRMWARE_VERSION-$CODENAME-$buildVer"
