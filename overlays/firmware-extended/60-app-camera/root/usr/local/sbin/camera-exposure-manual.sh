#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @RrrrobertB1992

# Switches rkaiq_3A_server to manual exposure for the internal camera.
#
# rkaiq applies auto exposure as set in the sensor tuning files in
# /etc/iqfiles, and overrides exposure and analogue_gain set through
# control-v4l2. Copies of the tuning files with "AecOpType" set to
# "RK_AIQ_OP_MODE_MANUAL" are bind-mounted over the originals, so white
# balance and the rest of the ISP tuning keep working.
#
# rkaiq only reads the tuning files when it starts, and restarting it while
# the camera streams stalls the VI channel, so this runs from the lmd hook
# before unisrv starts.

IQ_DIR=/etc/iqfiles
MANUAL_DIR=/tmp/camera-iq-manual

if grep -q " $IQ_DIR/" /proc/mounts; then
    exit 0
fi

echo "Switching the internal camera to manual exposure"

/etc/init.d/S40rkaiq_3A stop >/dev/null 2>&1
i=0
while pidof rkaiq_3A_server >/dev/null; do
    if [ "$i" -ge 10 ]; then
        echo "rkaiq_3A_server did not stop"
        exit 1
    fi
    sleep 1
    i=$((i + 1))
done

mkdir -p "$MANUAL_DIR"
for f in "$IQ_DIR"/*.json; do
    grep -q '"AecOpType":[[:space:]]*"RK_AIQ_OP_MODE_AUTO"' "$f" || continue
    sed 's/"AecOpType":\([[:space:]]*\)"RK_AIQ_OP_MODE_AUTO"/"AecOpType":\1"RK_AIQ_OP_MODE_MANUAL"/' "$f" > "$MANUAL_DIR/${f##*/}"
    mount --bind "$MANUAL_DIR/${f##*/}" "$f"
done

rm -f /tmp/.rkaiq_3A
/etc/init.d/S40rkaiq_3A start
