#!/usr/bin/env bash

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "Usage: $0 <target-file> <url> <sha256> [extract-dir]"
  exit 1
fi

TARGET="$1"
URL="$2"
SHA256="$3"
EXTRACT_DIR="$4"

TARGET_DIR="$(dirname "$TARGET")"
FILENAME="$(basename "$TARGET")"

set -e
mkdir -p "$TARGET_DIR"

if [[ ! -f "$TARGET" ]]; then
  echo ">> Downloading $FILENAME..."
  wget -O "$TARGET" "$URL"
fi

echo ">> Verifying $TARGET checksum..."
if ! echo "$SHA256  $TARGET" | sha256sum --check --status; then
  echo "[!] SHA256 checksum mismatch for $FILENAME"
  exit 1
fi

if [[ -z "$EXTRACT_DIR" ]]; then
  exit 0
fi

rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"

case "$FILENAME" in
  *.tar.gz|*.tgz)
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
