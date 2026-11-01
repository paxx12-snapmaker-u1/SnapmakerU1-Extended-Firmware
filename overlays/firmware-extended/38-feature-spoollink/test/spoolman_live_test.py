#!/usr/bin/env python3
"""Live checks for the SpoolLink Spoolman integration against a running printer.

    python3 spoolman_live_test.py <host> [--spool ID] [--channel N] [--apply]

Exercises the two paths that work without an RFID tag:

  * a spool set the way Fluidd's Spoolman panel sets it, which must land on the
    channel of the toolhead in use instead of being reverted, and
  * `SET_SPOOL_ID` channel resolution, which must work with or without AFC-Lite.

Read-only by default. `--apply` runs the tests that write to the printer; they assign
and clear spool ids on real channels and the original values are restored afterwards.
Nothing runs while a print is active.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 10
SETTLE_SECONDS = 8  # SpoolLink resolves against Spoolman before Klipper is updated

_passes = 0
_failures = 0
_skipped = 0


def check(desc, actual, expected):
    global _passes, _failures
    if actual == expected:
        print(f"  PASS  {desc}")
        _passes += 1
    else:
        print(f"  FAIL  {desc}  (expected {expected!r}, got {actual!r})")
        _failures += 1
    return actual == expected


def check_true(desc, actual):
    return check(desc, bool(actual), True)


def skip(desc, why):
    global _skipped
    print(f"  SKIP  {desc} — {why}")
    _skipped += 1


def section(title):
    print(f"\n=== {title} ===")


class Printer:
    def __init__(self, host, api_key=None):
        self.base = host if "://" in host else f"http://{host}"
        self.base = self.base.rstrip("/")
        self.api_key = api_key

    def _request(self, path, payload=None, method=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                parsed = json.loads(body)
            except ValueError:
                return {"error": {"message": body[:200]}}
            # Moonraker already answers with {"error": {...}}; don't wrap it twice.
            return parsed if "error" in parsed else {"error": parsed}
        if not body:
            return {}
        try:
            return json.loads(body)
        except ValueError:
            return {"raw": body}

    def get(self, path):
        return self._request(path)

    def post(self, path, payload):
        return self._request(path, payload)

    def objects(self, *names):
        query = "&".join(names)
        result = self.get(f"/printer/objects/query?{query}")
        return (result.get("result") or {}).get("status", {})

    def gcode(self, script):
        """Run a gcode script. Returns None on success, else the error message."""
        result = self.post("/printer/gcode/script", {"script": script})
        if "error" in result:
            err = result["error"]
            return err.get("message") or str(err)
        return None

    def spoolman(self, path, query=None):
        body = {"request_method": "GET", "path": path}
        if query:
            body["query"] = query
        return self.post("/server/spoolman/proxy", body)


def extruder_channel(extruder):
    if extruder == "extruder":
        return 0
    try:
        return int(extruder.replace("extruder", ""))
    except ValueError:
        return 0


def spool_ids(printer):
    ptc = printer.objects("print_task_config").get("print_task_config", {})
    return list(ptc.get("filament_spool_id") or [])


def identity(printer, channel):
    ptc = printer.objects("print_task_config").get("print_task_config", {})
    return (
        (ptc.get("filament_vendor") or [""] * 4)[channel],
        (ptc.get("filament_type") or [""] * 4)[channel],
    )


def wait_for(predicate, seconds=SETTLE_SECONDS):
    """Poll until the printer catches up; SpoolLink resolves asynchronously."""
    deadline = time.time() + seconds
    value = predicate()
    while not value and time.time() < deadline:
        time.sleep(0.5)
        value = predicate()
    return value


def preflight(printer):
    section("Preflight")
    info = printer.get("/server/info")
    result = info.get("result", info)
    state = result.get("klippy_state")
    check("Klipper is ready", state, "ready")

    components = result.get("components", [])
    check_true("spoolman component loaded", "spoolman" in components)
    spoollink = "spoollink" in components
    check_true("spoollink component loaded", spoollink)

    status = printer.get("/server/spoolman/status")
    connected = (status.get("result") or status).get("spoolman_connected")
    check_true("Spoolman server connected", connected)

    stats = printer.objects("print_stats").get("print_stats", {})
    printing = stats.get("state") in ("printing", "paused")
    check("printer is idle", printing, False)

    help_text = json.dumps(printer.get("/printer/gcode/help"))
    check_true("SET_SPOOL_ID macro exists", "SET_SPOOL_ID" in help_text)

    objects = printer.get("/printer/objects/list")
    names = (objects.get("result") or {}).get("objects", [])
    afc = any(n.startswith("AFC_lane ") for n in names)
    print(f"  INFO  AFC-Lite lanes {'present' if afc else 'absent'} "
          f"({'lane names resolve via AFC' if afc else 'lane names must resolve by index'})")

    ids = spool_ids(printer)
    th = printer.objects("toolhead").get("toolhead", {})
    channel = extruder_channel(th.get("extruder", "extruder"))
    print(f"  INFO  active toolhead: {th.get('extruder')} (channel {channel})")
    print(f"  INFO  filament_spool_id: {ids}")
    return not printing and state == "ready", channel, ids


def pick_spool(printer, requested, assigned):
    spools = printer.spoolman("/v1/spool")
    payload = spools.get("response", spools.get("result", spools))
    if isinstance(payload, dict):
        payload = payload.get("response", [])
    if not isinstance(payload, list) or not payload:
        return None, "no spools returned by Spoolman"
    ids = [s.get("id") for s in payload if s.get("id")]
    if requested:
        if requested not in ids:
            return None, f"spool {requested} is not in the library ({len(ids)} spools)"
        return requested, None
    unused = [i for i in ids if i not in assigned]
    return (unused or ids)[0], None


def test_active_spool(printer, channel, spool_id):
    section("A spool set the way Fluidd sets it lands on the toolhead in use")
    before = spool_ids(printer)
    printer.post("/server/spoolman/spool_id", {"spool_id": spool_id})

    landed = wait_for(lambda: (spool_ids(printer) or [0])[channel] == spool_id)
    check(f"filament_spool_id[{channel}] is {spool_id}",
          (spool_ids(printer) or [0])[channel], spool_id)

    active = printer.get("/server/spoolman/spool_id")
    active_id = (active.get("result") or active).get("spool_id")
    check("the selection was not reverted", active_id, spool_id)

    if landed:
        vendor, material = identity(printer, channel)
        check_true(f"filament identity applied ({vendor} {material})",
                   vendor and material)
    else:
        skip("filament identity applied", "the spool never landed on the channel")

    after = spool_ids(printer)
    others = [(i, b, a) for i, (b, a) in enumerate(zip(before, after)) if i != channel and b != a]
    check("no other channel was touched", others, [])
    return before


def test_set_spool_id(printer, channel, spool_id):
    section("SET_SPOOL_ID resolves the channel")
    before = spool_ids(printer)

    err = printer.gcode(f"SET_SPOOL_ID LANE=E{channel} SPOOL_ID={spool_id}")
    check(f"LANE=E{channel} accepted", err, None)
    wait_for(lambda: (spool_ids(printer) or [0])[channel] == spool_id)
    check(f"LANE=E{channel} assigned spool {spool_id}",
          (spool_ids(printer) or [0])[channel], spool_id)

    err = printer.gcode(f"SET_SPOOL_ID LANE=E{channel} SPOOL_ID=0")
    check("clearing accepted", err, None)
    wait_for(lambda: (spool_ids(printer) or [1])[channel] == 0)
    check("clearing released the channel", (spool_ids(printer) or [1])[channel], 0)

    err = printer.gcode(f"SET_SPOOL_ID CHANNEL={channel} SPOOL_ID={spool_id}")
    check(f"CHANNEL={channel} accepted", err, None)
    wait_for(lambda: (spool_ids(printer) or [0])[channel] == spool_id)
    check(f"CHANNEL={channel} assigned spool {spool_id}",
          (spool_ids(printer) or [0])[channel], spool_id)

    snapshot = spool_ids(printer)
    err = printer.gcode(f"SET_SPOOL_ID LANE=bogus SPOOL_ID={spool_id}")
    check_true("an unrecognised lane is rejected", err is not None)
    # A lane that resolves to nothing must not quietly land on channel 0.
    check("a rejected lane assigned nothing", spool_ids(printer), snapshot)
    return before


def restore(printer, original_ids, original_active):
    section("Restore")
    current = spool_ids(printer)
    for channel, (was, now) in enumerate(zip(original_ids, current)):
        if was == now:
            continue
        err = printer.gcode(
            f"SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER={channel} "
            f"FILAMENT_SPOOL_ID={was} FORCE=1")
        check(f"channel {channel} restored to {was}", err, None)
    printer.post("/server/spoolman/spool_id", {"spool_id": original_active})
    print(f"  INFO  filament_spool_id: {spool_ids(printer)}")
    print(f"  INFO  active spool restored to {original_active}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="printer host or URL (e.g. 192.168.1.50)")
    parser.add_argument("--spool", type=int, help="Spoolman spool id to test with")
    parser.add_argument("--channel", type=int,
                        help="channel for the SET_SPOOL_ID tests (default: an unassigned one)")
    parser.add_argument("--apply", action="store_true",
                        help="run the tests that write to the printer")
    parser.add_argument("--api-key", help="Moonraker API key, if required")
    args = parser.parse_args()

    printer = Printer(args.host, args.api_key)
    ok, active_channel, ids = preflight(printer)
    if not ok:
        print("\nPreflight failed — not running further tests.")
        return 1

    if not args.apply:
        print("\nRead-only run. Re-run with --apply to exercise the write paths;")
        print("they assign and clear spool ids on real channels and restore them after.")
        print(f"\n{_passes} passed, {_failures} failed, {_skipped} skipped")
        return 1 if _failures else 0

    spool_id, why = pick_spool(printer, args.spool, ids)
    if spool_id is None:
        print(f"\nCannot pick a spool to test with: {why}")
        return 1
    print(f"\nUsing spool #{spool_id}")

    original_active = (printer.get("/server/spoolman/spool_id").get("result") or {}).get("spool_id")
    original_ids = list(ids)

    try:
        test_active_spool(printer, active_channel, spool_id)
        channel = args.channel
        if channel is None:
            free = [i for i, v in enumerate(spool_ids(printer))
                    if not v and i != active_channel]
            channel = free[0] if free else active_channel
        test_set_spool_id(printer, channel, spool_id)
    finally:
        restore(printer, original_ids, original_active)

    print(f"\n{_passes} passed, {_failures} failed, {_skipped} skipped")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
