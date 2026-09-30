# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Render the Moonraker `[spoolman]` and `[spoollink]` config from the Spoolman
# configuration.
#
# `[spoolman] host` in `extended2.cfg` is the single source of truth. When a host
# is configured, generate the Moonraker `[spoolman]` block (built-in usage
# tracking) and the `[spoollink]` block (the SpoolLink component that bridges
# Spoolman to the AFC/RFID stack) into the extended config directory pointing at
# it; otherwise remove the generated config.
if [ "$1" = start ]; then
    EXTENDED_CFG="/oem/printer_data/config/extended/extended2.cfg"
    MOONRAKER_CFG="/oem/printer_data/config/extended/moonraker/spoollink.cfg"
    CACHE_DIR="/oem/printer_data/config/extended/spoollink"

    SPOOLMAN_HOST=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" spoolman host "" 2>/dev/null)
    API_KEY=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" spoolman api_key "" 2>/dev/null)
    FORCE_GENERIC_VENDOR=$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" spoolman force_generic_vendor false 2>/dev/null)

    if [ -n "$SPOOLMAN_HOST" ]; then
        mkdir -p "$(dirname "$MOONRAKER_CFG")" "$CACHE_DIR"
        chown lava:lava "$CACHE_DIR"
        API_KEY_LINE=""
        SPOOLMAN_BLOCK=""
        if [ -n "$API_KEY" ]; then
            API_KEY_LINE="api_key: $API_KEY"
        else
            SPOOLMAN_BLOCK="[spoolman]
server: $SPOOLMAN_HOST
sync_rate: 5
"
        fi
        cat > "$MOONRAKER_CFG" <<EOF
$SPOOLMAN_BLOCK
# SpoolLink component: bridges FilaMan / Spoolman to the Snapmaker AFC/RFID stack.
[spoollink]
server: $SPOOLMAN_HOST
$API_KEY_LINE
cache_dir: $CACHE_DIR
force_generic_vendor: $FORCE_GENERIC_VENDOR
EOF
        chown lava:lava "$MOONRAKER_CFG"
    else
        rm -f "$MOONRAKER_CFG"
    fi
fi
