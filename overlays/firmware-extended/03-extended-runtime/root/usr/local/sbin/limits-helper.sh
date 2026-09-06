#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12
#
# Per-service cgroup v2 memory limits for extended-firmware services.
# Source this file in init scripts and call `limits_enter` before
# launching daemons, so they inherit the cgroup membership (same
# pattern as the stock S61moonraker joining the apps cgroup).
#

[ -f /usr/sbin/cgroup_helper.sh ] && . /usr/sbin/cgroup_helper.sh
EXT_CGROUP=${CGROUP_ROOT:-/sys/fs/cgroup}/ext

# apps_swap_enable
#
# Makes the stock `apps` cgroup swap-eligible. apps holds moonraker,
# nginx, unisrv, gui and wpa_supplicant - NOT klipper, which the
# stock firmware already isolates into its own `rt` cgroup (confirmed
# via /proc/<klipper-pid>/cgroup), so real-time motion control is
# never swap-eligible regardless of this.
#
# swap.max is just a permission ceiling, not a retroactive trigger:
# apps' members already finished their startup allocations by the
# time this runs (their own S* scripts run earlier in rcS, ours run
# at S99), and apps sits nowhere near its memory.high/memory.max, so
# nothing organically forces reclaim there - simply granting swap
# access wouldn't actually move any of that already-resident cold
# memory into zram on its own. Force it once, the first time this
# runs (guarded on swap.max still reading "0"): memory.reclaim only
# evicts genuinely cold/inactive pages regardless of the requested
# size, so asking for more than what's actually reclaimable is safe -
# it just does its best and stops, same as the kernel would do
# organically under real pressure, just on demand instead of waiting
# for pressure that may never meaningfully arrive.
apps_swap_enable() {
    [ -f /sys/fs/cgroup/apps/memory.swap.max ] || return 0
    _apps_first_time=$(cat /sys/fs/cgroup/apps/memory.swap.max 2>/dev/null)

    echo max > /sys/fs/cgroup/apps/memory.swap.max 2>/dev/null

    if [ "$_apps_first_time" = "0" ]; then
        echo 64M > /sys/fs/cgroup/apps/memory.reclaim 2>/dev/null
    fi
}

# limits_enter <name> <memory.max>
#
# Create/refresh /sys/fs/cgroup/ext/<name> with the given memory.max
# and move the calling shell (and thus every daemon it spawns) into it.
# Degrades gracefully: if cgroup v2 is unavailable the service still
# starts, just unlimited.
limits_enter() {
    _cg="$EXT_CGROUP/$1"

    mkdir -p "$EXT_CGROUP" 2>/dev/null
    echo "+memory" > "$EXT_CGROUP/cgroup.subtree_control" 2>/dev/null
    mkdir -p "$_cg" 2>/dev/null

    apps_swap_enable

    if [ -f "$_cg/memory.max" ]; then
        echo "$2" > "$_cg/memory.max" 2>/dev/null
        echo "max" > "$_cg/memory.swap.max" 2>/dev/null
        if echo $$ > "$_cg/cgroup.procs" 2>/dev/null; then
            echo "limits: joined cgroup ext/$1 (memory.max=$2)"
        else
            echo "limits: failed to join cgroup ext/$1, running unlimited"
        fi
    else
        echo "limits: memory controller unavailable for ext/$1, running unlimited"
    fi

    # Aggressive allocator tuning so freed heap actually goes back to the
    # OS instead of sitting in glibc's arenas / CPython's pymalloc pools.
    # PYTHONMALLOC=malloc is a no-op for the non-python services here
    # (camera/Go binaries); harmless to set unconditionally.
    export PYTHONMALLOC=malloc
    export MALLOC_ARENA_MAX=1
    export MALLOC_TRIM_THRESHOLD_=65536
    export MALLOC_MMAP_THRESHOLD_=65536
}
