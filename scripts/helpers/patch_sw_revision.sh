#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Fluidd and Mainsail's service worker precaches index.html under the revision
# hash listed in sw.js and keeps serving its cached copy until that hash
# changes. After editing index.html, set its revision to the new file's md5 so
# browsers that cached the stock page pick up the edited one.

set -eo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <sw.js> <index.html>"
  exit 1
fi

python3 - "$1" "$2" <<'PY'
import hashlib
import re
import sys

sw_path, index_path = sys.argv[1:]
with open(index_path, "rb") as f:
    revision = hashlib.md5(f.read()).hexdigest()
with open(sw_path, encoding="utf-8") as f:
    src = f.read()

# Fluidd: {"revision":"...","url":"index.html"}  Mainsail: {url:"index.html",revision:"..."}
patterns = [
    r'(\{"revision":")[0-9a-f]+(","url":"index\.html"\})',
    r'(\{url:"index\.html",revision:")[0-9a-f]+("\})',
]
matches = sum(len(re.findall(p, src)) for p in patterns)
if matches != 1:
    sys.exit(f"ERROR: expected the index.html precache entry once in {sw_path}, found {matches}; "
             "update the pattern for this web interface version")

for p in patterns:
    src = re.sub(p, lambda m: m.group(1) + revision + m.group(2), src)
with open(sw_path, "w", encoding="utf-8") as f:
    f.write(src)
print(f">> Updated the index.html revision in {sw_path}")
PY
