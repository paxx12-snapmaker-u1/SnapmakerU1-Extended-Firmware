#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# Run spoolman_test.py against a fresh Spoolman in Docker, once per version.
#
#   run-spoolman.sh                  # 0.27.0 (native tags) and 0.26.1
#   run-spoolman.sh 0.27.0           # one version
#   PORT=17912 run-spoolman.sh       # another host port (default 17912)

set -euo pipefail

TEST_DIR="$(dirname "$(realpath "$0")")"
PORT="${PORT:-17912}"
VERSIONS=("$@")
[[ ${#VERSIONS[@]} -gt 0 ]] || VERSIONS=(0.27.0 0.26.1)

CONTAINER="spoollink-test-$$"
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true' EXIT

failed=()
for version in "${VERSIONS[@]}"; do
  echo ">> Spoolman $version"
  docker run -d --rm --name "$CONTAINER" -p "127.0.0.1:$PORT:8000" \
    "ghcr.io/donkie/spoolman:$version" >/dev/null

  for _ in $(seq 60); do
    curl -fs "http://127.0.0.1:$PORT/api/v1/info" >/dev/null && break
    sleep 1
  done

  python3 "$TEST_DIR/spoolman_test.py" "http://127.0.0.1:$PORT" || failed+=("$version")
  docker rm -f "$CONTAINER" >/dev/null
  echo ""
done

if [[ ${#failed[@]} -gt 0 ]]; then
  echo ">> FAILED on Spoolman ${failed[*]}"
  exit 1
fi
echo ">> All passed on Spoolman ${VERSIONS[*]}"
