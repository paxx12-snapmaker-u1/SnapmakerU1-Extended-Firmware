#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Show all four nozzle diameters in both stock GUI flow modes."""

import argparse
import hashlib
from pathlib import Path


# U1_2.0.0.205_20260914173503_upgrade.bin, /usr/bin/gui (AArch64 PIE).
# The GUI source is not distributed with the firmware. Offsets below are both
# file offsets and virtual addresses in its .text section. Refuse other builds.
ORIGINAL_SHA256 = "bd2d80e9084c6709161fd9ca4130f06c57e89b00ffa596bc6c5706c4f1aebda4"

# In the nozzle panel's visibility updater at 0x119400, always take the
# show-all path at 0x119430. This bypasses high-flow hiding and repositioning
# without changing the selected flow type or the save callback.
PATCHES = (
    (0x119418, "d4000034", "06000014", "cbz w20, 0x119430 -> b 0x119430"),
)


def patch_image(data):
    """Validate the entire image before changing any bytes; allow safe reruns."""
    restored = bytearray(data)
    for offset, before, after, description in PATCHES:
        current = data[offset:offset + 4]
        if current not in (bytes.fromhex(before), bytes.fromhex(after)):
            raise ValueError(f"Unsupported GUI instruction at {offset:#x}: {description}")
        restored[offset:offset + 4] = bytes.fromhex(before)
    if hashlib.sha256(restored).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("Unsupported GUI build; high-flow nozzle patch needs review")
    result = bytearray(data)
    for offset, before, after, description in PATCHES:
        result[offset:offset + 4] = bytes.fromhex(after)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gui", type=Path)
    args = parser.parse_args()
    original = args.gui.read_bytes()
    try:
        patched = patch_image(original)
    except ValueError as error:
        parser.exit(1, f"{error}; {args.gui} was not modified\n")
    if patched != original:
        args.gui.write_bytes(patched)
    print("GUI high-flow nozzle options enabled: 0.2, 0.4, 0.6, 0.8 mm")


if __name__ == "__main__":
    main()
