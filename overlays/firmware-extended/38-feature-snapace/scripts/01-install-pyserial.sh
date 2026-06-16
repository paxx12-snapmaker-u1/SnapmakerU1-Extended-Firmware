#!/usr/bin/env bash
set -euo pipefail

ROOTFS_DIR="$1"

cache_pip.sh "$ROOTFS_DIR" pyserial
