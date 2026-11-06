#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

set -euo pipefail

checkout_path="${1:-$PWD}"
kernel_release_file="/proc/sys/kernel/osrelease"

if [[ -z "${WSL_INTEROP:-}${WSL_DISTRO_NAME:-}" ]] \
    && ! grep -qiE 'microsoft|wsl' "$kernel_release_file" 2>/dev/null; then
  exit 0
fi

if ! command -v findmnt >/dev/null 2>&1; then
  echo "Error: Cannot verify the checkout filesystem because findmnt is unavailable." >&2
  echo "Install util-linux or move the checkout to the WSL Linux filesystem." >&2
  exit 1
fi

filesystem_type="$(findmnt --target "$checkout_path" --noheadings --output FSTYPE 2>/dev/null | tr -d '[:space:]' || true)"
if [[ -z "$filesystem_type" ]]; then
  echo "Error: Could not determine the filesystem for the WSL checkout: $checkout_path" >&2
  echo "Move the checkout to the WSL Linux filesystem and rerun ./dev.sh." >&2
  exit 1
fi

case "$filesystem_type" in
  drvfs|9p)
    echo "Error: The WSL checkout is on a Windows-mounted filesystem ($filesystem_type): $checkout_path" >&2
    echo "Move the repository into the WSL Linux filesystem (for example, ~/src) and rerun ./dev.sh." >&2
    exit 1
    ;;
esac
