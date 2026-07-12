#!/usr/bin/env bash

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

inject_shortcut() {
  local index_file="$1"
  local frontend_name="$2"

  if [[ ! -f "$index_file" ]]; then
    echo ">> $frontend_name index.html not found, skipping Firmware Config shortcut injection."
    return
  fi

  if grep -q 'firmware-config-shortcut.js' "$index_file"; then
    echo ">> Firmware Config shortcut already present in $frontend_name."
    return
  fi

  perl -0pi -e '
    BEGIN { $tag = "    <script defer src=\"/firmware-config-shortcut.js\"></script>\n" }
    if (index($_, "firmware-config-shortcut.js") < 0) {
      s#</body>#$tag  </body># or die "Could not find </body>\n";
    }
  ' "$index_file"

  echo ">> Injected Firmware Config shortcut into $frontend_name."
}

inject_shortcut "$ROOTFS_DIR/home/lava/fluidd/index.html" "Fluidd"
inject_shortcut "$ROOTFS_DIR/home/lava/mainsail/index.html" "Mainsail"
