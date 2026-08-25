# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    echo "Starting lmd with firmware-upgrade check disabled!"
    export LD_PRELOAD="/usr/local/lib/libfirmware-imposter.so${LD_PRELOAD:+:$LD_PRELOAD}"
    export FW_UPDATE_URL="/api/device/firmware/latest"
    export FW_UPDATE_BLOCK=1
fi
