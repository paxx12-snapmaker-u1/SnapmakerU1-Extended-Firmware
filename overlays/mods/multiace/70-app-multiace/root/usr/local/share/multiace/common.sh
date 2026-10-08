#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/decay71/multiACE
# SPDX-FileCopyrightText: Copyright (c) 2026 @decay71 and contributors

# Shared PAXX activation helpers. This file is sourced by the Klipper hook,
# the web init script, and the Firmware Config control command.

MULTIACE_PRINTER_DATA="${MULTIACE_PRINTER_DATA:-/home/lava/printer_data}"
MULTIACE_CONFIG_DIR="${MULTIACE_CONFIG_DIR:-$MULTIACE_PRINTER_DATA/config}"
MULTIACE_EXTENDED_DIR="$MULTIACE_CONFIG_DIR/extended"
MULTIACE_EXTENDED_CFG="$MULTIACE_EXTENDED_DIR/extended2.cfg"
MULTIACE_INSTALL_DIR="/oem/apps/multiace"
MULTIACE_APP_DIR="${MULTIACE_APP_DIR:-$MULTIACE_INSTALL_DIR/latest}"
MULTIACE_APP_ROOT="$MULTIACE_APP_DIR"
MULTIACE_STATE_DIR="$MULTIACE_EXTENDED_DIR/multiace"
MULTIACE_MANAGED_MARKER="${MULTIACE_MANAGED_MARKER:-$MULTIACE_STATE_DIR/.multiace-managed}"
MULTIACE_CONFIG_FILE="$MULTIACE_EXTENDED_DIR/ace.cfg"
MULTIACE_CONFIG_LINK="$MULTIACE_EXTENDED_DIR/klipper/multiace.cfg"
MULTIACE_WEB_VENDOR_DIR="$MULTIACE_APP_ROOT/web/backend/vendor"

multiace_enabled() {
    [ "$(/usr/local/bin/extended-config.py get \
        "$MULTIACE_EXTENDED_CFG" components ace none 2>/dev/null)" = "multiace" ]
}

multiace_export_environment() {
    export MULTIACE_MANAGED=1
    export MULTIACE_MANAGED_MARKER
    export MULTIACE_APP_DIR
    export MULTIACE_CONFIG_DIR
    export MULTIACE_PRINTER_DATA
}

