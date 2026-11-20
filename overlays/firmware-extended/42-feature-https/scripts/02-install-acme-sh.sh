#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

if [[ -z "$CREATE_FIRMWARE" ]]; then
  echo "Error: This script should be run within the create_firmware.sh environment."
  exit 1
fi

set -eo pipefail

# acme.sh is bundled rather than downloaded on demand so ACME works on
# printers without internet access (private CA on the LAN).
VERSION=3.1.6
FILENAME=acme.sh-$VERSION.tar.gz
URL=https://github.com/acmesh-official/acme.sh/archive/refs/tags/$VERSION.tar.gz
SHA256=0d3f9000ac44a6331314742a88c475f79134e24fc991997883652adc59efc486

cache_file.sh "$CACHE_DIR/$FILENAME" "$URL" "$SHA256" "$BUILD_DIR/acme.sh"

SRC="$BUILD_DIR/acme.sh/acme.sh-$VERSION"
DEST="$ROOTFS_DIR/usr/local/share/acme.sh"

echo ">> Installing acme.sh $VERSION..."
# The overlay already placed dnsapi/dns_rfc2136.sh here; keep it.
install -d "$DEST/dnsapi"
install -m 755 "$SRC/acme.sh" "$DEST/acme.sh"
install -m 644 "$SRC/LICENSE.md" "$DEST/LICENSE.md"
install -m 644 "$SRC"/dnsapi/*.sh "$DEST/dnsapi/"

# dnspython for the dns_rfc2136 provider (offline DNS-01 via RFC 2136
# dynamic updates); the firmware has no nsupdate.
echo ">> Installing dnspython via pip3"
cache_pip.sh "$ROOTFS_DIR" --require-hashes -r "$(dirname "$0")/dnspython-requirements.txt"
