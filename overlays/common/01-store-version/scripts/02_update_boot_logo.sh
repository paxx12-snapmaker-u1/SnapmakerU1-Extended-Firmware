#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12, @liberodark

set -e

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

UNPACK_DIR="$BUILD_DIR/boot-unpacked"

if [[ ! -f "$BOOT_IMG" ]]; then
  echo "Error: boot.img not found at $BOOT_IMG"
  exit 1
fi

rm -rf "$UNPACK_DIR"

echo ">> Unpacking boot.img to $UNPACK_DIR"
boot_fit.sh extract "$BOOT_IMG" "$UNPACK_DIR"

PROFILE_STR="custom"
[[ -n "$PROFILE" ]] && PROFILE_STR="$PROFILE"

VERSION_STR="$(git describe --abbrev --always)"
[[ -n "$GIT_VERSION" ]] && VERSION_STR="$GIT_VERSION-$VERSION_STR"

BUILD_DATE_STR="$(date "+%Y-%m-%d %H:%M")"

add_string() {
  local f="$1"
  local x="$2"
  local y="$3"
  local fs="$4"
  local fc="$5"
  shift 5
  local text="${*//:/\\:}"
  ffmpeg -y -i "$f" \
    -vf "drawtext=text='$text':x=$x:y=$y:fontsize=$fs:fontcolor=$fc:\
      fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" \
    -c:v bmp \
    "$f.new.bmp"
  mv "$f.new.bmp" "$f"
}

echo ">> Replacing logo.bmp in boot image"
for i in "$UNPACK_DIR/resources/"*.bmp; do
  # align with the gui spinner
  add_string "$i" "w/2-text_w/2" "h*220/320-ascent/2" 16 white "$PROFILE_STR"

  # align bottom left
  add_string "$i" "w-text_w-10" "h-ascent-10" 12 white "$VERSION_STR"

  # align bottom right
  add_string "$i" "10" "h-ascent-10" 12 white "$BUILD_DATE_STR"
done

echo ">> Repacking boot.img"
boot_fit.sh repack "$UNPACK_DIR" "$BOOT_IMG"
