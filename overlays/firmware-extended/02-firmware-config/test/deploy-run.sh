#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <host> [optional-args]"
  exit 1
fi

DIR="$(dirname "$0")"
cd "$DIR/.."

SSH_HOST="$1"
shift

set -xeo pipefail
scp -r "root/." "$SSH_HOST":/
ssh -t "$SSH_HOST" /etc/init.d/S99firmware-config stop
ssh -t "$SSH_HOST" "/usr/local/bin/firmware-config.py" --bind "0.0.0.0" --port "9091" \
  --html-dir "/usr/local/share/firmware-config/html" \
  --functions-dir "/usr/local/share/firmware-config/functions" \
  "$@"
