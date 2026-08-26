# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Render the Moonraker `[spoolman]` and `[spoollink]` config from the Spoolman
# configuration.
#
# The `[spoolman]` section in `extended2.cfg` is the source of truth. When a host
# is configured, generate the Moonraker `[spoolman]` block (built-in usage
# tracking) and the `[spoollink]` block (the SpoolLink component that bridges
# Spoolman to the AFC/RFID stack) into the extended config directory pointing at
# it; otherwise remove the generated config. Optional external channels are
# copied into the SpoolLink block.
if [ "$1" = start ]; then
    EXTENDED_CFG="/oem/printer_data/config/extended/extended2.cfg"
    MOONRAKER_CFG="/oem/printer_data/config/extended/moonraker/spoollink.cfg"
    CACHE_DIR="/oem/printer_data/config/extended/spoollink"

    SPOOLMAN_HOST=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" spoolman host "" 2>/dev/null)
    EXTERNAL_CHANNELS=$(
        /usr/local/bin/extended-config.py get \
            "$EXTENDED_CFG" spoolman external_channels "" 2>/dev/null
    )
    if [ -n "$SPOOLMAN_HOST" ]; then
        mkdir -p "$(dirname "$MOONRAKER_CFG")" "$CACHE_DIR"
        chown lava:lava "$CACHE_DIR"
        cat > "$MOONRAKER_CFG" <<EOF
# Spoolman Integration (generated from extended2.cfg [spoolman] host).
# See: https://moonraker.readthedocs.io/en/latest/configuration/#spoolman
[spoolman]
server: $SPOOLMAN_HOST
sync_rate: 5

# SpoolLink component: bridges Spoolman to the Snapmaker AFC/RFID stack.
[spoollink]
server: $SPOOLMAN_HOST
cache_dir: $CACHE_DIR
external_channels: $EXTERNAL_CHANNELS
EOF
        chown lava:lava "$MOONRAKER_CFG"
    else
        rm -f "$MOONRAKER_CFG"
    fi
fi
