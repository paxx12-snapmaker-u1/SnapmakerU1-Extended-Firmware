#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @plastikman

"""Convert a stock Panda Breath to DragonBreath over the network — NO USB.

Stdlib only (no external deps), so it runs on the U1's stock Python.

The device flip is a single HTTP POST to the stock firmware-update endpoint,
validated on stock 1.0.3 AND 1.0.4:

    POST /ota   Content-Type: application/octet-stream   header OTA-Type: ota_fw
    body = the raw DragonBreath app image (~1.08 MB; stock size ceiling 0x480000)

Stock writes it to the *inactive* OTA slot and reboots into it, leaving the stock
app in the other slot (so the device can always boot-inactive back to stock).
DragonBreath's first-boot NVS shim (>= v1.0.2) carries WiFi + Moonraker + HA over
from the stock NVS, so the device rejoins with no re-provisioning.

This script only performs the device flip (flash + wait-for-DragonBreath). The
Klipper config swap + klippy restart are done by the caller (the settings-YAML cmd).
"""
import argparse
import http.client
import json
import sys
import time
import urllib.request

DEFAULT_IMAGE = "/usr/local/share/chamber-heater/dragonbreath.bin"


def log(msg):
    print(msg, flush=True)


def probe(host, timeout=4):
    """Return the /api/v2/info dict if `host` answers as DragonBreath, else None."""
    try:
        with urllib.request.urlopen("http://%s/api/v2/info" % host, timeout=timeout) as r:
            info = json.load(r)
        if isinstance(info, dict) and info.get("project") == "dragonbreath":
            return info
    except Exception:
        pass
    return None


def flash(host, image_path, timeout=120):
    """POST the raw image to the stock /ota endpoint. Returns True on HTTP 200."""
    with open(image_path, "rb") as fh:
        body = fh.read()
    log(">> Uploading %d bytes to http://%s/ota ..." % (len(body), host))
    conn = http.client.HTTPConnection(host, 80, timeout=timeout)
    try:
        conn.request("POST", "/ota", body=body, headers={
            "Content-Type": "application/octet-stream;charset=UTF-8",
            "OTA-Type": "ota_fw",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        status = resp.status
        resp.read()
    finally:
        conn.close()
    log(">> /ota responded HTTP %d" % status)
    return status == 200


def wait_for_dragonbreath(host, timeout=90, interval=5):
    log(">> Waiting for %s to reboot into DragonBreath ..." % host)
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = probe(host)
        if info:
            log(">> DragonBreath is up (firmware %s)." % info.get("firmware"))
            return info
        time.sleep(interval)
    return None


def migrate(host, image_path):
    # Idempotency / resume guard: already DragonBreath? skip the flash.
    info = probe(host)
    if info:
        log(">> %s already reports DragonBreath (%s) — skipping flash." % (host, info.get("firmware")))
        return 0
    if not flash(host, image_path):
        log("!! Flash failed: the device did not return HTTP 200. Aborting; nothing changed.")
        return 2
    info = wait_for_dragonbreath(host)
    if not info:
        log("!! Device did not come back up as DragonBreath in time.")
        log("   The stock firmware is still in the inactive OTA slot — the device can")
        log("   boot-inactive to revert to stock. Retry once it is reachable.")
        return 3
    log(">> Device flip complete.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Panda Breath -> DragonBreath no-USB conversion")
    ap.add_argument("--host", required=True, help="device host/IP (read from panda_breath.cfg)")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="DragonBreath app image to flash")
    ap.add_argument("command", nargs="?", default="migrate", choices=["migrate", "probe"],
                    help="migrate = flip the device; probe = is it already DragonBreath?")
    args = ap.parse_args()

    if args.command == "probe":
        info = probe(args.host)
        print("dragonbreath" if info else "not-dragonbreath")
        return 0 if info else 1
    return migrate(args.host, args.image)


if __name__ == "__main__":
    sys.exit(main())
