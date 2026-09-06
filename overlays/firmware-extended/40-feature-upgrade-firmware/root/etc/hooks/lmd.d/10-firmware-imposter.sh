# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [ "$1" = start ]; then
    EXTENDED_CFG="/home/lava/printer_data/config/extended/extended2.cfg"
    FIRMWARE_IMPOSTER=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" components upgrade none)
    export LD_PRELOAD="/usr/local/lib/libfirmware-imposter.so${LD_PRELOAD:+:$LD_PRELOAD}"
    export FW_UPDATE_URL="/api/device/firmware/latest"

    case "$FIRMWARE_IMPOSTER" in
        stable|testing|develop)
            echo "Starting lmd with firmware-upgrade imposter (channel=$FIRMWARE_IMPOSTER)!"
            VERSION=$(cat /etc/VERSION 2>/dev/null)
            BUILD_VERSION=$(cat /etc/BUILD_VERSION 2>/dev/null)
            BUILD_PROFILE=$(cat /etc/BUILD_PROFILE 2>/dev/null)
            export FW_UPDATE_REWRITE_URL="https://snapmakeru1-extended-firmware.pages.dev/api/device/firmware/latest?channel=$FIRMWARE_IMPOSTER&version=$VERSION&build_version=$BUILD_VERSION&build_profile=$BUILD_PROFILE"
            ;;
        *)
            echo "Starting lmd with firmware-upgrade check disabled!"
            export FW_UPDATE_BLOCK=1
            ;;
    esac
fi
