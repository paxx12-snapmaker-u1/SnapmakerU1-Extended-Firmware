#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Fluidd and Mainsail register a service worker that answers every page
# navigation with the app's cached index.html, except the API paths listed in
# its denylist. Browsers only run service workers over HTTPS, so with HTTPS
# enabled the extended firmware's own pages (/firmware-config/, /screen/, ...)
# are replaced by a broken copy of the web interface. Add them to the denylist.

set -eo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <sw.js> <denylist-anchor>"
  exit 1
fi

SW_JS="$1"
ANCHOR="$2"

# Pages served by nginx next to the web interface (fluidd.d/*.conf)
EXTENDED_PATHS='/^\/(firmware-config|screen|filament|https-check|multiace|detect-http)(\/|$)/,'

python3 - "$SW_JS" "$ANCHOR" "$EXTENDED_PATHS" <<'PY'
import sys

path, anchor, paths = sys.argv[1:]
with open(path, encoding="utf-8") as f:
    src = f.read()

count = src.count(anchor)
if count != 1:
    sys.exit(f"ERROR: expected the service worker denylist '{anchor}' once in {path}, found {count}; "
             "update the anchor for this web interface version")

head = anchor.index("[") + 1
src = src.replace(anchor, anchor[:head] + paths + anchor[head:])
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print(f">> Excluded extended firmware pages from the service worker in {path}")
PY
