#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

set -e

echo ">> Installing Pillow for JPEG support in moonraker venv..."
/opt/venv/bin/pip3 install Pillow

