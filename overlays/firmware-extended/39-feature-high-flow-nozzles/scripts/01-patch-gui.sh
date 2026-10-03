#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

python3 "$(dirname "$0")/../patch_gui.py" "$1/usr/bin/gui"
