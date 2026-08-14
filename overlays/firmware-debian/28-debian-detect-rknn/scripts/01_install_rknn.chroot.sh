#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

set -e

echo ">> Installing RKNN Toolkit Lite2 in venv..."
/opt/venv/bin/pip3 install https://github.com/airockchip/rknn-toolkit2/raw/refs/heads/master/rknn-toolkit-lite2/packages/rknn_toolkit_lite2-2.3.2-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
/opt/venv/bin/pip3 install opencv-python-headless 'numpy<2.0.0'
