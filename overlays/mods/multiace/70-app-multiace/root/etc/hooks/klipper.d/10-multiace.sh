#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/decay71/multiACE
# SPDX-FileCopyrightText: Copyright (c) 2026 @decay71 and contributors

. /usr/local/share/multiace/common.sh

case "${1:-}" in
    start|restart)
        if multiace_enabled; then
            if ! multiace_activate; then
                echo "multiACE is enabled but could not be activated; starting stock Klipper paths." >&2
            fi
        else
            multiace_deactivate
        fi
        ;;
    stop)
        multiace_unmount_modules
        ;;
esac
