# Reconcile the Klipper `[spoollink]` include with the Spoolman configuration.
#
# `[spoolman] host` in `extended2.cfg` is the single source of truth for whether
# the integration is enabled. Symlink the Klipper snippet into the extended
# config directory when a host is configured, and remove the symlink otherwise,
# so enabling/disabling Spoolman from `firmware-config` does not have to manage
# the Klipper side directly.
if [ "$1" = start ]; then
    EXTENDED_CFG="/oem/printer_data/config/extended/extended2.cfg"
    KLIPPER_CFG_SRC="/usr/local/share/spoollink/klipper.cfg"
    KLIPPER_CFG_LINK="/oem/printer_data/config/extended/klipper/spoollink.cfg"

    if [ -n "$(/usr/local/bin/extended-config.py get "$EXTENDED_CFG" spoolman host "" 2>/dev/null)" ]; then
        if [ ! -L "$KLIPPER_CFG_LINK" ]; then
            ln -sf "$KLIPPER_CFG_SRC" "$KLIPPER_CFG_LINK"
            chown -h lava:lava "$KLIPPER_CFG_LINK"
        fi
    else
        rm -f "$KLIPPER_CFG_LINK"
    fi
fi
