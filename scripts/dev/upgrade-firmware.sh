#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 @paxx12, @LixNix, @liberodark

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <user@ip> <profile>"
  exit 1
fi

SSH_HOST="$1"
PROFILE="$2"
shift 2

PASSWORD="${PASSWORD:-snapmaker}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

set -xe

make build OUTPUT_FILE=firmware/firmware_$PROFILE.bin PROFILE="$PROFILE" OVERWRITE=1
sshpass -p "$PASSWORD" scp $SSH_OPTS "tmp/firmware/update.img" "$SSH_HOST:/userdata/"
sshpass -p "$PASSWORD" ssh $SSH_OPTS "$SSH_HOST" /home/lava/bin/systemUpgrade.sh upgrade soc /userdata/update.img
