#!/usr/bin/env bash

EXPECTED_SHA256="da24c3113bf89030246f16ffa8f62c87287173b41f1f99ba6e9a435a968cd8e8"

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <rootfs-dir>"
    exit 1
fi

set -eo pipefail

ROOTFS="$1"
FILE=etc/unisrv/config.json

ACTUAL_SHA256=$(cd "$ROOTFS" && sha256sum "$FILE" | awk '{print $1}')

if [[ -z "$EXPECTED_SHA256" ]]; then
    echo ">> Update EXPECTED_SHA256 in $0 to: $ACTUAL_SHA256"
    exit 1
fi

if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
    echo ">> Checksum mismatch for $FILE (got $ACTUAL_SHA256, expected $EXPECTED_SHA256)"
    echo ">> Review S99detect-rknn and update EXPECTED_SHA256 in $0"
    exit 1
fi

echo ">> Checksum OK."
