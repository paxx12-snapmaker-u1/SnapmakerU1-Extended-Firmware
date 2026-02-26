#!/usr/bin/env bash

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <printer-ip-or-host>"
  echo "Example: $0 192.168.1.100"
  exit 1
fi

SSH_HOST="$1"
DIR="$(dirname "$0")"
ROOT_DIR="$(realpath "$DIR/../../../..")"

set -xeo pipefail

# sed '/^'"$SSH_HOST"' /d' -i ~/.ssh/known_hosts
ssh-copy-id -o "StrictHostKeyChecking=no" "root@$SSH_HOST"
ssh-add -l >/dev/null 2>&1 || ssh-add

echo "Deploying modified files to $SSH_HOST..."

# Copy modified Klipper file
scp "$ROOT_DIR/tmp/firmware/rootfs/home/lava/klipper/klippy/extras/filament_protocol.py" "root@$SSH_HOST:/home/lava/klipper/klippy/extras/"
scp "$ROOT_DIR/tmp/firmware/rootfs/home/lava/klipper/klippy/extras/filament_protocol_ndef.py" "root@$SSH_HOST:/home/lava/klipper/klippy/extras/"
scp "$ROOT_DIR/tmp/firmware/rootfs/home/lava/klipper/klippy/extras/filament_detect.py" "root@$SSH_HOST:/home/lava/klipper/klippy/extras/"

# Copy modified Moonraker component
scp "$ROOT_DIR/tmp/firmware/rootfs/home/lava/moonraker/moonraker/components/spoolman.py" "root@$SSH_HOST:/home/lava/moonraker/moonraker/components/"

# Copy Spoolman multi-tool macros
echo "Deploying Spoolman multi-tool macros..."
ssh "root@$SSH_HOST" "mkdir -p /usr/local/share/firmware-config/extended/klipper/ /usr/local/share/firmware-config/functions/ /usr/local/bin/"
scp "$ROOT_DIR/overlays/firmware-extended/65-spoolman-nfc/root/usr/local/share/firmware-config/extended/klipper/spoolman_multi_tool.cfg.disabled" "root@$SSH_HOST:/usr/local/share/firmware-config/extended/klipper/"
scp "$ROOT_DIR/overlays/firmware-extended/02-firmware-config/root/usr/local/share/firmware-config/functions/05_settings.yaml" "root@$SSH_HOST:/usr/local/share/firmware-config/functions/"
scp "$ROOT_DIR/overlays/firmware-extended/65-spoolman-nfc/root/usr/local/share/firmware-config/functions/20_settings_spoolman_sync.yaml" "root@$SSH_HOST:/usr/local/share/firmware-config/functions/"
scp "$ROOT_DIR/overlays/firmware-extended/65-spoolman-nfc/root/usr/local/bin/spoolman-macros-apply.sh" "root@$SSH_HOST:/usr/local/bin/"
ssh "root@$SSH_HOST" "chmod +x /usr/local/bin/spoolman-macros-apply.sh"

# Copy modified OpenRFID files
echo "Deploying OpenRFID modified files..."
scp "$ROOT_DIR/tmp/firmware/rootfs/usr/local/share/openrfid/filament/generic.py" "root@$SSH_HOST:/usr/local/share/openrfid/filament/"
scp "$ROOT_DIR/tmp/firmware/rootfs/usr/local/share/openrfid/tag/openspool/processor.py" "root@$SSH_HOST:/usr/local/share/openrfid/tag/openspool/"
scp "$ROOT_DIR/tmp/firmware/rootfs/usr/local/share/openrfid/extended/openrfid_u1_vendor.cfg" "root@$SSH_HOST:/usr/local/share/openrfid/extended/"
scp "$ROOT_DIR/tmp/firmware/rootfs/usr/local/share/openrfid/extended/openrfid_u1_generic.cfg" "root@$SSH_HOST:/usr/local/share/openrfid/extended/"

# Restart Klipper, Moonraker, and OpenRFID services on the printer
echo "Restarting services..."
ssh -t "root@$SSH_HOST" "/etc/init.d/S49extended-config restart && /etc/init.d/S60klipper restart && /etc/init.d/S61moonraker restart && /etc/init.d/S99openrfid restart && /etc/init.d/S99firmware-config restart"

echo "Deployed successfully!"
