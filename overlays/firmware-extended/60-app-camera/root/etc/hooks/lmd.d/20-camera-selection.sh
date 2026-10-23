# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CONFIG_HELPER=${EXTENDED_CONFIG_BIN:-/usr/local/bin/extended-config.py}
    if ! CAMERA_INTERNAL=$("$CONFIG_HELPER" get "$EXTENDED_CFG" camera internal snapmaker); then
        echo "Could not read internal camera setting; using Snapmaker camera."
        CAMERA_INTERNAL=snapmaker
    fi

    case "$CAMERA_INTERNAL" in
        paxx12)
            echo "Starting lmd in v4l2-imposter mode!"
            export LD_PRELOAD="/usr/local/lib/libv4l2-imposter.so${LD_PRELOAD:+:$LD_PRELOAD}"
            export V4L2_IMPOSTER_SOCKET_PATH=/tmp/capture-mipi-raw.sock
            export V4L2_IMPOSTER_DEVICE=/dev/video11
            export V4L2_IMPOSTER_WIDTH=1920
            export V4L2_IMPOSTER_HEIGHT=1080
            export V4L2_IMPOSTER_FORMAT=nv12
            ;;
        snapmaker)
            ;;
        none)
            echo "Internal camera is disabled, not starting lmd."
            exit 0
            ;;
        *)
            echo "Unknown internal camera setting '$CAMERA_INTERNAL'; using Snapmaker camera."
            CAMERA_INTERNAL=snapmaker
            ;;
    esac
fi
