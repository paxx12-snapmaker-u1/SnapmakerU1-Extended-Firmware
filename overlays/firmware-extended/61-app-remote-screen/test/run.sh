#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

ROOT_DIR="$(dirname "$(realpath "$0")")/.."

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <u1.home> [fb-http-server-args...]"
    exit 1
fi

HOST="$1"
shift

scp -r "$ROOT_DIR/root/." "root@$HOST:/"
ssh -t "root@$HOST" /etc/init.d/S99fb-http stop
ssh -t "root@$HOST" /usr/local/bin/fb-http-server.py --html "/usr/local/share/fb-http-server/index.html" "$@"
