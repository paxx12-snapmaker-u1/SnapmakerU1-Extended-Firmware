#!/usr/bin/env bash
# Cleanly leave whatever Panda Breath mode is currently active before another
# mode is applied (or before disabling). This releases the device from its
# current binding and removes the Klipper config so the next mode starts from a
# clean slate.
#
# Why this matters: MQTT mode binds the Panda to the broker (native HA mode).
# While broker-bound, the device's WebSocket /ws settings frame does not report
# a usable fw_version, so a later `bind-klipper`/`bind-broker` firmware-version
# check fails. Switching straight from MQTT to auto/manual therefore fails the
# version check — the device must be unbound from the broker (returned to
# WebSocket control) first. Auto/manual likewise leave a stale binding if not
# torn down. Running this first makes every transition behave like the
# known-good "disable, then enable" path.
#
# Idempotent and safe: exits 0 when nothing is configured, and every device
# round-trip is best-effort (a lost/unreachable Panda never blocks teardown).

set -eo pipefail

CFG=/oem/printer_data/config/extended/klipper/panda_breath.cfg
[ -f "$CFG" ] || exit 0

if grep -qE '^[[:space:]]*firmware:[[:space:]]*stock-mqtt' "$CFG" 2>/dev/null; then
  # MQTT mode: disconnect the Panda from the broker (like the device's own
  # "Unbind" button). The Panda IP is stored as a comment in the config.
  PANDA_BREATH_IP=$(sed -nE 's/^#[[:space:]]*panda_device_ip:[[:space:]]*([0-9.]+).*/\1/p' "$CFG" | head -1)
  if [ -n "${PANDA_BREATH_IP}" ]; then
    echo ""
    echo ">> Unbinding Panda Breath broker at ${PANDA_BREATH_IP}..."
    /usr/local/bin/panda_breath_cli.py --host "${PANDA_BREATH_IP}" unbind-broker || true
  fi
  echo ""
  echo ">> Removing the persisted MQTT broker listener..."
  /usr/local/bin/panda_breath_mqtt_setup.sh remove || true
elif PANDA_BREATH_IP=$(/usr/local/bin/extended-config.py get "$CFG" panda_breath host 2>/dev/null); then
  echo ""
  echo ">> Unbinding Panda Breath at ${PANDA_BREATH_IP}..."
  /usr/local/bin/panda_breath_cli.py --host "${PANDA_BREATH_IP}" unbind || true
fi

echo ""
echo ">> Removing Panda Breath configuration..."
rm -f "$CFG"
