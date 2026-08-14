#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

set -e

echo ">> Enabling mosquitto service..."
systemctl enable mosquitto

echo ">> Mosquitto installation complete"
