# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# pushraker lives in /oem/apps (kept across firmware upgrades); the shim in
# Moonraker's components folder is on the root filesystem and is not. Put
# the wiring back at every start when pushraker is enabled.
# Sourced by the init script: never exit, never fail the service start.
case "$1" in
    start|restart)
        /usr/local/bin/pushraker-pkg wire || true
        ;;
esac
