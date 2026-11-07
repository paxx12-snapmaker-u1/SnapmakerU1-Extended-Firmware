#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

CRONTAB="$ROOTFS_DIR/etc/crontabs/root"
CROND_INIT="$ROOTFS_DIR/etc/init.d/S01crond"

# Stock firmware ships crond with /etc/crontabs from 2.1.0 on; older bases
# only have the busybox applet, so provide the same init script there.
if [[ ! -f "$CRONTAB" ]]; then
  # A link to busybox: resolves on the device, not inside the build tree
  if [[ ! -L "$ROOTFS_DIR/sbin/crond" && ! -x "$ROOTFS_DIR/sbin/crond" ]]; then
    echo "ERROR: no crond in rootfs, certificates would not be renewed"
    exit 1
  fi
  echo ">> Creating root crontab..."
  install -d "$(dirname "$CRONTAB")"
  install -m 600 /dev/null "$CRONTAB"
fi

if [[ ! -f "$CROND_INIT" ]]; then
  echo ">> Adding crond init script..."
  cat > "$CROND_INIT" <<'EOF'
#!/bin/sh

DAEMON="crond"
PIDFILE="/var/run/$DAEMON.pid"
# /var/spool (the crond default) is a symlink to tmpfs and is wiped on
# reboot, so keep the crontabs on the read-only rootfs instead. crond
# only reads them.
CRONTABS_DIR="/etc/crontabs"

case "$1" in
	start)
		printf 'Starting %s: ' "$DAEMON"
		start-stop-daemon -S -q -m -b -p "$PIDFILE" -x "/sbin/$DAEMON" \
			-- -f -l 8 -c "$CRONTABS_DIR"
		[ $? = 0 ] && echo "OK" || echo "FAIL"
		;;
	stop)
		printf 'Stopping %s: ' "$DAEMON"
		start-stop-daemon -K -q -p "$PIDFILE"
		[ $? = 0 ] && { rm -f "$PIDFILE"; echo "OK"; } || echo "FAIL"
		;;
	restart|reload)
		"$0" stop
		sleep 1
		"$0" start
		;;
	*)
		echo "Usage: $0 {start|stop|restart|reload}"
		exit 1
		;;
esac
EOF
  chmod 755 "$CROND_INIT"
fi

echo ">> Adding certificate renewal to root crontab..."
# Make sure the entry starts on its own line
[[ -s "$CRONTAB" && -n "$(tail -c1 "$CRONTAB")" ]] && echo >> "$CRONTAB"
# Twice a day; acme.sh only renews when the certificate is due.
echo "17 3,15 * * * /usr/local/sbin/https-setup cron" >> "$CRONTAB"
