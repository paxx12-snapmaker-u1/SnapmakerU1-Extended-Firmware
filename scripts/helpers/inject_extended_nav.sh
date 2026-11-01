#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Load /firmware-config/nav.js in the web interface, which links the extended
# firmware's own pages (/firmware-config/, /screen/, ...) from its menu.

set -eo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <index.html>"
  exit 1
fi

python3 - "$1" <<'PY'
import sys

path = sys.argv[1]
anchor = "</body>"
script = '<script src="/firmware-config/nav.js" defer></script>\n'
with open(path, encoding="utf-8") as f:
    src = f.read()

count = src.count(anchor)
if count != 1:
    sys.exit(f"ERROR: expected '{anchor}' once in {path}, found {count}")

src = src.replace(anchor, script + anchor)
with open(path, "w", encoding="utf-8") as f:
    f.write(src)
print(f">> Added extended firmware navigation to {path}")
PY
