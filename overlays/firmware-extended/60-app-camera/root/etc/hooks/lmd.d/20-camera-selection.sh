# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CAMERA_INTERNAL=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal snapmaker)
    if [ "$CAMERA_INTERNAL" = paxx12 ]; then
        # unisrv asks for raw frames as soon as it starts and stops asking
        # after a few failures, so the MIPI capture sockets must exist
        # first. Start the capture before lmd (without lmd's preloads) and
        # wait for its raw frame socket.
        (unset LD_PRELOAD; /etc/init.d/S99v4l2-mpp-mipi start)
        _camera_wait=0
        while [ ! -S /tmp/capture-mipi-raw.sock ] && [ "$_camera_wait" -lt 10 ]; do
            sleep 1
            _camera_wait=$((_camera_wait + 1))
        done
        [ -S /tmp/capture-mipi-raw.sock ] || echo "MIPI capture socket not ready in time, starting lmd anyway."
        unset _camera_wait

        echo "Starting lmd in v4l2-imposter mode!"
        # libv4l2-imposter: unisrv before 2.1.0 (libv4l2)
        # librockit-imposter: unisrv 2.1.0 and later (librockit VI/VENC)
        export LD_PRELOAD="/usr/local/lib/libv4l2-imposter.so:/usr/local/lib/librockit-imposter.so${LD_PRELOAD:+:$LD_PRELOAD}"
        export V4L2_IMPOSTER_SOCKET_PATH=/tmp/capture-mipi-raw.sock
        export V4L2_IMPOSTER_DEVICE=/dev/video11
        export V4L2_IMPOSTER_WIDTH=1920
        export V4L2_IMPOSTER_HEIGHT=1080
        export V4L2_IMPOSTER_FORMAT=nv12
        export ROCKIT_IMPOSTER_RAW_SOCKET=/tmp/capture-mipi-raw.sock
        export ROCKIT_IMPOSTER_JPEG_SOCKET=/tmp/capture-mipi-jpeg.sock
        export ROCKIT_IMPOSTER_H264_SOCKET=/tmp/capture-mipi-h264.sock
        export ROCKIT_IMPOSTER_WIDTH=1920
        export ROCKIT_IMPOSTER_HEIGHT=1080
    elif [ "$CAMERA_INTERNAL" != snapmaker ]; then
        echo "Internal camera is not set to 'snapmaker', not starting lmd."
        exit 0
    fi
fi
