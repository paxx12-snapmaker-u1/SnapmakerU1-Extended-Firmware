# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CAMERA_INTERNAL=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal paxx12)
    if [ "$CAMERA_INTERNAL" = paxx12 ]; then
        # unisrv owns the camera, the v4l2-camera-publish libv4l2 plugin
        # publishes its frames to capture-raw-mpp.
        echo "Starting lmd in v4l2-camera-publish mode!"
        export V4L2_CAMERA_PUBLISH_PATH=/tmp/capture-mipi-input.sock
        export V4L2_CAMERA_PUBLISH_DEVICE=/dev/video11
    elif [ "$CAMERA_INTERNAL" != snapmaker ]; then
        echo "Internal camera is not set to 'snapmaker', not starting lmd."
        exit 0
    fi
fi
