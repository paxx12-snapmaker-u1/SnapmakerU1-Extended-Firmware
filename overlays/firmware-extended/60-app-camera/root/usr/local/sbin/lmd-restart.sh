#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Restarts lmd so that the lmd hooks apply again. lmd takes about 30 s to shut
# down, longer than `S90lmd restart` waits, and starting it while it is still
# running does nothing.

/etc/init.d/S90lmd stop

i=0
while pidof lmd >/dev/null; do
    if [ "$i" -ge 90 ]; then
        echo "lmd did not stop"
        exit 1
    fi
    sleep 1
    i=$((i + 1))
done

/etc/init.d/S90lmd start
