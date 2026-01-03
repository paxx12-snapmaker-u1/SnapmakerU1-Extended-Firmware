#!/usr/bin/env bash

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../../../..")"
DEST="$1/home/lava/www/rfid-manager/lib/PrintTag-Web"

echo ">> Installing PrintTag-Web JS libraries"
mkdir -p "$DEST"
cp "$ROOT_DIR/deps/PrintTag-Web/public/ndef.js" "$DEST/"
cp "$ROOT_DIR/deps/PrintTag-Web/public/openspool.js" "$DEST/"
