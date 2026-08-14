#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

set -e

echo ">> Checking required application files..."
file /usr/local/bin/detect-rknn-yolo11.py
file /usr/local/bin/detect-http.py
file /opt/lava/unisrv/camera_service/model/bed_check.fp.rknn
file /opt/lava/unisrv/camera_service/model/print_check.fp.rknn
