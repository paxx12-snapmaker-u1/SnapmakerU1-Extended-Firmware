#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

set -eu

OVERLAY_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/multiace-config-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT

CONFIG_FILE="$TEST_DIR/ace.cfg"
cat > "$CONFIG_FILE" <<'EOF'
[ace]
ace_device_count: 2

[gcode_macro ACEH__Update_Check]
description: Check GitHub for a newer multiACE release (no install)
gcode:
  ACE_UPDATE_CHECK

[gcode_macro ACEH__Update_Apply]
description: Download + install the latest multiACE release
gcode:
  ACE_UPDATE_APPLY

[gcode_macro ACEG__Status]
description: Show active ACE, detected devices, and head mapping
gcode:
  ACE_HEAD_STATUS

[gcode_macro INNER_RESUME]
description: Resume the actual running print
gcode:
  RESUME
EOF

# common.sh only defines helpers when sourced. Override the firmware path with
# the temporary fixture before invoking the PAXX sanitizer.
MULTIACE_PRINTER_DATA="$TEST_DIR/printer_data"
MULTIACE_CONFIG_DIR="$MULTIACE_PRINTER_DATA/config"
MULTIACE_APP_DIR="$TEST_DIR/apps/multiace/latest"
MULTIACE_MANAGED_MARKER="$MULTIACE_CONFIG_DIR/extended/multiace/.multiace-managed"
. "$OVERLAY_DIR/root/usr/local/share/multiace/common.sh"

if [ "$MULTIACE_CONFIG_DIR" != "$TEST_DIR/printer_data/config" ]; then
    echo "MULTIACE_CONFIG_DIR must remain the printer config directory" >&2
    exit 1
fi
if [ "$MULTIACE_STATE_DIR" != "$MULTIACE_CONFIG_DIR/extended/multiace" ]; then
    echo "multiACE state directory does not follow the shared config contract" >&2
    exit 1
fi
multiace_export_environment
[ "$MULTIACE_MANAGED" = "1" ]
[ "$MULTIACE_MANAGED_MARKER" = "$TEST_DIR/printer_data/config/extended/multiace/.multiace-managed" ]
[ "$MULTIACE_APP_DIR" = "$TEST_DIR/apps/multiace/latest" ]
[ "$MULTIACE_CONFIG_DIR" = "$TEST_DIR/printer_data/config" ]
[ "$MULTIACE_PRINTER_DATA" = "$TEST_DIR/printer_data" ]

MULTIACE_CONFIG_FILE="$CONFIG_FILE"
multiace_sanitize_provider_config

if grep -q 'ACEH__Update_' "$CONFIG_FILE"; then
    echo "update wrapper macros were not removed" >&2
    exit 1
fi
grep -q '^ace_device_count: 2$' "$CONFIG_FILE"
grep -q '^\[gcode_macro ACEG__Status\]$' "$CONFIG_FILE"
grep -q '^\[gcode_macro INNER_RESUME\]$' "$CONFIG_FILE"

# The managed provider archive omits its standalone save_variables section;
# PAXX supplies the persistent location without overwriting an existing one.
multiace_ensure_save_variables
grep -Fqx "filename: $MULTIACE_STATE_DIR/ace_vars.cfg" "$CONFIG_FILE"

cp "$CONFIG_FILE" "$TEST_DIR/first.cfg"
multiace_sanitize_provider_config
multiace_ensure_save_variables
cmp -s "$CONFIG_FILE" "$TEST_DIR/first.cfg"

# Keep the printer-specific USB permission fix persistent and only match the
# adapter observed during ACE 2 Pro testing.
UDEV_RULE="$OVERLAY_DIR/root/etc/udev/rules.d/99-multiace-serial.rules"
grep -Fqx 'SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", GROUP="lava", MODE="0660"' \
    "$UDEV_RULE"

# Selecting the managed component must update an older installed package to
# the pin in the firmware rather than treating download as an upgrade.
SETTINGS_FILE="$OVERLAY_DIR/root/usr/local/share/firmware-config/functions/27_settings_multiace.yaml"
grep -Fq '/usr/local/bin/extended-pkg multiace needs_upgrade' "$SETTINGS_FILE"
grep -Fq '/usr/local/bin/extended-pkg multiace upgrade' "$SETTINGS_FILE"

# Show the multiACE page in Firmware Config's Quick Links only while the
# PAXX-managed multiACE component is selected.
LINKS_FILE="$OVERLAY_DIR/root/usr/local/share/firmware-config/functions/11_links_multiace.yaml"
grep -Fq 'url: /multiace/' "$LINKS_FILE"
grep -Fq 'setting: ace' "$LINKS_FILE"
grep -Fq 'value: multiace' "$LINKS_FILE"

echo "multiACE managed config sanitizer test passed"
