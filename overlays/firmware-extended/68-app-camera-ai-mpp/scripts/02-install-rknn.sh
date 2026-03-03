#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
    echo "Error: This script should be run within the create_firmware.sh environment."
    exit 1
fi

set -eo pipefail

RKNN_VERSION=2.3.2
RKNN_FILENAME=rknn_toolkit_lite2-${RKNN_VERSION}-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
RKNN_URL=https://github.com/airockchip/rknn-toolkit2/raw/refs/heads/master/rknn-toolkit-lite2/packages/$RKNN_FILENAME
RKNN_SHA256=bda74f1179e15fccb8726054a24898982522784b65bb340b20146955d254e800

echo ">> Downloading RKNN Toolkit Lite2..."
cache_file.sh "$CACHE_DIR/$RKNN_FILENAME" "$RKNN_URL" "$RKNN_SHA256"

echo ">> Installing RKNN Toolkit Lite2..."
cp "$CACHE_DIR/$RKNN_FILENAME" "$ROOTFS_DIR/tmp/$RKNN_FILENAME"
chroot_firmware.sh "$ROOTFS_DIR" python3 -m pip install /tmp/$RKNN_FILENAME
rm "$ROOTFS_DIR/tmp/$RKNN_FILENAME"

echo ">> Installing opencv and numpy..."
chroot_firmware.sh "$ROOTFS_DIR" python3 -m pip install opencv-python-headless 'numpy<2'

echo ">> Installing paho-mqtt..."
chroot_firmware.sh "$ROOTFS_DIR" python3 -m pip install paho-mqtt