multiace_sanitize_provider_config() {
    if [ ! -f "$MULTIACE_CONFIG_FILE" ]; then
        return 0
    fi
    if [ -L "$MULTIACE_CONFIG_FILE" ]; then
        echo "multiACE config conflict: $MULTIACE_CONFIG_FILE is a symlink" >&2
        return 1
    fi

    # PAXX owns the provider version and update lifecycle. Remove only the
    # provider's two online-update wrapper macros; leave all user settings and
    # unrelated macros byte-for-byte unchanged. The ace.py runtime guard still
    # blocks direct ACE_UPDATE_* commands as a second safety boundary.
    temporary="${MULTIACE_CONFIG_FILE}.paxx.$$"
    if ! awk '
        function is_update_header(line) {
            return line ~ /^[[:space:]]*\[gcode_macro ACEH__Update_Check\][[:space:]]*$/ ||
                   line ~ /^[[:space:]]*\[gcode_macro ACEH__Update_Apply\][[:space:]]*$/
        }
        {
            if (is_update_header($0)) {
                skipping = 1
                next
            }
            if (skipping) {
                if ($0 ~ /^[[:space:]]*\[/) {
                    skipping = 0
                } else {
                    next
                }
            }
            print
        }
    ' "$MULTIACE_CONFIG_FILE" > "$temporary"; then
        rm -f "$temporary"
        echo "multiACE could not sanitize its persistent configuration" >&2
        return 1
    fi
    if ! mv -f "$temporary" "$MULTIACE_CONFIG_FILE"; then
        rm -f "$temporary"
        echo "multiACE could not update its persistent configuration" >&2
        return 1
    fi
}

multiace_ensure_save_variables() {
    if grep -Eq '^[[:space:]]*\[save_variables\][[:space:]]*$' \
        "$MULTIACE_CONFIG_FILE"; then
        return 0
    fi

    # The managed provider archive omits host-specific save-variable values.
    # PAXX supplies its persistent path when seeding the user config.
    temporary="${MULTIACE_CONFIG_FILE}.paxx.$$"
    if ! {
        printf '[save_variables]\nfilename: %s\n\n' \
            "$MULTIACE_STATE_DIR/ace_vars.cfg"
        cat "$MULTIACE_CONFIG_FILE"
    } > "$temporary"; then
        rm -f "$temporary"
        echo "multiACE could not add its save_variables path" >&2
        return 1
    fi
    chmod 644 "$temporary" 2>/dev/null || true
    if ! mv -f "$temporary" "$MULTIACE_CONFIG_FILE"; then
        rm -f "$temporary"
        echo "multiACE could not update its persistent configuration" >&2
        return 1
    fi
}

multiace_seed_config() {
    if [ ! -d "$MULTIACE_APP_ROOT" ]; then
        echo "multiACE package is not installed at $MULTIACE_APP_ROOT" >&2
        return 1
    fi
    if [ ! -f "$MULTIACE_APP_ROOT/config/extended/ace.cfg" ]; then
        echo "multiACE package is missing its ace.cfg template" >&2
        return 1
    fi

    mkdir -p "$MULTIACE_STATE_DIR" \
        "$(dirname "$MULTIACE_CONFIG_FILE")" \
        "$(dirname "$MULTIACE_CONFIG_LINK")"

    # ace.cfg is persistent user configuration. Seed it once; package updates
    # must never replace values the user has already configured.
    if [ -L "$MULTIACE_CONFIG_FILE" ] && [ ! -e "$MULTIACE_CONFIG_FILE" ]; then
        rm -f "$MULTIACE_CONFIG_FILE"
    fi
    if [ ! -e "$MULTIACE_CONFIG_FILE" ]; then
        cp "$MULTIACE_APP_ROOT/config/extended/ace.cfg" "$MULTIACE_CONFIG_FILE"
    fi
    if ! multiace_ensure_save_variables; then
        return 1
    fi
    if ! multiace_sanitize_provider_config; then
        return 1
    fi

    if [ ! -e "$MULTIACE_STATE_DIR/ace_vars.cfg" ]; then
        cp "$MULTIACE_APP_ROOT/config/extended/multiace/ace_vars.cfg" \
            "$MULTIACE_STATE_DIR/ace_vars.cfg"
    fi
    if [ ! -d "$MULTIACE_STATE_DIR/i18n" ] && [ -d "$MULTIACE_APP_ROOT/i18n" ]; then
        cp -a "$MULTIACE_APP_ROOT/i18n" "$MULTIACE_STATE_DIR/i18n"
    fi
    mkdir -p "$MULTIACE_STATE_DIR/filament_snapshots"

    # The stock Klipper include glob loads this symlink. Refuse to overwrite a
    # user-created file or an unrelated integration at the same path.
    if [ -e "$MULTIACE_CONFIG_LINK" ] && [ ! -L "$MULTIACE_CONFIG_LINK" ]; then
        echo "multiACE config-link conflict: $MULTIACE_CONFIG_LINK is a file" >&2
        return 1
    fi
    if [ -L "$MULTIACE_CONFIG_LINK" ]; then
        if [ "$(readlink "$MULTIACE_CONFIG_LINK")" != "$MULTIACE_CONFIG_FILE" ]; then
            echo "multiACE config-link conflict: $MULTIACE_CONFIG_LINK points elsewhere" >&2
            return 1
        fi
    else
        ln -s "$MULTIACE_CONFIG_FILE" "$MULTIACE_CONFIG_LINK"
    fi

    touch "$MULTIACE_MANAGED_MARKER"
    chown -R lava:lava "$MULTIACE_STATE_DIR" 2>/dev/null || true
    chown lava:lava "$MULTIACE_CONFIG_FILE" 2>/dev/null || true
    chown -h lava:lava "$MULTIACE_CONFIG_LINK" 2>/dev/null || true
}

multiace_mounted() {
    awk -v target="$1" '$2 == target { found = 1 }
        END { exit found ? 0 : 1 }' /proc/mounts 2>/dev/null
}

multiace_bind_file() {
    source_file="$1"
    target_file="$2"
    if [ ! -f "$source_file" ]; then
        echo "multiACE package is missing $source_file" >&2
        return 1
    fi
    mkdir -p "$(dirname "$target_file")"
    if [ ! -e "$target_file" ]; then
        : > "$target_file"
    fi
    if multiace_mounted "$target_file"; then
        umount "$target_file" 2>/dev/null || return 1
    fi
    mount -o ro --bind "$source_file" "$target_file"
}

multiace_unmount_file() {
    if multiace_mounted "$1"; then
        umount "$1" 2>/dev/null || true
    fi
}

multiace_unmount_modules() {
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_protocol.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_protocol_v1.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_protocol_v2.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_bg_swap.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_tipform.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_rc522.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/ace_gen1_tunnel.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/filament_feed_ace.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/filament_feed.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/filament_switch_sensor_ace.py
    multiace_unmount_file /home/lava/klipper/klippy/extras/filament_switch_sensor.py
    multiace_unmount_file /home/lava/klipper/klippy/kinematics/extruder_ace.py
    multiace_unmount_file /home/lava/klipper/klippy/kinematics/extruder.py
}

multiace_mount_modules() {
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace.py" \
        /home/lava/klipper/klippy/extras/ace.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_protocol.py" \
        /home/lava/klipper/klippy/extras/ace_protocol.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_protocol_v1.py" \
        /home/lava/klipper/klippy/extras/ace_protocol_v1.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_protocol_v2.py" \
        /home/lava/klipper/klippy/extras/ace_protocol_v2.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_bg_swap.py" \
        /home/lava/klipper/klippy/extras/ace_bg_swap.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_tipform.py" \
        /home/lava/klipper/klippy/extras/ace_tipform.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_rc522.py" \
        /home/lava/klipper/klippy/extras/ace_rc522.py || return 1
    if [ -f "$MULTIACE_APP_ROOT/klipper/extras/ace_gen1_tunnel.py" ]; then
        multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/ace_gen1_tunnel.py" \
            /home/lava/klipper/klippy/extras/ace_gen1_tunnel.py || return 1
    fi
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/filament_feed_ace.py" \
        /home/lava/klipper/klippy/extras/filament_feed_ace.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/filament_feed_ace.py" \
        /home/lava/klipper/klippy/extras/filament_feed.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/filament_switch_sensor_ace.py" \
        /home/lava/klipper/klippy/extras/filament_switch_sensor_ace.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/extras/filament_switch_sensor_ace.py" \
        /home/lava/klipper/klippy/extras/filament_switch_sensor.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/kinematics/extruder_ace.py" \
        /home/lava/klipper/klippy/kinematics/extruder_ace.py || return 1
    multiace_bind_file "$MULTIACE_APP_ROOT/klipper/kinematics/extruder_ace.py" \
        /home/lava/klipper/klippy/kinematics/extruder.py || return 1
}

multiace_activate() {
    multiace_export_environment
    if ! multiace_seed_config; then
        multiace_unmount_modules
        rm -f "$MULTIACE_CONFIG_LINK"
        return 1
    fi
    multiace_unmount_modules
    if ! multiace_mount_modules; then
        echo "multiACE activation failed; restoring stock Klipper paths" >&2
        multiace_unmount_modules
        rm -f "$MULTIACE_CONFIG_LINK"
        return 1
    fi
    return 0
}

multiace_deactivate() {
    multiace_unmount_modules
    rm -f "$MULTIACE_CONFIG_LINK"
    if ! multiace_enabled; then
        rm -f "$MULTIACE_MANAGED_MARKER"
    fi
}
