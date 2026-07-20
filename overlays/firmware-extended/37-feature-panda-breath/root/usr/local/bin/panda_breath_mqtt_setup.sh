#!/usr/bin/env bash
# Panda Breath — set up a dedicated, authenticated mosquitto listener for the
# stock firmware v1.0.4+ native-MQTT integration.
#
# The stock v1.0.4 firmware speaks Home-Assistant-style MQTT. We give it its
# own LAN listener on the printer's broker, locked down with a per-device
# password and a topic ACL (panda_breath/* only). The internal 127.0.0.1:1883
# listener is untouched and stays private; the Klipper extra uses that one.
#
# Persistence: /etc/mosquitto is on the squashfs overlay and resets on boot, so
# the password/ACL live under printer_data (persisted) and the listener block
# is re-injected on each boot by /etc/init.d/S49panda_breath_mqtt. Everything
# here writes to the persisted location.
#
# Idempotent: re-runs reuse the generated password and don't duplicate config.
#   panda_breath_mqtt_setup.sh          set up + start the listener
#   panda_breath_mqtt_setup.sh remove   tear the listener + credentials down

set -eo pipefail

PERSIST=/home/lava/printer_data/mqtt
PWFILE="$PERSIST/panda_pw.conf"
PWPLAIN="$PERSIST/panda_pw.plain"   # lets us re-show / re-bind the credential
ACL="$PERSIST/acl_panda.conf"
CONF=/etc/mosquitto/mosquitto.conf
HOOK=/etc/init.d/S49panda_breath_mqtt
PORT=1885
MUSER=panda

# ── teardown ────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "remove" ]]; then
  rm -f "$PWFILE" "$PWPLAIN" "$ACL"
  sed -i '/# Panda Breath stock-mqtt listener/,/max_connections 8/d' "$CONF" 2>/dev/null || true
  /etc/init.d/S50mosquitto restart >/dev/null 2>&1 || true
  echo ">> Panda Breath MQTT broker listener removed."
  exit 0
fi

mkdir -p "$PERSIST"
chown lava:lava "$PERSIST" 2>/dev/null || true

# 1. password — reuse the persisted one if present, else generate a strong,
#    transcription-safe (20 hex char / 80-bit) credential.
if [[ -s "$PWPLAIN" ]]; then
  PB_PASS="$(cat "$PWPLAIN")"
else
  PB_PASS="$(openssl rand -hex 10)"
  printf '%s' "$PB_PASS" > "$PWPLAIN"
  chown lava:lava "$PWPLAIN"; chmod 600 "$PWPLAIN"
fi

# 2. hashed password file for mosquitto (persisted)
mosquitto_passwd -c -b "$PWFILE" "$MUSER" "$PB_PASS"
chown lava:lava "$PWFILE"; chmod 600 "$PWFILE"

# 3. ACL — this user owns its own topics and may publish Home Assistant
#    autodiscovery configs (the stock firmware advertises the chamber as a full
#    HA device on homeassistant/#, so any HA instance on this broker discovers
#    it automatically alongside Klipper's own control).
printf 'user %s\ntopic readwrite panda_breath/#\ntopic readwrite homeassistant/#\n' "$MUSER" > "$ACL"
chown lava:lava "$ACL"; chmod 600 "$ACL"

# 4. inject the listener now (same path the boot hook uses) and restart broker
"$HOOK" start
/etc/init.d/S50mosquitto restart >/dev/null 2>&1 || true

# 5. report the binding (the firmware-config step binds the Panda automatically)
PRINTER_IP=$((ip -4 -o addr show eth0 2>/dev/null || ip -4 -o addr show wlan0 2>/dev/null) \
  | awk '{print $4}' | cut -d/ -f1 | head -1)
echo ""
echo ">> Panda Breath MQTT broker is ready (persisted; survives reboots)."
echo ""
echo "   Broker details (save these). If the Panda does not connect on its own,"
echo "   open the Panda web UI -> Bind a Broker and enter them manually:"
echo "     Broker IP: ${PRINTER_IP}"
echo "     Port:      ${PORT}"
echo "     Username:  ${MUSER}"
echo "     Password:  ${PB_PASS}"
echo ""
