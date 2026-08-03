if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    CAMERA_INTERNAL=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" camera internal snapmaker)
    if [ "$CAMERA_INTERNAL" = paxx12 ]; then
        echo "Starting lmd in v4l2-imposter mode!"
        export LD_PRELOAD="/usr/local/lib/libv4l2-imposter.so${LD_PRELOAD:+:$LD_PRELOAD}"
        export V4L2_IMPOSTER_SOCKET_PATH=/tmp/capture-mipi-raw.sock
        export V4L2_IMPOSTER_DEVICE=/dev/video11
        export V4L2_IMPOSTER_WIDTH=1920
        export V4L2_IMPOSTER_HEIGHT=1080
        export V4L2_IMPOSTER_FORMAT=nv12
    elif [ "$CAMERA_INTERNAL" != snapmaker ]; then
        echo "Internal camera is not set to 'snapmaker', not starting lmd."
        exit 0
    fi
fi
