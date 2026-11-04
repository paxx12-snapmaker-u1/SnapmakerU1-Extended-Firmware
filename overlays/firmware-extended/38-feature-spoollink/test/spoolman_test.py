#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

"""Test SpoolLink's card UID stores against a live, empty Spoolman.

Loads the real Moonraker `spoollink` component with stubbed Moonraker parts,
and runs the real `spoollink_migrate.py` and firmware-config commands. The
server must be empty, as the test creates its own spools; `run-spoolman.sh`
starts a fresh one in Docker for each Spoolman version.

    spoolman_test.py http://localhost:7912

Spoolman v0.27.0 or newer runs the native tag tests, older servers check
that native tags are refused. Stdlib only; the firmware-config checks also
need PyYAML and are skipped without it.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

FEATURE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..")
OVERLAYS_DIR = os.path.join(FEATURE_DIR, "..")
COMPONENT = os.path.join(FEATURE_DIR, "root/home/lava/moonraker/moonraker/components/spoollink.py")
MIGRATE = os.path.join(FEATURE_DIR, "root/usr/local/bin/spoollink_migrate.py")
FUNCTIONS = os.path.join(FEATURE_DIR, "root/usr/local/share/firmware-config/functions")
EXTENDED_CONFIG = os.path.join(OVERLAYS_DIR, "02-firmware-config/root/usr/local/bin/extended-config.py")
EXTENDED_CFG = os.path.join(OVERLAYS_DIR, "02-firmware-config/root/usr/local/share/firmware-config/extended/extended2.cfg")

URL = ""
_passes = 0
_failures = 0


def check(label, got, want):
    global _passes, _failures
    if got == want:
        _passes += 1
        print(f"PASS {label}: {got!r}")
    else:
        _failures += 1
        print(f"FAIL {label}: {got!r} (want {want!r})")


# -- Spoolman REST -------------------------------------------------------------

class Response:
    def __init__(self, status_code, data):
        self.status_code, self._data = status_code, data

    def json(self):
        return json.loads(self._data) if self._data else None

    def text(self):
        return self._data.decode() if self._data else ""


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Response(resp.status, resp.read())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read())


def api(method, path, body=None):
    resp = http(method, f"{URL}/api/v1{path}", body)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"{method} {path}: HTTP {resp.status_code} {resp.text()}")
    return resp.json()


def card_uids(spool_id):
    raw = (api("GET", f"/spool/{spool_id}").get("extra") or {}).get("card_uids", "")
    return json.loads(raw) if raw else ""


def tags(spool_id):
    return sorted(t["uid"] for t in api("GET", f"/spool/{spool_id}").get("tags") or [])


# -- Moonraker stubs -----------------------------------------------------------

class HttpClient:
    async def get(self, url, enable_cache=True):
        return http("GET", url)

    async def post(self, url, body=None):
        return http("POST", url, body)

    async def delete(self, url):
        return http("DELETE", url)

    async def request(self, method, url, body=None, **kwargs):
        return http(method, url, body)


class KlippyAPI:
    def __init__(self):
        self.sets = []

    async def _send_klippy_request(self, endpoint, params):
        self.sets.append(params)
        return {}

    async def run_gcode(self, *args):
        pass


class Server:
    error = Exception

    def __init__(self):
        self.components = {"http_client": HttpClient(), "klippy_apis": KlippyAPI()}

    def lookup_component(self, name):
        return self.components[name]

    def register_remote_method(self, *args):
        pass

    def register_event_handler(self, *args):
        pass


class ConfigHelper:
    error = ValueError

    def __init__(self, values):
        self.values = values
        self.server = Server()

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getboolean(self, key, default=False):
        return self.values.get(key, default)

    def getchoice(self, key, choices, default_key, force_lowercase=False):
        value = self.values.get(key, default_key)
        value = value.lower() if force_lowercase else value
        if value not in choices:
            raise self.error(f"invalid {key}: {value}")
        return choices[value]


def load_spoollink(**config):
    spec = importlib.util.spec_from_file_location("spoollink", COMPONENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SpoolLink(ConfigHelper({"server": URL, **config}))


def last_set(link):
    return link.klippy_apis.sets[-1]


def migrate(store):
    result = subprocess.run([sys.executable, "-I", MIGRATE, "--host", URL, store],
                            capture_output=True, text=True)
    return result.returncode, (result.stdout.strip().splitlines() or [""])[-1]


# -- Tests ---------------------------------------------------------------------

async def test_stores(native):
    vendor = api("POST", "/vendor", {"name": "SpoolLink Test"})
    filament = api("POST", "/filament", {
        "name": "Test", "vendor_id": vendor["id"], "material": "PLA",
        "density": 1.24, "diameter": 1.75, "color_hex": "FF0000"})

    def spool(uids=""):
        body = {"filament_id": filament["id"]}
        if uids:
            body["extra"] = {"card_uids": json.dumps(uids)}
        return api("POST", "/spool", body)["id"]

    legacy = load_spoollink()
    await legacy.component_init()  # creates the card_uids field

    try:
        load_spoollink(tag_store="bogus")
        check("invalid tag_store rejected", False, True)
    except ValueError:
        check("invalid tag_store rejected", True, True)

    print("-- card_uids store")
    a, b = spool("AA01"), spool()
    await legacy._resolve_spool(0, card_uid="AA01")
    check("resolves by card", last_set(legacy)["info"]["SPOOL_ID"], a)
    if native:
        api("POST", f"/spool/{b}/tag", {"uid": "AA02"})
        await legacy._resolve_spool(0, card_uid="AA02")
        check("ignores native tags", last_set(legacy)["status"], "error")
    await legacy._resolve_spool(0, spool_id=b, card_uid="AA01")
    check("move writes card_uids only", (card_uids(a), card_uids(b), tags(b)),
          ("", "AA01", ["AA02"] if native else []))

    link = load_spoollink(tag_store="native")
    await link.component_init()
    if not native:
        print("-- native store on Spoolman without native tags")
        await link._resolve_spool(0, card_uid="AA01")
        check("card resolve errors", (last_set(link)["status"], "v0.27.0" in last_set(link)["message"]),
              ("error", True))
        check("migration to native refused", migrate("native")[0], 1)
        check("migration to card_uids is a no-op", migrate("card_uids"),
              (0, "Moved 0 card(s) to card_uids."))
        return

    print("-- migration to native")
    s1, s2 = spool("BB01,BB02"), spool("BB03")
    d1, d2 = spool("BB04"), spool("BB04")
    t1, t2 = spool("BB05"), spool()
    api("POST", f"/spool/{t2}/tag", {"uid": "BB05"})
    check("exit and summary", migrate("native"),
          (0, "Moved 4 card(s) to native tags; 2 left in place, see above."))
    check("cards tagged", (tags(s1), tags(s2), tags(b)), (["BB01", "BB02"], ["BB03"], ["AA01", "AA02"]))
    check("card_uids cleared", (card_uids(s1), card_uids(s2), card_uids(b)), ("", "", ""))
    check("card on two spools kept", (card_uids(d1), card_uids(d2), tags(d1)), ("BB04", "BB04", []))
    check("card tagged elsewhere dropped", (card_uids(t1), tags(t1), tags(t2)), ("", [], ["BB05"]))
    check("rerun succeeds", migrate("native")[0], 0)
    check("rerun changes nothing", (tags(s1), card_uids(d1)), (["BB01", "BB02"], "BB04"))

    print("-- native store")
    await link._resolve_spool(0, card_uid="BB02")
    check("resolves by tag", last_set(link)["info"]["SPOOL_ID"], s1)
    await link._resolve_spool(0, card_uid="BB04")
    check("card_uids-only card not found", last_set(link)["status"], "error")
    await link._resolve_spool(0, spool_id=s2, card_uid="BB01")
    check("move", (tags(s1), tags(s2), card_uids(s2)), (["BB02"], ["BB01", "BB03"], ""))
    await link._unlink_card(0, card_uid="BB01")
    check("unlink", tags(s2), ["BB03"])

    print("-- migration to card_uids")
    api("PATCH", f"/spool/{d1}", {"extra": {"card_uids": json.dumps("BB03")}})
    check("exit", migrate("card_uids")[0], 0)
    check("tags copied", (card_uids(s1), card_uids(s2), card_uids(t2)), ("BB02", "BB03", "BB05"))
    check("tags unlinked", (tags(s1), tags(s2), tags(t2), tags(b)), ([], [], [], []))
    check("stale card_uids entry dropped", card_uids(d1), "")
    await legacy._resolve_spool(0, card_uid="BB03")
    check("resolves by card_uids again", last_set(legacy)["info"]["SPOOL_ID"], s2)


# Runs the firmware-config commands with printer paths pointed at this
# checkout, a temporary extended2.cfg and a stubbed Moonraker init script.
def test_firmware_config(native):
    try:
        import yaml
    except ImportError:
        print("SKIP firmware-config: PyYAML not installed")
        return
    print("-- firmware-config")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "extended2.cfg")
        with open(EXTENDED_CFG) as src, open(cfg, "w") as dst:
            dst.write(src.read())
        config_tool = f"{sys.executable} {EXTENDED_CONFIG}"
        subprocess.run(f"{config_tool} add {cfg} spoolman host {URL}", shell=True,
                       check=True, capture_output=True)

        def localize(script):
            return (script
                    .replace("/oem/printer_data/config/extended/extended2.cfg", cfg)
                    .replace("/usr/local/bin/extended-config.py", config_tool)
                    .replace("/usr/local/bin/spoollink_migrate.py", f"{sys.executable} -I {MIGRATE}")
                    .replace("/etc/init.d/S61moonraker", "echo moonraker"))

        def run(cmd):
            return subprocess.run([cmd[0], cmd[1], localize(cmd[2])], capture_output=True, text=True)

        settings = yaml.safe_load(open(os.path.join(FUNCTIONS, "24_settings_snapmaker_components_spoolman.yaml")))
        actions = yaml.safe_load(open(os.path.join(FUNCTIONS, "29_actions_spoollink.yaml")))
        setting = settings["settings"]["snapmaker_components"]["items"]["spoolman_tag_store"]
        retry = actions["actions"]["troubleshooting"]["items"]["spoollink-retry-migration"]

        def current():
            return run(setting["get_cmd"]).stdout.strip()

        check("setting hidden", (setting.get("hidden"), retry.get("hidden")), (True, True))
        check("shown with Spoolman enabled",
              subprocess.run(localize(setting["if_cmd"]), shell=True).returncode, 0)
        check("defaults to card_uids", current(), "card_uids")

        result = run(setting["options"]["native"]["cmd"])
        if native:
            check("switch to native", (result.returncode, current()), (0, "native"))
            check("retry", (run(retry["cmd"]).returncode, current()), (0, "native"))
        else:
            check("switch to native refused", (result.returncode, current()), (1, "card_uids"))
        check("Moonraker restarted", result.stdout.strip().splitlines()[-1], "moonraker start")
        result = run(setting["options"]["card_uids"]["cmd"])
        check("switch to card_uids", (result.returncode, current()), (0, "card_uids"))


def main():
    global URL
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="Spoolman URL, e.g. http://localhost:7912")
    URL = parser.parse_args().url.rstrip("/")

    version = str(api("GET", "/info").get("version") or "")
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    native = bool(match) and tuple(int(g) for g in match.groups()) >= (0, 27, 0)
    if api("GET", "/spool?allow_archived=true"):
        print(f"ERROR: Spoolman at {URL} has spools; the test needs an empty server")
        return 1
    print(f"Spoolman {version} at {URL}, native tags {'supported' if native else 'not supported'}")

    asyncio.run(test_stores(native))
    test_firmware_config(native)

    print(f"\n{_passes} passed, {_failures} failed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
