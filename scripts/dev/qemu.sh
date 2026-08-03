#!/bin/bash

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <rootfs.img>"
  exit 1
fi

ROOT_DIR="$(realpath "$(dirname "$0")/../..")"
TMP_DIR="$ROOT_DIR/tmp"

KERNEL_TAG=20260112-d688db2
KERNEL_FILENAME=kernel-open-6.1-$KERNEL_TAG-vmlinuz
KERNEL_URL=https://github.com/Snapmaker-U1-Extended-Firmware/base-kernel/releases/download/$KERNEL_TAG/$KERNEL_FILENAME
KERNEL_SHA256=2ebcebbc4b524e76e3a3ff9fef704478853f2d90e4d273221dfc4a5e0dfe5498
KERNEL_FILE="$TMP_DIR/$KERNEL_FILENAME"

ROOTFS_IMG="$(realpath "$1")"
DISK_IMG="$TMP_DIR/disk.img"

APPEND="console=ttyAMA0,115200 earlycon=pl011,mmio32,0x09000000"
APPEND="$APPEND systemd.volatile=overlay ro rootwait rootfstype=squashfs root=/dev/vda"
APPEND="$APPEND storagemedia=emmc androidboot.storagemedia=emmc androidboot.mode=normal androidboot.verifiedbootstate=orange android_slotsufix=_a vertype=rel"
APPEND="$APPEND androidboot.fwver=ddr-v1.07-6e9ae14bbb,bl31-v1.21,bl32-v1.07,uboot-3ed3e17641-12/30/2025"
APPEND="$APPEND video=Virtual-1:480x320 net.ifnames=0"

set -e

mkdir -p "$TMP_DIR"

if [[ ! -f "$TMP_DIR/$KERNEL_FILENAME" ]]; then
  echo ">> Downloading $KERNEL_FILENAME..."
  wget -O "$TMP_DIR/$KERNEL_FILENAME" "$KERNEL_URL"
fi

echo ">> Verifying $TMP_DIR/$KERNEL_FILENAME checksum..."
if ! echo "$KERNEL_SHA256  $TMP_DIR/$KERNEL_FILENAME" | sha256sum --check --status; then
  echo "[!] SHA256 checksum mismatch for $KERNEL_FILENAME"
  exit 1
fi

if [[ ! -f "$DISK_IMG" ]]; then
  echo ">> Creating $DISK_IMG..."
  rm -f "$DISK_IMG.tmp"
  truncate -s "2G" "$DISK_IMG.tmp"
  parted "$DISK_IMG.tmp" --script \
    mklabel gpt \
    mkpart misc 2MiB 128MiB \
    mkpart oem 128MiB 1024MiB \
    mkpart userdata 1024MiB 2047MiB
  mkfs.ext4 -F -L oem -E offset=$((128 * 1024 * 1024)) "$DISK_IMG.tmp" $((896 * 1024))
  mkfs.ext4 -F -L userdata -E offset=$((1024 * 1024 * 1024)) "$DISK_IMG.tmp" $((1023 * 1024))
  mv "$DISK_IMG.tmp" "$DISK_IMG"
fi

echo ">> Press Ctrl+A then X to exit the emulated system..."
sleep 1s

echo ">> Starting qemu..."
qemu-system-aarch64 \
  -machine virt \
  -cpu cortex-a57 \
  -m 1024 \
  -serial mon:stdio \
  -display none \
  -global virtio-mmio.force-legacy=false \
  -device virtio-gpu-device,xres=480,yres=320 \
  -device virtio-multitouch-device \
  -kernel "$KERNEL_FILE" \
  -append "$APPEND initcall_blacklist=rockchip_drm_init" \
  -netdev user,id=net0,hostfwd=tcp::2222-:22,hostfwd=tcp::2280-:80,hostfwd=tcp::2443-:443 \
  -device virtio-net-device,netdev=net0 \
  -drive "if=none,file=$DISK_IMG,format=raw,id=hd1" \
  -device "virtio-blk-device,drive=hd1" \
  -drive "if=none,file=$ROOTFS_IMG,format=raw,id=hd0" \
  -device "virtio-blk-device,drive=hd0" \
