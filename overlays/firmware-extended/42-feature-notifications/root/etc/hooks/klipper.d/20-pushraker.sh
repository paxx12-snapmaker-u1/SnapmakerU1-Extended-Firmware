# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Before Klipper (and so Moonraker) starts: set aside the include files an
# earlier setup wrote (the old Push Notifications form's notifications.cfg,
# and pushraker.cfg from test builds that installed it on demand), which
# would define pushraker's macros and section a second time. The files
# become .old; pushraker's own includes are the 05_pushraker.cfg links.
if [ "$1" = start ]; then
    for f in /oem/printer_data/config/extended/klipper/notifications.cfg \
             /oem/printer_data/config/extended/klipper/pushraker.cfg \
             /oem/printer_data/config/extended/moonraker/notifications.cfg \
             /oem/printer_data/config/extended/moonraker/pushraker.cfg; do
        if [ -f "$f" ] && [ ! -L "$f" ]; then
            mv -f "$f" "$f.old"
        fi
    done
fi
