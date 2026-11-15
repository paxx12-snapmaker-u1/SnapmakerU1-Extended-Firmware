# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    # Ensure the DRM connector is in connected state before disabling polling.
    if [ "$(cat /sys/class/drm/card0-DPI-1/status 2>/dev/null)" = "disconnected" ]; then
        echo on > /sys/class/drm/card0-DPI-1/status 2>/dev/null || true
    fi

    # Disable kernel DRM hotplug polling on the fixed internal DPI panel so
    # output_poll_execute never races drm_fb_helper_set_par / vop2_crtc_atomic_disable.
    if [ -w /sys/module/drm_kms_helper/parameters/poll ]; then
        echo N > /sys/module/drm_kms_helper/parameters/poll 2>/dev/null || true
    fi

    # Ensure the LCD backlight starts unblanked when lmd launches.
    if [ -w /sys/class/backlight/backlight/bl_power ]; then
        echo 0 > /sys/class/backlight/backlight/bl_power 2>/dev/null || true
    fi

    if [ -z "$HELIX_SUPERVISED" ]; then
        export LD_PRELOAD="/usr/local/lib/libgui-screen-fix.so${LD_PRELOAD:+:$LD_PRELOAD}"
    fi
fi
