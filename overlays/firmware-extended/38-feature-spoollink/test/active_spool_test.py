#!/usr/bin/env python3
"""Offline tests for SpoolLink's handling of Moonraker's active spool.

Loads the component with stubbed Moonraker services - no printer, no Spoolman and no
Moonraker install required:

    python3 active_spool_test.py

Moonraker tracks a single active spool while the U1 has four channels, so a frontend
that sets it (Fluidd's Spoolman panel, a mobile app) can say which spool but not where.
These tests cover what SpoolLink does with that selection.
"""

import asyncio
import importlib.util
import os
import sys

COMPONENT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '../root/home/lava/moonraker/moonraker/components/spoollink.py')

_passes = 0
_failures = 0


def check(desc, actual, expected):
    global _passes, _failures
    if actual == expected:
        print(f"  PASS  {desc}")
        _passes += 1
    else:
        print(f"  FAIL  {desc}  (expected {expected!r}, got {actual!r})")
        _failures += 1


def section(title):
    print(f"\n=== {title} ===")


# -- Moonraker stubs --------------------------------------------------------

SPOOLS = {
    12: {"id": 12, "extra": {}, "filament": {
        "material": "PETG", "color_hex": "00FF00",
        "vendor": {"name": "Overture"}, "extra": {"variant": '"Matte"'}}},
    3: {"id": 3, "extra": {}, "filament": {
        "material": "PLA", "color_hex": "FF0000", "vendor": {"name": "Elegoo"}}},
}


class Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def text(self):
        return str(self._payload)


class HttpClient:
    def __init__(self):
        self.patches = []

    async def get(self, url, enable_cache=False):
        if "/api/v1/field/" in url:
            return Response([{"key": "card_uids"}, {"key": "variant"}])
        if "/api/v1/spool?" in url:
            return Response(list(SPOOLS.values()))
        spool_id = int(url.rsplit("/", 1)[-1])
        return Response(SPOOLS.get(spool_id), 200 if spool_id in SPOOLS else 404)

    async def post(self, url, body=None):
        return Response({}, 201)

    async def request(self, method, url, body=None):
        self.patches.append((method, url, body))
        spool_id = int(url.rsplit("/", 1)[-1])
        return Response(SPOOLS[spool_id])


class KlippyAPI:
    def __init__(self):
        self.sets = []
        self.gcodes = []

    async def _send_klippy_request(self, endpoint, params):
        self.sets.append((endpoint, params))
        return {}

    async def run_gcode(self, script, *args):
        self.gcodes.append(script)

    async def subscribe_objects(self, objects, callback, default):
        return {}


class Spoolman:
    def __init__(self):
        self.calls = []

    def set_active_spool(self, spool_id):
        self.calls.append(spool_id)


class ServerError(Exception):
    pass


class Server:
    def __init__(self):
        self.error = ServerError
        self.components = {
            "http_client": HttpClient(),
            "klippy_apis": KlippyAPI(),
            "spoolman": Spoolman(),
        }

    def lookup_component(self, name, default=None):
        return self.components.get(name, default)

    def register_remote_method(self, *args):
        pass

    def register_event_handler(self, *args):
        pass


class Config:
    def __init__(self, server):
        self._server = server

    def get_server(self):
        return self._server

    def get(self, key, default=None):
        return {"server": "http://spoolman:7912", "cache_dir": None}.get(key, default)

    def getboolean(self, key, default=False):
        return default


def build(toolhead="extruder1", spool_ids=(0, 0, 0, 0)):
    server = Server()
    link = spoollink.SpoolLink(Config(server))
    link._toolhead_extruder = toolhead
    link._ptc_spool_ids = list(spool_ids)
    return link, server


async def _fire(link, payload):
    """Deliver the event the way Moonraker does, then let the tasks it spawns finish."""
    link._handle_active_spool_set(payload)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    while pending:
        await asyncio.gather(*pending)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


def fire(link, payload):
    asyncio.run(_fire(link, payload))


def applied(server):
    """(channel, SPOOL_ID, VENDOR) of each filament pushed to Klipper."""
    return [(p["channel"], p["info"]["SPOOL_ID"], p["info"]["VENDOR"])
            for _, p in server.components["klippy_apis"].sets if "info" in p]


# -- Tests ------------------------------------------------------------------

def main():
    section("A selection from Fluidd lands on the toolhead in use")
    link, server = build(toolhead="extruder1")
    fire(link, {"spool_id": 12})

    check("spool applied to the current channel", applied(server), [(1, 12, "Overture")])
    check("the selection is not reverted", server.components["spoolman"].calls, [])
    check("SpoolLink still considers spool 12 active", link._active_spool_id, 12)

    section("Regression: the selection used to be overwritten with None")
    # Before the fix `_sync_active_spool()` ran straight from the event, read
    # `filament_spool_id` for the channel (0, no card scanned) and pushed that back,
    # clearing what the user had just chosen.
    check("no spool was cleared", None in server.components["spoolman"].calls, False)

    section("Selecting the spool the channel already holds changes nothing")
    link, server = build(toolhead="extruder", spool_ids=(3, 0, 0, 0))
    fire(link, {"spool_id": 3})

    check("nothing re-applied", applied(server), [])
    check("no active spool churn", server.components["spoolman"].calls, [])

    section("Clearing leaves the channel's spool alone")
    link, server = build(toolhead="extruder", spool_ids=(3, 0, 0, 0))
    link._active_spool_id = 3  # the channel's spool is what is currently active
    fire(link, {"spool_id": None})

    check("channel not unassigned", applied(server), [])
    check("active spool restored from the channel",
          server.components["spoolman"].calls, [3])

    section("An unknown spool does not strand the channel")
    link, server = build(toolhead="extruder")
    fire(link, {"spool_id": 999})

    check("nothing applied", applied(server), [])

    print(f"\n{_passes} passed, {_failures} failed")
    return 1 if _failures else 0


if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('spoollink', COMPONENT)
    spoollink = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spoollink)
    sys.exit(main())
