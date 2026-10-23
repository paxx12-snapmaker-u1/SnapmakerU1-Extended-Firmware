#!/bin/sh
# Generate a persistent self-signed certificate for the phone-to-printer LAN.
set -eu
SERVICE_DIR=${1:-/home/lava/printer_data/config/extended/ext-mic}
CERT_FILE=$SERVICE_DIR/server.crt
KEY_FILE=$SERVICE_DIR/server.key
HOSTNAME_VALUE=${2:-$(hostname 2>/dev/null || echo lava)}
IP_VALUE=${3:-}
mkdir -p "$SERVICE_DIR"
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }
if [ -e "$CERT_FILE" ] || [ -e "$KEY_FILE" ]; then
    if [ -r "$CERT_FILE" ] && [ -r "$KEY_FILE" ]; then
        echo "certificate already exists"
        exit 0
    fi
    echo "removing incomplete certificate pair" >&2
    rm -f "$CERT_FILE" "$KEY_FILE"
fi
umask 077
SAN="DNS:$HOSTNAME_VALUE,DNS:lava,IP:127.0.0.1"
[ -n "$IP_VALUE" ] && SAN="$SAN,IP:$IP_VALUE"
TMP_DIR="$SERVICE_DIR/.ext-mic-cert.$$"
mkdir "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM
openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
  -keyout "$TMP_DIR/server.key" -out "$TMP_DIR/server.crt" \
  -subj "/CN=$HOSTNAME_VALUE" \
  -addext "subjectAltName=$SAN"
chmod 600 "$TMP_DIR/server.key"
chmod 644 "$TMP_DIR/server.crt"
mv "$TMP_DIR/server.key" "$KEY_FILE"
mv "$TMP_DIR/server.crt" "$CERT_FILE"
echo "created $CERT_FILE and $KEY_FILE"
echo "Install the certificate (or its issuing CA) on the phone before opening HTTPS."
