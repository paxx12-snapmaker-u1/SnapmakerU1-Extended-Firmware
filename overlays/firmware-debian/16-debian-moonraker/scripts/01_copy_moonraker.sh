#!/bin/bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
	echo "Error: This script should be run within the create_firmware.sh environment."
	exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <rootfs-dir>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../../../..")"
ROOTFS_DIR="$(realpath "$1")"
ORG_ROOTFS_DIR="${ROOTFS_DIR}.org"

set -e

mkdir_chroot() {
  for dir; do
    echo ">> Creating directory $dir in chroot..."
    mkdir -p "$ROOTFS_DIR/$dir"
  done
}

copy_chroot() {
  local target="$1"
  shift

  echo ">> Copying to $target the $@..."
  for src; do
    cp -rv -L --remove-destination "$ORG_ROOTFS_DIR/$src" "$ROOTFS_DIR/$target"
  done
}

in_chroot() {
  "$ROOT_DIR/scripts/helpers/chroot_firmware.sh" "$ROOTFS_DIR" bash -c "$@"
}

echo ">> Copying Moonraker from /home/lava/moonraker to /opt/moonraker..."
mkdir_chroot /opt/moonraker
copy_chroot /opt/moonraker/ /home/lava/moonraker/.

cat <<EOF > "$ROOTFS_DIR/opt/moonraker/scripts/moonraker-requirements.txt"
# Python dependencies for Moonraker
--find-links=python_wheels
tornado==6.2.0 ; python_version=='3.7'
tornado==6.4.1 ; python_version>='3.8'
pyserial==3.4
pyserial-asyncio==0.6
pillow
streaming-form-data==1.11.0 ; python_version=='3.7'
streaming-form-data==1.15.0 ; python_version>='3.8'
distro==1.9.0
inotify-simple==1.3.5
libnacl==2.1.0
paho-mqtt==1.6.1
zeroconf==0.131.0
preprocess-cancellation==0.2.1
jinja2==3.1.4
dbus-next==0.2.3
apprise==1.8.0
ldap3==2.9.1
python-periphery==2.4.1
importlib_metadata==6.7.0 ; python_version=='3.7'
importlib_metadata==8.2.0 ; python_version>='3.8'
httpx
EOF

echo ">> Installing moonraker requirements..."
in_chroot '/opt/venv/bin/pip3 install --upgrade pip'
in_chroot '/opt/venv/bin/pip3 install -r /opt/moonraker/scripts/moonraker-requirements.txt'

echo ">> Setting ownership to lava:lava..."
in_chroot 'chown -R lava:lava /opt/moonraker'

echo ">> Moonraker installation complete"
