#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12
#
# Checks that cache_git.sh leaves the cache at the requested revision. Uses a local
# throwaway repository as the "remote", so it needs no network.

set -uo pipefail

HELPER="$(realpath "$(dirname "$0")/../cache_git.sh")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
check() {
  if [[ "$2" == "$3" ]]; then
    echo "  PASS  $1"
    pass=$((pass + 1))
  else
    echo "  FAIL  $1"
    echo "          expected: $3"
    echo "          actual:   $2"
    fail=$((fail + 1))
  fi
}

# A local repository with two revisions to pin to.
REMOTE="$WORK/remote"
git init -q "$REMOTE"
git -C "$REMOTE" config user.email t@example.invalid
git -C "$REMOTE" config user.name Test
echo one > "$REMOTE/file"
git -C "$REMOTE" add file
git -C "$REMOTE" commit -qm one
OLD_SHA="$(git -C "$REMOTE" rev-parse HEAD)"
echo two > "$REMOTE/file"
git -C "$REMOTE" commit -qam two
NEW_SHA="$(git -C "$REMOTE" rev-parse HEAD)"

CACHE="$WORK/cache"

echo "=== A fresh cache is cloned at the requested revision ==="
"$HELPER" "$CACHE" "$REMOTE" "$OLD_SHA" > /dev/null 2>&1
check "cloned at the pin" "$(git -C "$CACHE" rev-parse HEAD)" "$OLD_SHA"

echo "=== A cache left at another revision is moved to the requested one ==="
# The regression: the helper used to return early whenever the directory existed,
# leaving whatever revision a previous build had checked out. A build crossing a
# version bump then shipped the previous build's dependency.
"$HELPER" "$CACHE" "$REMOTE" "$NEW_SHA" > /dev/null 2>&1
check "moved to the new pin" "$(git -C "$CACHE" rev-parse HEAD)" "$NEW_SHA"

echo "=== Asking for the revision already checked out is a no-op ==="
"$HELPER" "$CACHE" "$REMOTE" "$NEW_SHA" > /dev/null 2>&1
check "still at the pin" "$(git -C "$CACHE" rev-parse HEAD)" "$NEW_SHA"

echo "=== A revision absent from the cache is fetched ==="
# Prove the fetch fallback still works: drop the object and ask for it again.
rm -rf "$CACHE"
git clone -q "$REMOTE" "$CACHE"
git -C "$CACHE" checkout -q "$OLD_SHA"
"$HELPER" "$CACHE" "$REMOTE" "$NEW_SHA" > /dev/null 2>&1
check "fetched and checked out" "$(git -C "$CACHE" rev-parse HEAD)" "$NEW_SHA"

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
