#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <rootfs> [pip params]"
  echo "  A --hash=<algo>:<digest> right after a requirement (e.g. pkg==1.0)"
  echo "  pins that requirement's file, like in a requirements file."
  exit 1
fi

ROOTFS_DIR="$1"
shift

PIP_ARGS=()
HASHED_REQS=()
STAGED_FILES=()
cleanup() {
  rm -f "${STAGED_FILES[@]}"
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-r" ]]; then
    shift
    STAGED="/tmp/$(basename "$1").$$"
    cp "$1" "$ROOTFS_DIR$STAGED"
    STAGED_FILES+=("$ROOTFS_DIR$STAGED")
    PIP_ARGS+=(-r "$STAGED")
    shift
  elif [[ "$1" == --hash=* ]]; then
    if [[ ${#PIP_ARGS[@]} -eq 0 || "${PIP_ARGS[-1]}" == -* ]]; then
      echo "Error: $1 must follow a requirement"
      exit 1
    fi
    if [[ ${#HASHED_REQS[@]} -gt 0 && "${HASHED_REQS[-1]%% *}" == "${PIP_ARGS[-1]}" ]]; then
      HASHED_REQS[-1]+=" $1"
    else
      HASHED_REQS+=("${PIP_ARGS[-1]} $1")
    fi
    shift
  else
    PIP_ARGS+=("$1")
    shift
  fi
done

# Hashed requirements go to a requirements file: pip takes --hash only there
HASHED_FILE=
if [[ ${#HASHED_REQS[@]} -gt 0 ]]; then
  HASHED_FILE="/tmp/hashed-requirements.txt.$$"
  printf '%s\n' "${HASHED_REQS[@]}" > "$ROOTFS_DIR$HASHED_FILE"
  STAGED_FILES+=("$ROOTFS_DIR$HASHED_FILE")
fi

chroot_cmd() {
  chroot_firmware.sh "$ROOTFS_DIR" "$@"
}

# Install the hashed requirements alone first, checked against their hashes
# (--no-deps: with --require-hashes pip would want every dependency hashed).
# The plain install after that finds them satisfied and adds their dependencies.
if [[ -n "$HASHED_FILE" ]]; then
  chroot_cmd bash -c '
    pip3 install --no-index --find-links=/cache/pip --no-deps --require-hashes -r "$1" ||
    (
      pip3 download -d /cache/pip --no-deps --require-hashes -r "$1" &&
      pip3 install --no-index --find-links=/cache/pip --no-deps --require-hashes -r "$1"
    )
  ' -- "$HASHED_FILE"
fi

chroot_cmd bash -c '
  pip3 install --no-index --find-links=/cache/pip "$@" ||
  (
    pip3 download -d /cache/pip "$@" &&
    pip3 install --no-index --find-links=/cache/pip "$@"
  )
' -- "${PIP_ARGS[@]}"
