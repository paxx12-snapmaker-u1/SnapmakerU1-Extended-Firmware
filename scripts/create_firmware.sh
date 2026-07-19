#!/usr/bin/env bash

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <upgrade.bin> <temp-dir> <output.bin> [overlays...]"
  exit 1
fi

if [[ $(id -u) -ne 0 ]]; then
  echo "Error: This script must be run as root (sudo) - squashfs operations require root privileges"
  exit 1
fi

set -eo pipefail

IN_FIRMWARE="$(realpath "$1")"
OUT_FIRMWARE="$(realpath -m "$3")"

export CREATE_FIRMWARE=1
export ROOT_DIR="$(realpath "$(dirname "$0")/..")"
export CACHE_DIR="$ROOT_DIR/tmp/cache"
export PATH="$ROOT_DIR/scripts/helpers:$PATH"
export BUILD_DIR="$(realpath -m "$2")"
export ROOTFS_DIR="$BUILD_DIR/rootfs"
export BOOT_IMG="$BUILD_DIR/rk-unpacked/boot.img"
export ROOTFS_IMG="$BUILD_DIR/rk-unpacked/rootfs.img"

# Cache dirs for build tools
export GOPATH="$ROOT_DIR/tmp/cache-go"
export CCACHE_DIR="$ROOT_DIR/tmp/ccache"
export CHROOT_CACHE="$ROOT_DIR/tmp/cache-chroot"

rm -rf "$BUILD_DIR"

shift 3

check_perms() {
  local file="$1"
  local expected_uid="$2"
  local expected_gid="$3"

  if [[ ! -e "$file" ]]; then
    echo "Error: $file does not exist for ownership check."
    exit 1
  fi

  local actual_uid=$(stat -c '%u' "$file")
  local actual_gid=$(stat -c '%g' "$file")

  if [[ "$actual_uid" != "$expected_uid" ]] || [[ "$actual_gid" != "$expected_gid" ]]; then
    echo "Error: $file should be $expected_uid:$expected_gid, got $actual_uid:$actual_gid"
    echo "This system does not properly preserve file ownership in squashfs operations."
    exit 1
  fi
}

echo ">> Unpacking firmware..."
"$ROOT_DIR/scripts/helpers/unpack_firmware.sh" "$IN_FIRMWARE" "$BUILD_DIR"

echo ">> Extracting squashfs from rootfs.img..."
unsquashfs -d "$ROOTFS_DIR" "$BUILD_DIR/rk-unpacked/rootfs.img"

echo ">> Verifying ownership preservation..."
check_perms "$ROOTFS_DIR/etc/passwd" 0 0
check_perms "$ROOTFS_DIR/home/lava/bin/hwver.sh" 1000 1000
echo "   Ownership check passed"

if [[ -z "$CI" ]]; then
  echo ">> Restoring chroot cache..."
  mkdir -p "$CHROOT_CACHE" "$ROOTFS_DIR/cache"
  cp -a "$CHROOT_CACHE/." "$ROOTFS_DIR/cache/"
fi

for overlay; do
  if [[ ! -d "$overlay" ]]; then
    echo "!! Overlay directory '$overlay' does not exist, skipping."
    exit 1
  fi

  echo ">> Applying overlay $overlay..."
  if [[ -d "$overlay/pre-scripts/" ]]; then
    for scriptfile in "$overlay/pre-scripts/"*.sh; do
      echo "[+] Running pre-script: $(basename "$scriptfile")"
      ./"$scriptfile" "$ROOTFS_DIR"
    done
  fi

  if [[ -d "$overlay/patches/" ]]; then
    pushd "$overlay/patches/" > /dev/null
    # apply all .patch to their respective directories
    while read -r patchfile; do
      echo "[+] Applying patch: $(basename "$patchfile") in subdir $(dirname "$patchfile")"
      patch -F 0 --no-backup-if-mismatch -d "$ROOTFS_DIR/$(dirname "$patchfile")" -p1 < "$patchfile"
    done < <(find -type f -name "*.patch" | sort)
    popd > /dev/null
  fi

  if [[ -d "$overlay/root/" ]]; then
    echo ">> Copying custom files..."
    cp -rv "$overlay/root/." "$ROOTFS_DIR/"
  fi

  if [[ -d "$overlay/scripts/" ]]; then
    for scriptfile in "$overlay/scripts/"*.sh; do
      echo "[+] Running script: $(basename "$scriptfile")"
      ./"$scriptfile" "$ROOTFS_DIR"
    done
  fi
done

if [[ -z "$CI" ]]; then
  echo ">> Saving chroot cache..."
  cp -a "$ROOTFS_DIR/cache/." "$CHROOT_CACHE/"
  rm -rf "$ROOTFS_DIR/cache"
fi

echo ">> Checking for non-ARM binaries in rootfs..."
if FILES=$(find "$ROOTFS_DIR" -type f -exec file {} + | grep "ELF" | grep -v "ARM"); then
  echo "!! Error: Found non-ARM binaries in the rootfs:"
  echo "$FILES"
  exit 1
fi

echo ">> Create squash filesystem..."
mksquashfs "$ROOTFS_DIR" "$BUILD_DIR/rk-unpacked/rootfs-v2.img" -comp gzip

echo ">> Replace rootfs.img in firmware..."
mv -v "$BUILD_DIR/rk-unpacked"/{rootfs-v2,rootfs}.img

echo ">> Update version..."
git rev-parse --short HEAD >> "$BUILD_DIR/UPFILE_VERSION"

echo ">> Repacking firmware..."
"$ROOT_DIR/scripts/helpers/pack_firmware.sh" "$BUILD_DIR" "$OUT_FIRMWARE"

echo ">> Done: $OUT_FIRMWARE"
