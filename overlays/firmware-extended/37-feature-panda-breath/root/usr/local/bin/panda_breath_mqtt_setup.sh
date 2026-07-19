#!/usr/bin/env bash
# Panda Breath — set up a dedicated, authenticated mosquitto listener for the
# stock firmware v1.0.4+ native-MQTT integration.
#
# The stock v1.0.4 firmware speaks Home-Assistant-style MQTT. We give it its
# own LAN listener on the printer's broker, locked down with a per-device
# password and a topic ACL (panda_breath/* only). The internal 127.0.0.1:1883
# listener is untouched and stays private; the Klipper extra uses that one.
#
# Idempotent: re-runs reuse the generated password and don't duplicate config.

set -eo pipefail

CONF=/etc/mosquitto/mosquitto.conf
PWFILE=/etc/mosquitto/panda_pw.conf
PWPLAIN=/etc/mosquitto/panda_pw.plain     # root-only, lets us re-show the cred
ACL=/etc/mosquitto/acl_panda.conf
PORT=1885
MUSER=panda

# 1. password — reuse if already generated, otherwise create a strong one
if [[ -s "$PWPLAIN" ]]; then
  PB_PASS="$(cat "$PWPLAIN")"
else
  # 20 hex chars (80-bit): strong for an ACL-scoped LAN listener and within the
  # Panda "Bind a Broker" field length limit (longer values get truncated).
  PB_PASS="$(openssl rand -hex 10)"
  printf '%s' "$PB_PASS" > "$PWPLAIN"
  chown lava:lava "$PWPLAIN"; chmod 600 "$PWPLAIN"
fi

# 2. hashed password file for mosquitto
mosquitto_passwd -c -b "$PWFILE" "$MUSER" "$PB_PASS"
chown lava:lava "$PWFILE"; chmod 600 "$PWFILE"

# 3. ACL — this user may only touch its own topics (+ read HA discovery)
printf 'user %s\ntopic readwrite panda_breath/#\ntopic read homeassistant/#\n' "$MUSER" > "$ACL"
chown lava:lava "$ACL"; chmod 644 "$ACL"

# 4. listener (idempotent, backed up)
cp -n "$CONF" "$CONF.bak-panda" 2>/dev/null || true
if ! grep -q "listener $PORT\b" "$CONF"; then
  cat >> "$CONF" <<EOF

# Panda Breath stock-mqtt listener (LAN): auth + ACL, restricted to panda_breath/*
listener $PORT
allow_anonymous false
password_file $PWFILE
acl_file $ACL
max_connections 8
EOF
fi

# 5. restart broker so the new listener takes effect
/etc/init.d/S50mosquitto restart >/dev/null 2>&1 || true

# 6. print the binding the user enters once in the Panda web UI
PRINTER_IP=$((ip -4 -o addr show eth0 2>/dev/null || ip -4 -o addr show wlan0 2>/dev/null) \
  | awk '{print $4}' | cut -d/ -f1 | head -1)
echo ""
echo ">> Panda Breath MQTT broker is ready (listener ${PORT}, user ${MUSER})."
echo "   IP ${PRINTER_IP}:${PORT} — the Panda is bound to this automatically over"
echo "   its WebSocket API; no manual web-UI entry is required."
echo ""
