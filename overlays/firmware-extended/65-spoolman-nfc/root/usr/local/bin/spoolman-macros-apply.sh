#!/bin/bash

CFG="/oem/printer_data/config/extended/extended2.cfg"
MACRO_DIR="/home/lava/printer_data/config/extended/klipper"
MACRO_FILE="$MACRO_DIR/spoolman_multi_tool.cfg"

SYNC_ENABLED=$(/usr/local/bin/extended-config.py get "$CFG" components spoolman_sync disabled)
MACRO_MODE=$(/usr/local/bin/extended-config.py get "$CFG" components spoolman_macros auto)

SHOULD_ENABLE=false
if [ "$MACRO_MODE" = "enabled" ]; then
    SHOULD_ENABLE=true
elif [ "$MACRO_MODE" = "auto" ] && [ "$SYNC_ENABLED" = "enabled" ]; then
    SHOULD_ENABLE=true
fi

# Ensure macro directory exists
mkdir -p "$MACRO_DIR"

KLIPPER_NEEDS_RESTART=false

if [ "$SHOULD_ENABLE" = "true" ]; then
    if [ -f "$MACRO_FILE.disabled" ]; then
        mv "$MACRO_FILE.disabled" "$MACRO_FILE"
        KLIPPER_NEEDS_RESTART=true
    fi
else
    if [ -f "$MACRO_FILE" ]; then
        mv "$MACRO_FILE" "$MACRO_FILE.disabled"
        KLIPPER_NEEDS_RESTART=true
    fi
fi

if [ "$KLIPPER_NEEDS_RESTART" = "true" ]; then
    /etc/init.d/S60klipper restart
fi

# Always restart moonraker to pick up sync changes
/etc/init.d/S61moonraker restart
