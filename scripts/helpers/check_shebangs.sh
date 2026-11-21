#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

set -euo pipefail

if [[ $# -ne 1 || ! -d $1 ]]; then
  echo "Usage: $0 <staged-rootfs-directory>" >&2
  exit 2
fi

ROOTFS_DIR=$1
CR=$(printf '\r')
INVALID=0

while IFS= read -r -d '' file; do
  first_line=''
  IFS= read -r first_line < "$file" || [[ -n $first_line ]] || continue
  if [[ $first_line == '#!'* && $first_line == *"$CR" ]]; then
    printf '!! Invalid CRLF shebang in staged runtime file: %q\n' "$file" >&2
    INVALID=1
  fi
done < <(find "$ROOTFS_DIR" -type f -print0)

if (( INVALID )); then
  echo "Runtime shebangs must use LF line endings." >&2
  exit 1
fi
