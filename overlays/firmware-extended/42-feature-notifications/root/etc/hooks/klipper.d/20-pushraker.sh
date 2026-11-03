# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Keep the Klipper side of pushraker in place at every start (and remove
# what an older firmware generated). Sourced by the init script: never
# exit, never fail the service start.
case "$1" in
    start|restart)
        /usr/local/bin/pushraker-pkg wire || true
        ;;
esac
