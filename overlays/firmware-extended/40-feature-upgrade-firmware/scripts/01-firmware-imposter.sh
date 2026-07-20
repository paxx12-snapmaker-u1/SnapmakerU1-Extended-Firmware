#!/usr/bin/env bash

CUR_DIR="$(realpath "$(dirname "$0")")"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

set -eo pipefail

echo ">> Setting up cross-compilation environment..."
export CROSS_COMPILE=aarch64-linux-gnu-

echo ">> Compiling firmware-imposter..."
make -C "$CUR_DIR/../apps/firmware-imposter" install DESTDIR="$1"

echo ">> Validate binaries..."
stat "$1/usr/local/lib/libfirmware-imposter.so" >/dev/null

echo ">> firmware-imposter installation completed successfully."
