#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <user@ip> <profile> [command]"
  exit 1
fi

SSH_HOST="$1"
PROFILE="$2"
shift 2

PASSWORD="${PASSWORD:-snapmaker}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

overlays_list() {
  make -s overlays PROFILE="$1"
}

tar_overlays_root() {
  for overlay; do
    [[ -d "$overlay/root" ]] && echo "-C $(realpath "$overlay/root") ."
  done
}

# Use `tar` instead of `scp` as being significantly faster when there are many small files.

echo ">> Uploading rootfs overlays..."
tar -cf - $(tar_overlays_root $(overlays_list "$PROFILE")) |
  sshpass -p "$PASSWORD" ssh $SSH_OPTS "$SSH_HOST" tar -C / -xf -

if [[ $# -gt 0 ]]; then
  echo ">> Running command: $*"
  sshpass -p "$PASSWORD" ssh $SSH_OPTS "$SSH_HOST" "$@"
fi

echo ">> Done."
