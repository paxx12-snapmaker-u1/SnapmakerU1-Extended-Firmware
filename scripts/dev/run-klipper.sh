#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025 @paxx12

CONFIG_FILE="${1:-/home/lava/printer_data/config/printer.cfg}"

/etc/init.d/S60klipper stop

/usr/bin/lava_io set MAIN_MCU_POWER=1 HEAD_MCU_POWER=1

/home/lava/firmware_MCU/klippy_mcu &
PIDS="$!"

/usr/bin/python3 /home/lava/klipper/klippy/klippy.py "${CONFIG_FILE}" -I /home/lava/printer_data/comms/klippy.serial -v -a /home/lava/printer_data/comms/klippy.sock -u lava &
PIDS="$!"

cleanup() {
    kill $PIDS 2>/dev/null
    exit 1
}
trap cleanup INT TERM

wait -n
echo "One of the processes has exited, cleaning up..."
