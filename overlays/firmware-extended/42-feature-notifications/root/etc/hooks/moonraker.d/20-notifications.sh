# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Render the Moonraker side of push notifications from extended2.cfg
# [notifications] (the single source of truth), or remove it when disabled.
# Sourced by the init script: never exit, never fail the service start.
case "$1" in
    start|restart)
        /usr/local/bin/notifications-config.py render-moonraker ||
            echo "notifications: failed to render Moonraker config" >&2
        ;;
esac
