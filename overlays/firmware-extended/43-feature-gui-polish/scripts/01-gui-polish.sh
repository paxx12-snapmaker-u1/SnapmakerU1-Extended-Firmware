#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @xtronets
#
# Polish for the stock touchscreen GUI (opt-in, `[components] gui_language`).
#
# The stock GUI reads its texts from JSON files, but its language list and menu
# labels are compiled into /usr/bin/gui. This script builds a copy of the binary
# with the "Türkçe" menu label renamed to "Polski"; the lmd hook bind-mounts it
# and the Polish texts over the stock files only when the option is selected.
#
# The label sits in a fixed, NUL-padded slot, so the copy has the same layout.
# If the label is not found exactly once (e.g. a new stock GUI), no copy is
# built and the menu keeps the "Türkçe" label - the build itself never fails.

set -eo pipefail

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

CUR_DIR="$(realpath "$(dirname "$0")")"
DEST_DIR="$ROOTFS_DIR/usr/local/share/gui-polish"
RES_DIR="$ROOTFS_DIR/home/lava/resource"
GUI_BIN="$ROOTFS_DIR/usr/bin/gui"

echo ">> Comparing the Polish translation with this firmware's English texts..."
python3 - "$CUR_DIR/../i18n/source" "$RES_DIR" <<'EOF'
import json, sys
src, res = sys.argv[1], sys.argv[2]
pairs = (("en-GB.json", "locales/en-GB.json"), ("exception_en-GB.json", "text/exception_en-GB.json"))
stale = 0
for snap, stock in pairs:
    old = json.load(open(f"{src}/{snap}", encoding="utf-8"))
    new = json.load(open(f"{res}/{stock}", encoding="utf-8"))
    n = sum(1 for k in new if old.get(k) != new[k]) + sum(1 for k in old if k not in new)
    if n:
        print(f"!! WARNING: {stock}: {n} text(s) changed since the Polish translation was made.")
        stale += n
if stale:
    print("!! Missing texts fall back to English. See overlays/firmware-extended/43-feature-gui-polish/i18n/README.md")
else:
    print("[+] Polish translation is up to date with this firmware")
EOF

echo ">> Building /usr/bin/gui copy with the 'Polski' language label..."
python3 - "$GUI_BIN" "$DEST_DIR/gui" <<'EOF'
import os, shutil, sys

src, dst = sys.argv[1], sys.argv[2]
old = "Türkçe".encode() + b"\0"
new = "Polski".encode()
new += b"\0" * (len(old) - len(new))

data = open(src, "rb").read()
count = data.count(old)
if count != 1:
    print(f"!! WARNING: expected one 'Türkçe' label in /usr/bin/gui, found {count}. "
          "Polish will be listed as 'Türkçe' in the language menu.")
    sys.exit(0)

offset = data.index(old)
shutil.copy2(src, dst)
st = os.stat(src)
os.chown(dst, st.st_uid, st.st_gid)
with open(dst, "r+b") as f:
    f.seek(offset)
    f.write(new)
print(f"[+] Built {dst.split('/rootfs', 1)[-1]} (label at offset {hex(offset)})")
EOF
