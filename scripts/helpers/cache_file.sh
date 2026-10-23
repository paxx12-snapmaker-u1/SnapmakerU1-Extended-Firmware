#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "Usage: $0 <target-file> <url> <sha256> [extract-dir]"
  exit 1
fi

TARGET="$1"
URL="$2"
SHA256="$3"
EXTRACT_DIR="${4:-}"

TARGET_DIR="$(dirname "$TARGET")"
FILENAME="$(basename "$TARGET")"

set -euo pipefail
mkdir -p "$TARGET_DIR"

TEMP_TARGET=""
cleanup() {
  if [[ -n "$TEMP_TARGET" ]]; then
    rm -f -- "$TEMP_TARGET"
  fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

hash_matches() {
  printf '%s  %s\n' "$SHA256" "$1" | sha256sum --check --status
}

if [[ -f "$TARGET" ]] && hash_matches "$TARGET"; then
  echo ">> Verified cached $FILENAME"
else
  if [[ -f "$TARGET" ]]; then
    echo "[!] Cached checksum mismatch for $FILENAME; downloading a replacement"
  fi
  echo ">> Downloading $FILENAME..."
  TEMP_TARGET="$(mktemp "$TARGET.tmp.XXXXXX")"
  if ! wget -O "$TEMP_TARGET" "$URL"; then
    echo "[!] Download failed for $FILENAME" >&2
    exit 1
  fi
  if ! hash_matches "$TEMP_TARGET"; then
    echo "[!] SHA256 checksum mismatch for downloaded $FILENAME" >&2
    exit 1
  fi
  # mktemp creates a private file; keep verified build-cache assets readable
  # by subsequent build users and the CI cache uploader.
  chmod 0644 "$TEMP_TARGET"
  mv -f -- "$TEMP_TARGET" "$TARGET"
  TEMP_TARGET=""
fi

if [[ -z "$EXTRACT_DIR" ]]; then
  exit 0
fi

rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"

case "$FILENAME" in
  *.tar.gz)
    echo ">> Extracting $FILENAME..."
    tar -xzf "$TARGET" -C "$EXTRACT_DIR"
    ;;

  *.tar.xz)
    echo ">> Extracting $FILENAME..."
    tar -xJf "$TARGET" -C "$EXTRACT_DIR"
    ;;

  *.zip)
    echo ">> Extracting $FILENAME..."
    unzip -o "$TARGET" -d "$EXTRACT_DIR"
    ;;

  *)
    ;;
esac
