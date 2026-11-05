# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CAMERA_INTERNAL=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal snapmaker)
    if [ "$CAMERA_INTERNAL" = paxx12 ]; then
        # The v4l2-camera-socket libv4l2 plugin serves this device to unisrv.
        echo "Starting lmd in v4l2-camera-socket mode!"
        export V4L2_CAMERA_SOCKET_PATH=/tmp/capture-mipi-raw.sock
        export V4L2_CAMERA_SOCKET_DEVICE=/dev/video11
        export V4L2_CAMERA_SOCKET_WIDTH=1920
        export V4L2_CAMERA_SOCKET_HEIGHT=1080
        export V4L2_CAMERA_SOCKET_FORMAT=nv12
    elif [ "$CAMERA_INTERNAL" != snapmaker ]; then
        echo "Internal camera is not set to 'snapmaker', not starting lmd."
        exit 0
    fi
fi
