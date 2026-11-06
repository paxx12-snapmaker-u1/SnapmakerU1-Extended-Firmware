# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CAMERA_INTERNAL=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal snapmaker)
    if [ "$CAMERA_INTERNAL" = paxx12 ]; then
        # Stock 2.1.0 unisrv captures through librockit. It keeps owning the
        # internal camera, and librockit-export.so encodes frames from its
        # unused VI channel into the paxx12 camera sockets.
        INTERNAL_RESOLUTION=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal_resolution 1080p25)
        case "$INTERNAL_RESOLUTION" in
            1080p30) INTERNAL_FPS=30 ;;
            1080p25) INTERNAL_FPS=25 ;;
            1080p15) INTERNAL_FPS=15 ;;
            1080p5)  INTERNAL_FPS=5 ;;
            *)       INTERNAL_FPS=25 ;;
        esac
        echo "Starting lmd with the internal camera exported from unisrv!"
        export LD_PRELOAD="/usr/local/lib/librockit-export.so${LD_PRELOAD:+:$LD_PRELOAD}"
        export ROCKIT_EXPORT_FPS=$INTERNAL_FPS
        unset INTERNAL_RESOLUTION INTERNAL_FPS
    elif [ "$CAMERA_INTERNAL" != snapmaker ]; then
        echo "Internal camera is not set to 'snapmaker', not starting lmd."
        exit 0
    fi
fi
