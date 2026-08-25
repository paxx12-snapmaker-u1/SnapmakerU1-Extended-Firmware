#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

EXPECTED_SHA256="d8a4c2a201b8636c399ee7dde5bd514876ae7868f5ccda6ded95e79ec70fb172"

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
