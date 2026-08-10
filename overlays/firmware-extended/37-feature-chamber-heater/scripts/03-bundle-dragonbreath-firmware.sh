#!/usr/bin/env bash
#
# Bundle the pinned DragonBreath device firmware INTO the paxx image at build time.
#
# WHY BUNDLE (not fetch at migrate time): the no-USB Panda -> DragonBreath conversion
# must NOT assume the printer or the Panda has an internet connection. The .bin is
# baked into the rootfs here on the CI/build host (which does have internet), and the
# U1 later flashes that local copy to the Panda over the LAN (POST /ota). So the only
# machine that needs internet is the build host, at build time.
#
# SIZE / COMPRESSION: the app image is ~1.08 MB of compiled ESP code. It compresses
# poorly (~0.6 MB) and a compressed bundle would add a decompress step + a new failure
# mode on the U1 at migrate time. Against a ~270 MB image the ~0.4 MB saving isn't
# worth that, so we ship it uncompressed and flash it straight from disk.
#
# The runtime flasher (dragonbreath_migrate.py) reads this exact path as its default
# --image, so keep BUNDLE_DEST in sync with dragonbreath_migrate.DEFAULT_IMAGE.

# Pinned DragonBreath release. Bump alongside the dragonbreath-klipper GIT_SHA pin in
# 01-install-dragonbreath.sh when moving to a new release.
DB_VERSION=v1.1.5
DB_URL="https://github.com/plastikman/DragonBreath/releases/download/${DB_VERSION}/dragonbreath-${DB_VERSION}.bin"
# sha256 of dragonbreath-v1.1.5.bin (from the release SHA256SUMS.txt).
DB_SHA256=c08a39fc3ea49f1d732b8af695657c312b3953666efd07f6af08df5821be772d

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

CACHE_BIN="$CACHE_DIR/dragonbreath-${DB_VERSION}.bin"
BUNDLE_DEST="$ROOTFS_DIR/usr/local/share/chamber-heater/dragonbreath.bin"

echo ">> Fetching + verifying DragonBreath ${DB_VERSION} device firmware (build host)..."
cache_file.sh "$CACHE_BIN" "$DB_URL" "$DB_SHA256"

echo ">> Bundling DragonBreath firmware into the image at ${BUNDLE_DEST#$ROOTFS_DIR}..."
install -Dm644 "$CACHE_BIN" "$BUNDLE_DEST"

echo ">> DragonBreath firmware bundled ($(stat -c%s "$CACHE_BIN") bytes, ${DB_VERSION})."
