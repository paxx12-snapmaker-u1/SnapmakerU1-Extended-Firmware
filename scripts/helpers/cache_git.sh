#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <target-dir> <git-url> <git-rev>"
  exit 1
fi

TARGET_DIR="$1"
GIT_URL="$2"
GIT_SHA="$3"

set -e

if [[ ! -d "$TARGET_DIR" ]]; then
  echo ">> Cloning $GIT_URL into $TARGET_DIR"
  git clone "$GIT_URL" "$TARGET_DIR" --recursive
elif [[ -n "$CI" ]]; then
  echo ">> CI environment detected. Forcing the git repository to be re-fetched."
else
  echo ">> Using cached git repository in $TARGET_DIR"
  exit 0
fi

echo ">> Fetching $GIT_SHA into $TARGET_DIR"
if ! git -C "$TARGET_DIR" checkout -f "$GIT_SHA"; then
  git -C "$TARGET_DIR" remote set-url origin "$GIT_URL" || git -C "$TARGET_DIR" remote add origin "$GIT_URL"
  git -C "$TARGET_DIR" fetch origin "$GIT_SHA"
  git -C "$TARGET_DIR" checkout -f "$GIT_SHA"
fi

git -C "$TARGET_DIR" submodule update --init --recursive --checkout --force

