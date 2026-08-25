#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 @paxx12, @liberodark

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <repo> <local-path> [file-to-check]"
  echo ""
  echo "Klipper Example:"
  echo ""
  echo "$0 https://github.com/Klipper3d/klipper.git ./tmp/extracted/rootfs/home/lava/klipper/ \ "
  echo "  scripts/klippy-requirements.txt klippy/extras/{bme280,bus}.py scripts/spi_flash/board_defs.py \ "
  echo "  klippy/extras/probe_eddy_current.py"
  echo ""
  echo "Moonraker Example:"
  echo ""
  echo "$0 https://github.com/Arksine/moonraker.git ./tmp/extracted/rootfs/home/lava/moonraker/ \ "
  echo "  moonraker/eventloop.py moonraker/components/data_store.py moonraker/components/power.py \ "
  echo "  scripts/install-moonraker.sh moonraker/components/application.py"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/..")"
REPO="$1"
REPO_NAME="$(basename "$REPO" .git)"
LOCAL_PATH="$(realpath "$2")"
shift 2

cd "$ROOT_DIR/tmp"

if [[ ! -d "$REPO_NAME" ]]; then
  git clone "$REPO" "$REPO_NAME"
fi

if [[ -n "$UPPER_COMMIT" ]]; then
  echo ">> Checking out upper commit $UPPER_COMMIT"
  git -C "$REPO_NAME" checkout -f "$UPPER_COMMIT"
else
  echo ">> Using latest commit as upper commit"
  git -C "$REPO_NAME" checkout -f master
  git -C "$REPO_NAME" clean -fdx
  UPPER_COMMIT=$(git -C "$REPO_NAME" rev-parse HEAD)
fi

if [[ -z "$LOWER_COMMIT" ]]; then
  echo ">> Using initial commit as lower commit"
  LOWER_COMMIT=$(git -C "$REPO_NAME" rev-list --max-parents=0 HEAD)
fi

for file; do
  echo ">> Checking file $file..."
  REPO_FILE="$REPO_NAME/$file"
  LOCAL_FILE="$LOCAL_PATH/$file"
  LOCAL_SHA256="$(sha256sum "$LOCAL_FILE" | awk '{print $1}')"

  git -C "$REPO_NAME" checkout -q -f "$UPPER_COMMIT"

  while ! echo "$LOCAL_SHA256  $REPO_FILE" | sha256sum --check --status; do
    if [[ "$(git -C "$REPO_NAME" rev-parse HEAD)" == "$LOWER_COMMIT" ]]; then
      echo "No matching version found for file $file"
      exit 1
    fi

    if ! git -C "$REPO_NAME" checkout -q -f HEAD~1; then
      echo "No matching version found for file $file"
      exit 1
    fi
  done

  UPPER_COMMIT="$(git -C "$REPO_NAME" rev-parse HEAD)"

  while echo "$LOCAL_SHA256  $REPO_FILE" | sha256sum --check --status; do
    PREV_COMMIT=$(git -C "$REPO_NAME" rev-parse HEAD)
    if [[ "$PREV_COMMIT" == "$LOWER_COMMIT" ]]; then
      break
    fi

    if ! git -C "$REPO_NAME" checkout -q -f HEAD~1; then
      echo "No matching version found for file $file"
      exit 1
    fi
  done

  LOWER_COMMIT="$PREV_COMMIT"

  COMMIT_COUNT=$(git -C "$REPO_NAME" rev-list --count "$LOWER_COMMIT".."$UPPER_COMMIT")

  echo ">> Found matching version for file $file between $LOWER_COMMIT..$UPPER_COMMIT ($COMMIT_COUNT commits)"
done


echo ">> All files matched successfully."
echo ""
echo "Use the following range to get the exact version matching all files:"
echo ""
echo "export LOWER_COMMIT=$LOWER_COMMIT"
echo "export UPPER_COMMIT=$UPPER_COMMIT"
echo ""