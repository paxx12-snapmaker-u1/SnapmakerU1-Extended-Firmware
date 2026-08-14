#!/bin/bash

set -e

OEM_DIR="/oem/printer_data"
SOURCE_DIR="/opt/lava/printer_data"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source directory $SOURCE_DIR does not exist"
  exit 1
fi

# Create user printer data directory if it doesn't exist

if [[ ! -d "$OEM_DIR" ]]; then
  echo "Creating $OEM_DIR from $SOURCE_DIR"
  mkdir -p /oem
  cp -a "$SOURCE_DIR" "$OEM_DIR"
  chown -R lava:lava "$OEM_DIR"
  echo "Printer data initialized successfully"
else
  echo "Printer data already exists at $OEM_DIR"
fi

# Create MQTT TLS certificates if they don't exist

CERTS_DIR="$OEM_DIR/certs"
CA_CRT="$CERTS_DIR/mqtt_ca.crt"
SRV_CRT="$CERTS_DIR/mqtt_server.crt"
SRV_KEY="$CERTS_DIR/mqtt_server.key"

if [[ ! -f "$CA_CRT" || ! -f "$SRV_CRT" || ! -f "$SRV_KEY" ]]; then
  FULL_VERSION=$(cat /etc/FULLVERSION 2>/dev/null || echo "unknown_$(date +%Y%m%d)")
  BUILD_DATE=${FULL_VERSION#*_}
  BUILD_DATE=${BUILD_DATE:0:8}
  VALID=3650

  echo "TLS certificates not found. Generating self-signed certificates..."
  mkdir -p "$CERTS_DIR"
  rm -rf "$CERTS_DIR/mqtt_"*
  /opt/venv/bin/python3 /opt/lava/bin/ca_tool.py "$CERTS_DIR" $BUILD_DATE $VALID ca mqtt_ca
  /opt/venv/bin/python3 /opt/lava/bin/ca_tool.py "$CERTS_DIR" $BUILD_DATE $VALID server mqtt_server
  /opt/venv/bin/python3 /opt/lava/bin/ca_tool.py "$CERTS_DIR" $BUILD_DATE $VALID client mqtt_cli0
  chown -R lava:lava "$CERTS_DIR"
else
  echo "TLS certificates already exist in $CERTS_DIR"
fi

# Create MQTT password file if it doesn't exist

PASSWD_FILE="$OEM_DIR/mqtt/users.conf"

# Check if file does not contain cli0:
if ! grep -q "^cli0:" "$PASSWD_FILE" 2>/dev/null; then
  echo "MQTT user file not found. Generating it..."
  mkdir -p /oem/printer_data/mqtt
  mosquitto_passwd -c -b "$PASSWD_FILE" cli0 snapmaker
  chmod 0600 "$PASSWD_FILE"
  chown -R lava:lava "$PASSWD_FILE"
else
  echo "MQTT user file already exists at $PASSWD_FILE"
fi

echo
echo "Printer data initialization complete."
echo
