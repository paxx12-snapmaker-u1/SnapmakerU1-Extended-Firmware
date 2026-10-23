#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Offline regression tests for Push Notifications: the Moonraker component
# (against a stand-in Moonraker, Spoolman and fake services) and
# notifications-config.py (validation, generated Moonraker and Klipper
# config). Needs Python 3.9+, apprise and Pillow; no printer or network.
#
#   python3 overlays/firmware-extended/39-feature-notifications/test/notifications_test.py
#
# Exits non-zero if any check fails.

import asyncio
import copy
import email
import importlib.util
import json
import os
import re
import shutil
import smtplib
import sys

# The build copies root/ as it is: no bytecode left in it by a test run
sys.dont_write_bytecode = True
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "root")
COMPONENTS = os.path.join(ROOT, "home/lava/moonraker/moonraker/components")
CONFIG_TOOL = os.path.join(ROOT, "usr/local/bin/notifications-config.py")

_passes = 0
_failures = 0


def check(name, ok, detail=""):
    global _passes, _failures
    if ok:
        _passes += 1
        print(f"  ok    {name}")
    else:
        _failures += 1
        print(f"  FAIL  {name}" + (f": {detail}" if detail else ""))


# ---- stand-in Moonraker ----------------------------------------------------

WORK = tempfile.mkdtemp(prefix="notifications_test_")
PKG = os.path.join(WORK, "moonraker")
os.makedirs(os.path.join(PKG, "components"))
open(os.path.join(PKG, "__init__.py"), "w").close()
open(os.path.join(PKG, "components", "__init__.py"), "w").close()
with open(os.path.join(PKG, "common.py"), "w") as f:
    f.write('''from enum import Enum
class KlippyState(Enum):
    DISCONNECTED = 1; STARTUP = 2; READY = 3; ERROR = 4; SHUTDOWN = 5
class JobEvent(Enum):
    STANDBY = 1; STARTED = 2; PAUSED = 3; RESUMED = 4
    COMPLETE = 5; CANCELLED = 6; ERROR = 7
    def __str__(self): return self.name.lower()
    @property
    def finished(self): return self.value >= 5
    @property
    def aborted(self): return self.value >= 6
    @property
    def is_printing(self): return self.value in (2, 4)
''')
for name in os.listdir(COMPONENTS):
    if name.startswith("push_notifications") and name.endswith(".py"):
        shutil.copy(os.path.join(COMPONENTS, name),
                    os.path.join(PKG, "components", name))
sys.path.insert(0, WORK)

from moonraker.common import JobEvent  # noqa: E402
import moonraker.components.push_notifications as pn  # noqa: E402

pn.SEND_RETRY_DELAYS = (0.2, 0.2, 0.2)
pn.PAUSE_SNAPSHOT_DELAY = 0.1
pn.FILAMENT_POLL = 0.2

STATE = {}
META = {}
SPOOLS = {}
SECRETS = {}


class Loop:
    def create_task(self, coro):
        return asyncio.get_event_loop().create_task(coro)

    async def run_in_thread(self, fn, *args):
        return fn(*args)


class Meta:
    def get(self, key, default=None):
        return dict(META)


GCODES = tempfile.mkdtemp(prefix="gcodes_", dir=WORK)


class FileManager:
    def get_metadata_storage(self):
        return Meta()

    def get_directory(self, root):
        return GCODES


class Klippy:
    async def query_objects(self, objs, default=None):
        return {k: copy.deepcopy(STATE.get(k, {})) for k in objs}

    async def subscribe_objects(self, objs, cb, default=None):
        return {k: copy.deepcopy(STATE.get(k, {})) for k in objs}


class Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


class Http:
    async def get(self, url, enable_cache=False):
        sid = int(url.rstrip("/").rsplit("/", 1)[1])
        spool = SPOOLS.get(sid)
        return Resp(200, copy.deepcopy(spool)) if spool else Resp(404, None)


class Server:
    def __init__(self):
        self.handlers = {}
        self.remote = {}

    def get_event_loop(self):
        return Loop()

    def register_event_handler(self, name, fn):
        self.handlers[name] = fn

    def register_remote_method(self, name, fn):
        self.remote[name] = fn

    def lookup_component(self, name, default=None):
        return {"klippy_apis": Klippy(), "file_manager": FileManager(),
                "http_client": Http()}.get(name, default)

    def get_app_args(self):
        return {"data_path": os.path.join(WORK, "data")}


class Config:
    error = Exception

    def __init__(self, server, values):
        self.server = server
        self.values = values

    def get_server(self):
        return self.server

    def get_name(self):
        return "push_notifications"

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getint(self, key, default=None, **_kw):
        return int(self.values.get(key, default))

    def getfloat(self, key, default=None, **_kw):
        return float(self.values.get(key, default))

    def gettemplate(self, key, default=None, **_kw):
        # Moonraker renders {secrets.<section>.<name>} from its secrets file
        value = str(self.values.get(key, default))

        class T:
            def render(self_):
                return re.sub(r"\{secrets\.notifications\.(\w+)\}",
                              lambda m: SECRETS.get(m.group(1), ""), value)
        return T()


# ---- fake services ---------------------------------------------------------

class FakeServer:
    headers = {}


class FakeService:
    """Apprise stand-in: records what was sent, fails the first `fail`
    attempts, takes `delay` seconds per attempt."""

    def __init__(self, log, channel, fail=0, delay=0.0):
        self.log, self.channel, self.fail, self.delay = log, channel, fail, delay
        self.srv = FakeServer()

    def __iter__(self):
        return iter([self.srv])

    async def async_notify(self, body, title, attach=None, notify_type=None):
        await asyncio.sleep(self.delay)
        ok = self.fail <= 0
        self.fail -= 1
        self.log.append(dict(channel=self.channel, title=title, body=body,
                             attach=attach, ok=ok, t=time.monotonic(),
                             headers=dict(self.srv.headers)))
        return ok


def snapshot_file():
    from PIL import Image
    d = tempfile.mkdtemp(prefix="snap_", dir=WORK)  # the component deletes d
    p = os.path.join(d, "snapshot.jpg")
    Image.new("RGB", (64, 48), (40, 160, 200)).save(p)
    return p


def make(values, services):
    """A component with fake services: services = {row: [(channel, fake,
    email)]}."""
    server = Server()
    os.makedirs(os.path.join(WORK, "data"), exist_ok=True)
    state = os.path.join(WORK, "data", ".push_notifications.json")
    if os.path.exists(state):
        os.remove(state)
    c = pn.PushNotifications(Config(server, dict(
        dict(name="U1", settle_time="0.1"), **values)))
    c.rows = {rid: ("snapshot" if not rid.startswith(("filament", "spool"))
                    else "spool",
                    [pn.Destination(ch, fake, em) for ch, fake, em in dests])
              for rid, dests in services.items()}

    async def snap(*_a, **_k):
        return snapshot_file()
    c._fetch_snapshot = snap
    c._thumbnail = lambda *_a: None
    return server, c


def ps(state, filename="Benchy.gcode", **kw):
    d = dict(state=state, filename=filename, print_duration=600, info={})
    d.update(kw)
    return d


async def start_job(server, filename="Benchy.gcode"):
    STATE["print_stats"] = ps("printing", filename)
    STATE["virtual_sdcard"] = {"file_position": 1000, "progress": 0}
    await server.handlers["server:klippy_ready"]()
    await server.handlers["job_state:state_changed"](
        JobEvent.STARTED, {}, STATE["print_stats"])


def reset_world():
    STATE.clear()
    STATE.update(print_stats={}, virtual_sdcard={}, print_task_config={},
                 webhooks={"state": "ready"})
    META.clear()
    META.update(gcode_start_byte=1000, gcode_end_byte=11000,
                estimated_time=3600)
    SPOOLS.clear()


# ---- component tests -------------------------------------------------------

async def test_order_and_priority():
    print("Send order and priority")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({}, {r: [("ntfy", ntfy, False)] for r in
                          ("paused", "resumed", "progress_25", "error")})
    await start_job(server)
    # paused waits for its snapshot; resumed happens right after
    await server.handlers["job_state:state_changed"](
        JobEvent.PAUSED, {}, ps("paused"))
    await server.handlers["job_state:state_changed"](
        JobEvent.RESUMED, {}, ps("printing"))
    await asyncio.sleep(0.6)
    titles = [e["title"] for e in log]
    check("paused arrives before resumed",
          titles[:2] == ["⏸️ U1 paused", "▶️ U1 resumed"], titles)

    # A status notification that is retrying doesn't hold up a fault
    log.clear()
    ntfy.fail = 2
    c._spawn(c._send("progress_25", "⏳ U1 25%", "Benchy"))
    await asyncio.sleep(0.05)
    c._spawn(c._send("error", "⚠️ U1 Error: fault", "Benchy\ncode"))
    await asyncio.sleep(1.0)
    sent = [e["title"] for e in log if e["ok"]]
    check("fault goes ahead of a retrying status notification",
          sent[:2] == ["⚠️ U1 Error: fault", "⏳ U1 25%"], sent)


async def test_expiry_and_independence():
    print("Expiry, retries, independent services")
    reset_world()
    pn.STATUS_EXPIRY = 0.3
    log = []
    ntfy = FakeService(log, "ntfy", fail=99)
    mail = FakeService(log, "email")
    server, c = make({}, {"progress_25": [("ntfy", ntfy, False)],
                          "complete": [("ntfy", ntfy, False),
                                       ("email", mail, True)]})
    await start_job(server)
    t0 = time.monotonic()
    c._spawn(c._send("progress_25", "⏳ U1 25%", "Benchy"))
    c._spawn(c._send("complete", "✅ U1 complete", "Benchy"))
    await asyncio.sleep(0.15)
    mails = [e for e in log if e["channel"] == "email"]
    check("email isn't held up by a failing push service",
          len(mails) == 1 and mails[0]["t"] - t0 < 0.15)
    await asyncio.sleep(1.5)
    tries = [e for e in log if e["channel"] == "ntfy"
             and e["title"].endswith("25%")]
    check("an expired status notification stops retrying",
          1 <= len(tries) < 4, f"{len(tries)} attempts")
    done = [e for e in log if e["channel"] == "ntfy"
            and e["title"].endswith("complete")]
    check("complete keeps retrying (never expires)", len(done) == 4,
          f"{len(done)} attempts")
    check("last attempt is sent without the image",
          done and done[-1]["attach"] is None and done[0]["attach"])
    pn.STATUS_EXPIRY = 300.


async def test_fault_dedup():
    print("Fault de-duplication")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({}, {"error": [("ntfy", ntfy, False)],
                          "paused": [("ntfy", ntfy, False)]})
    await start_job(server)
    ex = {"level": 2, "id": 532, "index": 0, "code": 2,
          "message": "detected noodle"}
    STATE["print_stats"] = ps("paused", exception=ex)
    await server.handlers["job_state:state_changed"](
        JobEvent.PAUSED, {}, STATE["print_stats"])
    await server.handlers["snapmaker:exception_notification"](ex)
    await asyncio.sleep(0.6)
    errors = [e for e in log if "Error" in e["title"]]
    check("a fault that paused the print is reported once",
          len(errors) == 1, [e["title"] for e in log])
    check("the pause is reported as the error, not as a plain pause",
          errors and errors[0]["title"] == "⚠️ U1 Error: paused")


async def test_same_preparation_everywhere():
    print("Telegram and Discord preparation for every notification")
    reset_world()
    log = []
    tg = FakeService(log, "telegram")
    dc = FakeService(log, "discord")
    server, c = make({"web_ui_url": "http://u1/"},
                     {r: [("telegram", tg, False), ("discord", dc, False)]
                      for r in ("complete", "filament_low")})
    await start_job(server, "A & <B>.gcode")
    await c._send("complete", "✅ U1 complete", "A & <B>\nPrint time: 1m")
    item = dict(ch=0, sid=7, name="Tom & <Jerry> PLA", rem=40., full=1000.,
                colour=(200, 0, 0), need=0., backups=[], refill_on=False,
                exact=True, status="orange", together="green",
                unknown_backup=False, total=40.)
    await c._low_alert([item])
    tgs = [e for e in log if e["channel"] == "telegram"]
    dcs = [e for e in log if e["channel"] == "discord"]
    check("Telegram text is escaped (status notification)",
          "A &amp; &lt;B&gt;" in tgs[0]["body"], tgs[0]["body"])
    check("Telegram text is escaped (spool notification)",
          len(tgs) == 2 and "Tom &amp; &lt;Jerry&gt;" in tgs[1]["body"],
          tgs[-1]["body"])
    check("Telegram spool notification has the links",
          len(tgs) == 2 and '<a href="http://u1/">Web UI</a>' in tgs[1]["body"])
    check("Discord spool notification has the links and escaping",
          len(dcs) == 2 and "[Web UI](http://u1/)" in dcs[1]["body"]
          and "\\<Jerry\\>" not in dcs[1]["body"]
          and "Tom & <Jerry>" in dcs[1]["body"].replace("\\", ""))


async def test_email_threads():
    print("Email: HTML card and threads")
    reset_world()
    sent = []

    class SMTP:
        def __init__(self, *_a, **_k):
            pass

        def __getattr__(self, name):
            def fn(*a, **_k):
                if name == "sendmail":
                    sent.append(a[2])
                    return {}
                return (250, b"ok")
            return fn
    smtplib.SMTP = SMTP
    smtplib.SMTP_SSL = SMTP
    import apprise
    url = ("mailtos://u:p@smtp.example.com:587?from=a@example.com&"
           "to=b@example.com&name=U1&format=html&inline=yes&mode=starttls")
    ap = apprise.Apprise()
    ap.add(url)
    server, c = make({"web_ui_url": "http://u1/"},
                     {r: [("email", ap, True)] for r in ("started",
                                                         "complete")})
    await start_job(server)
    await asyncio.sleep(0.3)
    await c._send("complete", "✅ U1 complete", "Benchy\nPrint time: 10m")
    msgs = [email.message_from_string(m) for m in sent]
    check("two emails sent", len(msgs) == 2, len(msgs))
    if len(msgs) == 2:
        refs = [m["References"] for m in msgs]
        check("both in one thread (same References)",
              refs[0] and refs[0] == refs[1], refs)
        parts = [p.get_content_type() for p in msgs[1].walk()]
        check("HTML with a plain-text version and the image inline",
              "text/html" in parts and "text/plain" in parts
              and any(p.get("Content-ID") for p in msgs[1].walk()), parts)


async def test_filament_measured():
    print("Filament still needed is measured from Spoolman")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({"spoolman_url": "http://spoolman:7912",
                      "filament_low_percent": "0", "filament_low_grams": "0"},
                     {"filament_short": [("ntfy", ntfy, False)]})

    def spool(sid, rem, name="PLA"):
        SPOOLS[sid] = {"id": sid, "remaining_weight": rem,
                       "initial_weight": 1000,
                       "filament": {"name": name, "vendor": {"name": "X"},
                                    "color_hex": "FF0000", "weight": 1000}}
    # T0 on toolhead 1 (spool 10) 50 g; T1 on toolhead 2 (spool 20) 100 g,
    # used only late in the print
    META["filament_weight"] = [50.0, 100.0]
    STATE["print_task_config"] = {
        "extruder_map_table": [0, 1], "filament_spool_id": [10, 20],
        "extruders_used": [True, True], "auto_replenish_filament": False,
        "replenish_ignore_color": False, "filament_exist": [True, True],
        "filament_vendor": ["X", "X"], "filament_type": ["PLA", "PLA"],
        "filament_sub_type": ["", ""], "filament_color_rgba": ["FF0000FF"] * 2}
    spool(10, 400.0)
    spool(20, 160.0)
    await start_job(server)
    await asyncio.sleep(0.3)
    check("no alert at start when every spool has enough",
          not [e for e in log if e["ok"]], [e["title"] for e in log])
    # Half way through the file: T0 used 25 g, T1 nothing yet
    STATE["virtual_sdcard"]["file_position"] = 6000
    c._status["virtual_sdcard"]["file_position"] = 6000
    spool(10, 375.0)
    await c._poll_spools("Benchy.gcode", {0: dict(sid=10, reported=0),
                                          1: dict(sid=20, reported=0)})
    check("a filament used late still needs all of it at 50%",
          c._need_now(1) == 100.0, c._need_now(1))
    check("a filament used evenly needs what's left",
          c._need_now(0) == 25.0, c._need_now(0))
    # Untracked usage: nothing used well into the print
    c._spool_now = dict(c._spool_start)
    check("no usage tracked: the mid-print check is skipped",
          c._need_now(1) is None)


async def test_faults_over_a_job():
    print("Faults across pause, resume and cancel")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({}, {r: [("ntfy", ntfy, False)] for r in
                          ("error", "paused", "resumed", "cancelled",
                           "shutdown")})
    await start_job(server)
    ex = {"level": 2, "id": 532, "index": 0, "code": 2,
          "message": "filament runout"}
    events = server.handlers["job_state:state_changed"]
    STATE["print_stats"] = ps("paused", exception=ex)
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    await server.handlers["snapmaker:exception_notification"](ex)
    # Resumed: Klipper keeps print_stats.exception
    STATE["print_stats"] = ps("printing", exception=ex)
    await events(JobEvent.RESUMED, {}, STATE["print_stats"])
    # A plain pause an hour later must not be the old fault again
    STATE["print_stats"] = ps("paused", exception=ex)
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    STATE["print_stats"] = ps("printing", exception=ex)
    await events(JobEvent.RESUMED, {}, STATE["print_stats"])
    # ...nor a cancel from the print hook
    await server.remote["push_notification"](event="cancelled")
    await asyncio.sleep(0.6)
    titles = [e["title"].split(" ", 1)[1] for e in log]
    check("a resumed fault doesn't colour a later pause or cancel",
          titles == ["U1 Error: paused", "U1 resumed", "U1 paused",
                     "U1 resumed", "U1 cancelled"], titles)

    # The same fault again is a new fault
    log.clear()
    await start_job(server)
    STATE["print_stats"] = ps("paused", exception=ex)
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    STATE["print_stats"] = ps("printing", exception=ex)
    await events(JobEvent.RESUMED, {}, STATE["print_stats"])
    await asyncio.sleep(0.6)
    c._faults_sent.clear()
    await server.handlers["snapmaker:exception_notification"](ex)
    STATE["print_stats"] = ps("paused", exception=ex)
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    await asyncio.sleep(0.6)
    titles = [e["title"].split(" ", 1)[1] for e in log]
    check("the fault happening again is reported again",
          titles[-1] == "U1 Error: paused", titles)

    # Fault-pause, then the user cancels: one error, then a cancel
    # naming the fault
    log.clear()
    await start_job(server)
    STATE["print_stats"] = ps("paused", exception=ex)
    c._faults_sent.clear()
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    await server.remote["push_notification"](event="cancelled")
    await asyncio.sleep(0.6)
    titles = [e["title"].split(" ", 1)[1] for e in log]
    check("cancel after a reported fault-pause: no second error",
          titles == ["U1 Error: paused", "U1 cancelled"]
          and "After:" in log[-1]["body"], (titles, log[-1]["body"]))

    # A small file: 1 % at start is the start G-code, not a recovery
    log.clear()
    STATE["virtual_sdcard"] = {"file_position": 1200, "progress": 0.02}
    STATE["print_stats"] = ps("printing", print_duration=0)
    await server.handlers["server:klippy_ready"]()
    await events(JobEvent.STARTED, {}, STATE["print_stats"])
    await asyncio.sleep(0.3)
    check("a fresh job 2 % into a small file is 'started'",
          c._thread_recovered is False if hasattr(c, "_thread_recovered")
          else not [e for e in log if "power loss" in e["title"]])


async def test_spool_switch():
    print("Refill from a backup spool (happens while paused)")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({"spoolman_url": "http://spoolman:7912",
                      "filament_low_percent": "0", "filament_low_grams": "0"},
                     {"spool_switch": [("ntfy", ntfy, False)]})
    for sid in (10, 20):
        SPOOLS[sid] = {"id": sid, "remaining_weight": 500.0,
                       "initial_weight": 1000,
                       "filament": {"name": "PLA", "vendor": {"name": "X"},
                                    "color_hex": "FF0000", "weight": 1000}}
    META["filament_weight"] = [50.0]
    STATE["print_task_config"] = {
        "extruder_map_table": [0, 0], "filament_spool_id": [10, 20],
        "extruders_used": [True, False], "auto_replenish_filament": True,
        "replenish_ignore_color": False, "filament_exist": [True, True],
        "filament_vendor": ["X", "X"], "filament_type": ["PLA", "PLA"],
        "filament_sub_type": ["", ""], "filament_color_rgba": ["FF0000FF"] * 2}
    await start_job(server)
    await asyncio.sleep(0.3)
    # Runout: the printer pauses, moves T0 to toolhead 2, resumes
    STATE["print_stats"]["state"] = "paused"
    c._status["print_stats"]["state"] = "paused"
    # SpoolLink clears the emptied toolhead's spool before the refill
    await c._on_status_update(
        {"print_task_config": {"filament_spool_id": [0, 20]}}, 0)
    await c._on_status_update(
        {"print_task_config": {"extruder_map_table": [1, 0]}}, 0)
    await asyncio.sleep(0.6)
    check("a refill while paused is reported",
          [e["title"] for e in log] == ["🔄 U1 switched to backup spool"],
          [e["title"] for e in log])
    check("...naming the emptied spool even after it was unassigned",
          log and "spool #10" in log[0]["body"] and "#0)" not in log[0]["body"],
          log and log[0]["body"])
    STATE["print_task_config"]["filament_spool_id"] = [10, 20]
    STATE["print_task_config"]["filament_vendor"] = ["X", "Y"]
    c._job_map = [0, 0]
    log.clear()
    await c._on_status_update(
        {"print_task_config": {"extruder_map_table": [1, 0],
                               "filament_vendor": ["X", "Y"]}}, 0)
    await asyncio.sleep(0.4)
    check("another vendor isn't a refill the printer would do",
          not log, [e["title"] for e in log])

    # A runout with a backup spool: only "switched to backup spool", no
    # "Error: paused" (the printer refills and resumes by itself)
    pn.REFILL_WAIT = 1.5
    for refill in (True, False):
        reset_world()
        log = []
        ntfy = FakeService(log, "ntfy")
        server, c = make({"spoolman_url": "http://spoolman:7912",
                          "filament_low_percent": "0",
                          "filament_low_grams": "0"},
                         {"spool_switch": [("ntfy", ntfy, False)],
                          "error": [("ntfy", ntfy, False)],
                          "resumed": [("ntfy", ntfy, False)]})
        for sid in (10, 20):
            SPOOLS[sid] = {"id": sid, "remaining_weight": 500.0,
                           "initial_weight": 1000,
                           "filament": {"name": "PLA", "vendor": {"name": "X"},
                                        "color_hex": "FF0000", "weight": 1000}}
        META["filament_weight"] = [50.0]
        STATE["print_task_config"] = {
            "extruder_map_table": [0, 0], "filament_spool_id": [10, 20],
            "extruders_used": [True, False], "auto_replenish_filament": True,
            "replenish_ignore_color": False, "filament_exist": [True, True],
            "filament_vendor": ["X", "X"], "filament_type": ["PLA", "PLA"],
            "filament_sub_type": ["", ""],
            "filament_color_rgba": ["FF0000FF"] * 2}
        await start_job(server)
        await asyncio.sleep(0.3)
        ex = {"id": 523, "index": 0, "code": 0,
              "message": "e0_filament runout", "level": 2}
        STATE["print_stats"] = ps("paused", exception=ex)
        c._status["print_stats"]["state"] = "paused"
        await server.handlers["job_state:state_changed"](
            JobEvent.PAUSED, {}, STATE["print_stats"])
        await asyncio.sleep(0.6)
        if refill:
            await c._on_status_update(
                {"print_task_config": {"extruder_map_table": [1, 0]}}, 0)
        await asyncio.sleep(2.2)
        titles = [e["title"].split(" ", 2)[2] for e in log]
        if refill:
            check("runout refilled from a backup: no error, just the switch",
                  titles == ["switched to backup spool"], titles)
        else:
            check("runout with no refill: the error after the wait",
                  titles == ["Error: paused"], titles)
    pn.REFILL_WAIT = 25.
    tangle = {"id": 523, "index": 0, "code": 38,
              "message": "detect filament tangled!", "level": 2}
    check("a tangle (same module id) is not held as a runout",
          not c._refill_expected(tangle))


async def test_time_left():
    print("Time left: slicer estimate early, measured later")
    reset_world()
    server, c = make({}, {"first_layer": [("ntfy", FakeService([], "ntfy"),
                                           False)]})
    await start_job(server)          # estimated_time 3600, print_duration 600

    async def at(pos, pd=600):
        await c._on_status_update({"virtual_sdcard": {"file_position": pos},
                                   "print_stats": {"print_duration": pd}}, 0)
        return c._time_left()
    left = await at(1000 + 200)                               # 2 % of the file
    check("early in the job: slicer estimate minus time printed",
          left.startswith("~50m left"), left)
    left = await at(1000 + 5000)                              # 50 %
    check("later: print time / progress", left.startswith("~10m left"), left)
    left = await at(1000 + 200, pd=4000)   # early, but slower than the slicer said
    check("early but already past the estimate: never 0 left",
          left.startswith("~29m left"), left)


async def test_calibration():
    print("Manual calibrations: complete, and user intervention needed")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({}, {"calibration": [("ntfy", ntfy, False)]})
    STATE["print_stats"] = ps("standby")
    await server.handlers["server:klippy_ready"]()
    nc_hold = sys.modules[type(c).__module__]
    nc_hold.INTERVENTION_HOLD = 0.2

    async def msm(state, action=0):
        await c._on_status_update(
            {"machine_state_manager": {"main_state": state,
                                       "action_code": action}}, 0)
    titles = lambda: [e["title"] for e in log]
    await msm(3, 257)              # bed levelling from the touchscreen
    await msm(3, 258)
    await msm(3, 256)
    await msm(0)                   # back to idle
    await asyncio.sleep(0.4)
    check("a touchscreen calibration ending in idle is reported, by name",
          titles() == ["📐 U1 Bed levelling complete"], titles())
    log.clear()
    await msm(2, 1)                # XYZ offset: homing
    await msm(2, 200)              # auto clean
    await msm(2, 192)              # brush toolhead 1
    await msm(2, 201)              # cooling
    await msm(2, 0)                # the screen waits for a tap
    await asyncio.sleep(0.4)
    await msm(2, 196)              # probing
    await asyncio.sleep(0.1)
    await msm(2, 0)                # a short gap between machine steps
    await msm(2, 197)
    await asyncio.sleep(0.4)
    check("manual clean and a held idle are user intervention; short gaps "
          "aren't", titles() == ["🖐️ U1 user intervention needed"] * 2
          and "Brush the nozzle of Toolhead 1" in log[0]["body"]
          and "waiting for you" in log[1]["body"], (titles(), [e["body"] for e in log]))
    log.clear()
    await msm(5, 384)              # queued: straight into input shaper
    await msm(0)
    await asyncio.sleep(0.4)
    check("a queued calibration completes the previous one",
          titles() == ["📐 U1 XYZ offset calibration complete",
                       "📐 U1 Input shaper calibration complete"]
          and "Input shaper calibration starts next" in log[0]["body"],
          titles())
    log.clear()
    await msm(5, 384)
    await msm(9)                   # ends somewhere other than idle
    await asyncio.sleep(0.4)
    check("one that doesn't end in idle isn't reported", log == [])
    await msm(0)
    await msm(4, 320)
    await msm(7)                   # ABNORMAL
    await asyncio.sleep(0.4)
    check("one that ends abnormal is reported as failed",
          titles() == ["📐 U1 Flow calibration failed"], titles())
    log.clear()
    await msm(0)
    await start_job(server)        # the start routine's bed mesh
    await msm(3, 257)
    await msm(3, 0)
    await asyncio.sleep(0.4)
    await msm(1)                   # back to printing
    await asyncio.sleep(0.3)
    check("nothing during a print job", log == [], titles())


async def test_gotify():
    print("Gotify: Markdown text, links, never an image")
    reset_world()
    log = []
    gotify, ntfy = FakeService(log, "gotify"), FakeService(log, "ntfy")
    server, c = make({"web_ui_url": "http://u1/"},
                     {"paused": [("gotify", gotify, False),
                                 ("ntfy", ntfy, False)]})
    await start_job(server, "my_part_v2.gcode")
    await server.handlers["job_state:state_changed"](
        JobEvent.PAUSED, {}, ps("paused", "my_part_v2.gcode"))
    await asyncio.sleep(3.6)
    g = [e for e in log if e["channel"] == "gotify"]
    n = [e for e in log if e["channel"] == "ntfy"]
    check("Gotify gets no attachment while ntfy does",
          g and g[0]["attach"] is None and n and n[0]["attach"], log)
    check("Gotify text is escaped Markdown with the links",
          g and "my\\_part\\_v2" in g[0]["body"]
          and "[Web UI](http://u1/)" in g[0]["body"],
          g and g[0]["body"])


async def test_image_names():
    print("Picture file names: time, printer, notification, file")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({"name": "U1-martha"},
                     {"progress_50": [("ntfy", ntfy, False)]})
    await start_job(server, "sub dir/My Part (v2).gcode")
    await c._send("progress_50", "⏳ U1-martha 50%", "x",
                  filename="sub dir/My Part (v2).gcode")
    name = os.path.basename(log[0]["attach"]) if log else ""
    check("named like 2026-10-22_17-05-03_U1-martha_50pct_sub-dir-My-Part-v2.jpg",
          re.fullmatch(r"\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_U1-martha_50pct_"
                       r"sub-dir-My-Part-v2\.jpg", name) is not None, name)
    check("the named copy is cleaned up after sending",
          log and not os.path.exists(log[0]["attach"]))


async def test_fault_emoji():
    print("Fault emoji before the error code")
    cases = [((532, 0, 2), "🍝 "), ((523, 1, 2), "🌡️ "), ((523, 3, 42), "🔧 "),
             ((525, 2, 32), "🧵 "), ((530, 0, 4), "📐 "), ((531, 0, 9), "💾 "),
             ((531, 0, 7), "☁️ "), ((523, 0, 6), ""), ((523, 0, 38), ""),
             ((532, 0, 1), "")]
    got = [(pn.describe(dict(level=2, id=m, index=i, code=c, message="x"))
            .split("\n")[0]) for (m, i, c), _ in cases]
    check("emoji by code, none for the rest",
          all(g.startswith(e + "000") if e else g.startswith("000")
              for g, (_, e) in zip(got, cases)), got)


async def test_driver_hot():
    print("Driver temperature: once per print job, again after a pause")
    reset_world()
    log = []
    ntfy = FakeService(log, "ntfy")
    server, c = make({"driver_temp": "105"},
                     {"driver_hot": [("ntfy", ntfy, False)],
                      "paused": [("ntfy", ntfy, False)],
                      "resumed": [("ntfy", ntfy, False)]})
    await start_job(server)
    events = server.handlers["job_state:state_changed"]

    async def temps(x, y):
        await c._on_status_update(
            {"tmc2240 stepper_x": {"temperature": x},
             "tmc2240 stepper_y": {"temperature": y}}, 0)
    await temps(98, 101)
    await temps(104, 106)
    await temps(107, 108)
    await temps(104, 104)
    await temps(106, 103)
    await asyncio.sleep(0.4)
    hot = [e for e in log if "driver" in e["title"]]
    check("one alert per print job, when either driver reaches the limit",
          len(hot) == 1 and "Y driver: 106 °C (alert at 105 °C)" in hot[0]["body"]
          and "X driver: 104 °C\n" in hot[0]["body"], [e["body"] for e in hot])
    STATE["print_stats"] = ps("paused")
    await events(JobEvent.PAUSED, {}, STATE["print_stats"])
    STATE["print_stats"] = ps("printing")
    await events(JobEvent.RESUMED, {}, STATE["print_stats"])
    await temps(109, 100)
    await asyncio.sleep(0.4)
    hot = [e for e in log if "driver" in e["title"]]
    check("after a pause it can warn again", len(hot) == 2
          and "X driver: 109 °C (alert" in hot[1]["body"], len(hot))


async def test_print_by_object():
    print("Print by object: layer and object notifications")
    reset_world()
    objects = [f"Assembly_id_{i}_copy_0" for i in range(3)]
    STATE["exclude_object"] = {"objects": [{"name": o} for o in objects],
                               "current_object": None,
                               "excluded_objects": []}
    # Layers run on across the objects, as Orca writes them: 81 each
    tail = "; CONFIG_BLOCK_START\n; print_sequence = by object\n; x = 1\n"
    with open(os.path.join(GCODES, "Assembly.gcode"), "w") as f:
        f.write("; gcode\nG28\n" * 2000 + tail)
    with open(os.path.join(GCODES, "Benchy.gcode"), "w") as f:
        f.write("; gcode\n" + tail.replace("by object", "by layer"))

    async def run(filename, mode):
        log = []
        ntfy = FakeService(log, "ntfy")
        server, c = make({"first_layer_objects": mode},
                         {"first_layer": [("ntfy", ntfy, False)],
                          "layer_started": [("ntfy", ntfy, False)],
                          "object_complete": [("ntfy", ntfy, False)]})
        await start_job(server, filename)
        for layer in range(1, 244):
            # The layer line comes just before the object marker, and the
            # two may arrive in one update or separately
            obj = objects[(layer - 1) // 81]
            STATE["print_stats"]["info"] = {"current_layer": layer}
            await c._on_status_update(
                {"virtual_sdcard": {"file_position": 1000 + 41 * layer}}, 0)
            if layer % 2:
                await c._on_status_update(
                    {"print_stats": {"info": {"current_layer": layer}}}, 0)
                await c._on_status_update(
                    {"exclude_object": {"current_object": obj}}, 0)
            else:
                await c._on_status_update(
                    {"print_stats": {"info": {"current_layer": layer}},
                     "exclude_object": {"current_object": obj}}, 0)
            await c._on_status_update(
                {"exclude_object": {"current_object": None}}, 0)
        await asyncio.sleep(0.3)
        return c, [(e["title"].split(" ", 2)[2], e["body"]) for e in log]

    c, sent = await run("Assembly.gcode", "each")
    check("by-object print job is recognised", c._by_object)
    check("first layer started: once, when layer 1 begins",
          [t for t, _ in sent if t == "first layer started"]
          == ["first layer started"] and sent[0][0] == "first layer started")
    layers = [b for t, b in sent if t.startswith("2nd layer")]
    check("layer complete once per object, from the object's own layers",
          len(layers) == 3 and "Object 2 of 3: Assembly #2" in layers[1],
          layers)
    done = [b for t, b in sent if t == "object complete"]
    check("object complete for each finished object, not the last",
          len(done) == 2 and "Object 1 of 3: Assembly #1" in done[0]
          and "Object 2 of 3" in done[1], done)
    check("object complete says how long is left",
          done and "left" in done[0], done[:1])
    c, sent = await run("Assembly.gcode", "once")
    check("or once per print job",
          [t for t, _ in sent if t.startswith("2nd")] == ["2nd layer complete"]
          and len([t for t, _ in sent if t == "object complete"]) == 2)
    c, sent = await run("Benchy.gcode", "each")
    check("a by-layer print job ignores the object markers",
          not c._by_object and [t for t, _ in sent]
          == ["first layer started", "2nd layer complete"], sent)


async def test_no_secrets_in_logs():
    print("No tokens in the log")
    import logging
    import requests
    import moonraker.components.push_notifications_discord as dc
    token = "SECRET_webhook_token_0123456789abcdef"

    class Capture(logging.Handler):
        lines = []

        def emit(self, record):
            Capture.lines.append(record.getMessage())
    h = Capture()
    logging.getLogger("apprise").addHandler(h)
    real_post, real_get = requests.post, requests.get

    def boom(url, **_kw):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='discord.com'): Max retries exceeded "
            f"with url: {url}")
    requests.post = requests.get = boom
    try:
        dc.send_webhook("text", "title", "info", None,
                        {"host": "1234567890", "fullpath": "/" + token,
                         "qsd": {}})
        dc.send_dm("text", "title", "info", None,
                   {"user": token, "host": "555", "qsd": {}})
        _, why = dc.bot_info(token)
    finally:
        requests.post, requests.get = real_post, real_get
        logging.getLogger("apprise").removeHandler(h)
    logged = "\n".join(Capture.lines) + "\n" + why
    check("a failed Discord request is logged", "connection failed" in logged,
          logged)
    check("without the webhook or bot token", token not in logged, logged)


async def test_config_lines():
    print("Config lines the component reads")
    reset_world()
    c = pn.PushNotifications(Config(Server(), dict(
        name="U1", complete="snapshot, ntfys://ntfy.example.com/u1",
        complete_email="snapshot, mailtos://u:p@smtp.example.com:587?"
                       "from=a@example.com&to=b@example.com",
        complete_webhook="snapshot, jsons://hooks.example.com/x?:event=complete")))
    channels = [d.channel for d in c.rows["complete"][1]]
    check("push, email and webhook for one notification",
          channels == ["ntfy", "email", "webhook"], channels)
    SECRETS.update(push_token="tk_real", webhook="jsons://u:p@h.example.com/x")
    c = pn.PushNotifications(Config(Server(), dict(
        name="U1",
        complete="snapshot, ntfys://ntfy.example.com/u1?priority=default"
                 "&token={secrets.notifications.push_token}",
        complete_webhook="snapshot, {secrets.notifications.webhook}"
                         "?:event=complete")))
    urls = [str(list(d.ap)[0].url(privacy=False))
            for d in c.rows["complete"][1]]
    check("secrets templates are rendered from Moonraker's secrets file",
          "tk_real" in urls[0] and "h.example.com" in urls[1], urls)
    SECRETS.clear()


async def run_component_tests():
    for t in (test_order_and_priority, test_expiry_and_independence,
              test_fault_dedup, test_same_preparation_everywhere,
              test_email_threads, test_filament_measured,
              test_faults_over_a_job, test_spool_switch, test_time_left, test_calibration, test_gotify, test_image_names, test_fault_emoji, test_driver_hot,
              test_print_by_object,
              test_no_secrets_in_logs, test_config_lines):
        try:
            await t()
        except Exception as e:  # report and carry on
            import traceback
            traceback.print_exc()
            check(t.__name__, False, repr(e))
        # let the component's background tasks finish
        await asyncio.sleep(0.2)


# ---- notifications-config.py -----------------------------------------------

def run_config_tests():
    print("notifications-config.py")
    cfgdir = os.path.join(WORK, "cfg")
    os.makedirs(cfgdir, exist_ok=True)
    with open(os.path.join(cfgdir, "extended2.cfg"), "w") as f:
        f.write("[spoolman]\nhost: http://spoolman:7912\n")
    os.environ["NOTIFICATIONS_ROOT"] = cfgdir
    os.environ["NOTIFICATIONS_SECRETS"] = os.path.join(cfgdir, "moonraker.secrets")
    os.environ["NOTIFICATIONS_COMPONENTS"] = COMPONENTS
    spec = importlib.util.spec_from_file_location("nc", CONFIG_TOOL)
    nc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nc)
    nc.camera_status = lambda: {"internal": True, "usb": False}

    def bad(data, what):
        try:
            nc.validate(dict(enabled=True, **data), nc.load())
            check(what, False, "accepted")
        except nc.ConfigError:
            check(what, True)
    bad(dict(provider="ntfy", server="ftp://x", topic="u1"),
        "rejects a server that isn't http(s)")
    bad(dict(provider="ntfy", server="https://ntfy.sh", topic="short"),
        "rejects a short topic on the public server")
    bad(dict(provider="telegram", secret="nope"), "rejects a bad bot token")
    bad(dict(provider="gotify", gotify_server="ftp://x", secret="A" * 15),
        "Gotify: rejects a server that isn't http(s)")
    bad(dict(provider="gotify", gotify_server="https://g.example.com:99999",
             secret="A" * 15), "Gotify: rejects a port out of range")
    bad(dict(provider="gotify", gotify_server="https://g.example.com",
             secret="no"), "Gotify: rejects a bad app token")
    g = nc.validate(dict(enabled=True, provider="gotify",
                         gotify_server="https://g.example.com:8443/gotify/",
                         secret="AbC.dEf-123_xyz", printer_url="http://u1"),
                    nc.load())
    gurl = nc.row_url(g, "error", "max", True)
    check("Gotify: URL with port, path, priority and Markdown",
          gurl == "gotifys://g.example.com:8443/gotify/AbC.dEf-123_xyz"
                  "?priority=emergency&format=markdown", gurl)
    gmoon = nc.render_moonraker(g, nc.SecretBag())
    check("Gotify: no pictures, links in the text, token as a secret",
          "error: none, gotifys://g.example.com:8443/gotify/"
          "{secrets.notifications.push_token}" in gmoon
          and "web_ui_url: http://u1/" in gmoon
          and "AbC.dEf-123_xyz" not in gmoon, gmoon)
    bad(dict(provider="telegram", secret="12345:" + "A" * 35,
             telegram_chat="123", printer_url="http://u1"),
        "rejects a dot-less printer host for Telegram (links are dropped)")
    nc.validate(dict(provider="telegram", secret="12345:" + "A" * 35,
                     telegram_chat="123", printer_url="http://100.64.0.10"),
                nc.load())
    nc.validate(dict(provider="ntfy", server="https://ntfy.example.com",
                     topic="u1", printer_url="http://u1"), nc.load())
    check("a dot-less printer host is fine for the other services", True)
    nc._TAILSCALE = ("u1", "100.64.0.10")
    try:
        tg = dict(enabled=True, provider="telegram", secret="12345:" + "A" * 35,
                  telegram_chat="123", printer_url="http://U1/")
        s = nc.validate(tg, nc.load())
        check("Telegram: the Tailscale name is accepted and sent as the IP",
              nc.links(s, "test", True)[:2] == [
                  ("Web UI", "http://100.64.0.10/"),
                  ("Live Camera", "http://100.64.0.10/webcam/webrtc")]
              and "web_ui_url: http://100.64.0.10/" in nc.render_moonraker(s)
              and s["printer_url"] == "http://U1/",
              nc.links(s, "test", True))
        bad(dict(provider="telegram", secret="12345:" + "A" * 35,
                 telegram_chat="123", printer_url="http://other"),
            "Telegram: another dot-less name is still refused")
        s = nc.validate(dict(enabled=True, provider="ntfy",
                             server="https://ntfy.example.com", topic="u1",
                             printer_url="http://u1"), nc.load())
        check("other services keep the Tailscale name",
              nc.links(s, "test", True)[0] == ("Web UI", "http://u1/"))
    finally:
        nc._TAILSCALE = None
    bad(dict(provider="discord", secret="https://example.com/x"),
        "rejects a URL that isn't a Discord webhook")

    s = nc.validate(dict(enabled=True, provider="ntfy",
                         server="https://ntfy.example.com", topic="u1",
                         printer_url="http://u1"), nc.load())
    moon = nc.render_moonraker(s)
    check("Moonraker config lists the enabled notifications",
          "started: render, ntfys://ntfy.example.com/u1" in moon)
    known = set(re.findall(r'config\.get(?:int|float|boolean)?\(\s*"(\w+)"',
                           open(os.path.join(COMPONENTS,
                                             "push_notifications.py")).read()))
    keys = {ln.split(":")[0] for ln in moon.splitlines()
            if ln and not ln.startswith(("#", "[")) and ":" in ln}
    rows = {k for k in keys if k.split("_email")[0].split("_webhook")[0]
            in pn.ROW_IDS}
    check("every generated option is one the component reads "
          "(Moonraker warns about the rest)", keys - rows <= known,
          sorted(keys - rows - known))
    check("print-by-object: object complete follows the Print complete row",
          "object_complete: snapshot, ntfys://" in moon
          and "first_layer_objects: each" in moon
          and "complete_objects" not in moon)
    s["complete_objects"] = "once"
    check("or is left out when complete is once per print job",
          "object_complete" not in nc.render_moonraker(s))
    s["complete_objects"] = "each"
    bad(dict(provider="ntfy", server="https://ntfy.example.com", topic="u1",
             first_layer_objects="sometimes"),
        "rejects a bad print-by-object setting")
    check("ntfy buttons from the printer URL",
          "view%2C%20Web%20UI%2C%20http%3A%2F%2Fu1%2F" in moon)
    klip = nc.render_klipper(s)
    check("print-end and cancel hooks are generated",
          "[gcode_macro _PRINT_END_NTFY]" in klip
          and "[gcode_macro _CANCEL_PRINT_NTFY]" in klip)
    check("lift only when homed, capped below the stock moves",
          '"z" in th.homed_axes' in klip and "[15, 265 - z]|min" in klip)
    # As Klipper reads it: "#" to the end of every line is stripped first
    # (configfile.py comment_r), then the gcode is a Jinja template with
    # "{" / "}" as the expression delimiters. A "{# ... #}" comment thus
    # becomes a bare "{" and halts the printer (it did, once).
    import re as _re
    import jinja2
    import configparser as cp
    env = jinja2.Environment("{%", "%}", "{", "}")
    parser = cp.RawConfigParser()
    parser.read_string("\n".join(_re.sub(r"[#;].*$", "", line).rstrip()
                                  for line in klip.splitlines()))
    try:
        for sec in parser.sections():
            env.from_string(parser.get(sec, "gcode"))
        check("every macro compiles with Klipper's Jinja delimiters", True)
    except jinja2.TemplateSyntaxError as e:
        check("every macro compiles with Klipper's Jinja delimiters", False,
              str(e))
    check("reports once lifted, with no hold over the part",
          "G4" not in klip and klip.index("M400") < klip.index(
              "[gcode_macro _NTFY_SEND]"))
    check("hooks only act during a print job",
          'printer.print_stats.state == "printing"' in klip
          and 'ps.state in ["printing", "paused"]' in klip)
    s["rows"]["complete"]["enabled"] = False
    s["rows"]["cancelled"]["enabled"] = False
    s["rows"]["error"]["enabled"] = False
    check("no hooks when nothing needs them",
          nc.render_klipper(s) is None)

    tok = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    t = nc.validate(dict(enabled=True, provider="telegram", secret=tok,
                         telegram_chat="555"), nc.load())
    url = nc.row_url(t, "progress_25", "low", True)
    check("Telegram: HTML, no Apprise icon, low priority is silent",
          url.startswith(f"tgram://{tok}/555?") and "format=html" in url
          and "image=no" in url and "silent=yes" in url, url)
    hook = ("https://discord.com/api/webhooks/1234567890123/"
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abc")
    d = nc.validate(dict(enabled=True, provider="discord", secret=hook,
                         discord_mention="everyone"), nc.load())
    w = nc.validate(dict(enabled=True, provider="ntfy",
                         server="https://ntfy.example.com", topic="u1",
                         webhook_enabled=True,
                         webhook_url="https://u:p@hooks.example.com/x/abc?k=1",
                         printer_url="http://u1"), nc.load())
    w["rows"]["progress_25"]["enabled"] = False
    wmoon = nc.render_moonraker(w)
    wurl = nc.webhook_url(w, "complete", True)
    check("webhook: JSON sender, own query kept, event, printer, links",
          wurl.startswith("jsons://u:p@hooks.example.com/x/abc?")
          and "-k=1" in wurl and ":event=complete" in wurl
          and ":printer=U1" in wurl and ":web_ui=http%3A%2F%2Fu1%2F" in wurl,
          wurl)
    check("webhook is added alongside the push service",
          "\ncomplete: snapshot, ntfys://" in wmoon
          and "\ncomplete_webhook: snapshot, {secrets.notifications.webhook}"
          in wmoon, wmoon)
    bag = nc.SecretBag()
    nc.render_moonraker(w, bag)
    check("the webhook address (it may hold a token) is a secret",
          bag.values["webhook"] == "jsons://u:p@hooks.example.com/x/abc?-k=1"
          and "hooks.example.com" not in wmoon, bag.values)
    check("no token, password or webhook address in the generated config",
          "tk_" not in wmoon and "abc" not in wmoon)
    check("webhook gets notifications that push and email don't",
          "progress_25_webhook:" in wmoon and "\nprogress_25:" not in wmoon)
    hook_only = nc.validate(dict(enabled=True, push_enabled=False,
                                 webhook_enabled=True,
                                 webhook_url="http://hooks.local/x"),
                            nc.load())
    check("a webhook alone is enough (print hooks generated)",
          nc.configured(hook_only) and nc.render_klipper(hook_only))
    bad(dict(push_enabled=False, webhook_enabled=True,
             webhook_url="ftp://example.com/x"),
        "rejects a webhook that isn't http(s)")
    bad(dict(push_enabled=False), "rejects nothing enabled at all")
    check("push off: no push lines, and the provider's fields aren't checked",
          "\nstarted:" not in nc.render_moonraker(nc.validate(
              dict(enabled=True, push_enabled=False, provider="ntfy",
                   server="", email_enabled=True, email_host="smtp.x.com",
                   email_from="a@x.com", email_to="b@x.com"), nc.load())))
    check("older settings: provider none means push off",
          not nc.load_from({"provider": "none", "enabled": "true"})
          ["push_enabled"])
    # Secrets: Moonraker's secrets file, not extended2.cfg
    tok = nc.validate(dict(enabled=True, provider="ntfy",
                           server="https://ntfy.example.com", topic="u1",
                           auth="token", secret="tk_secret123"), nc.load())
    bag = nc.SecretBag()
    tmoon = nc.render_moonraker(tok, bag)
    check("ntfy token: a template in the config, the value in the bag",
          "token={secrets.notifications.push_token}" in tmoon
          and bag.values["push_token"] == "tk_secret123", tmoon)
    nc.store(tok)
    nc.write_secrets(dict(bag.values, secret=tok["secret"],
                          secret_for=tok["secret_for"]))
    cfg_text = open(nc.EXTENDED_CFG).read()
    sec_text = open(nc.SECRETS_FILE).read()
    check("extended2.cfg holds no secret; the secrets file does, mode 0600",
          "tk_secret123" not in cfg_text and "tk_secret123" in sec_text
          and oct(os.stat(nc.SECRETS_FILE).st_mode & 0o777) == "0o600")
    back = nc.load()
    check("the secret is loaded back from the secrets file",
          back["secret"] == "tk_secret123"
          and back["secret_for"] == "ntfy|token|https://ntfy.example.com|",
          (back["secret"], back["secret_for"]))
    sw = nc.validate(dict(enabled=False, push_enabled=False,
                          provider="pushover"), back)
    check("switching services drops the stored secret (never replayed to "
          "another host)", sw["secret"] == "" and not sw["secret_for"])
    try:
        nc.validate(dict(provider="pushover", pushover_user="u" * 30), back)
        check("...and asks for the new service's own token", False)
    except nc.ConfigError as e:
        check("...and asks for the new service's own token",
              "another service or server" in str(e), str(e))
    try:
        nc.validate(dict(server="https://other.example.com"), back)
        check("so does another ntfy server", False)
    except nc.ConfigError as e:
        check("so does another ntfy server",
              "another service or server" in str(e), str(e))
    same = nc.validate(dict(topic="u2"), back)
    check("the same service keeps it", same["secret"] == "tk_secret123")
    nc.write_secrets({})
    # Save & Apply: what restarts, and what a running print job forbids
    import io as _io
    import contextlib
    calls = []
    nc.subprocess.call = lambda argv: calls.append(os.path.basename(argv[0])) or 0
    nc.enter_test_cgroup = lambda: None
    nc.apprise_check = lambda s: None
    nc.MOONRAKER_CFG = os.path.join(cfgdir, "moonraker", "notifications.cfg")
    nc.KLIPPER_CFG = os.path.join(cfgdir, "klipper", "notifications.cfg")
    nc.PENDING_RESTART = {k: os.path.join(cfgdir, f".restart-{k}")
                          for k in ("moonraker", "klipper")}
    base = dict(enabled=True, provider="ntfy", auth="none",
                server="https://ntfy.example.com", topic="u1")

    def save(data, busy=False):
        calls.clear()
        nc.read_stdin_json = lambda: data
        nc.printer_busy = lambda: busy
        out = _io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = nc.cmd_save()
        except nc.ConfigError as e:
            return "ERROR", str(e)
        return rc, out.getvalue()
    rc, out = save(base)
    check("first save restarts Klipper (hooks) then Moonraker",
          rc == 0 and calls == ["S60klipper", "S61moonraker"], (rc, calls, out))
    rc, out = save(dict(base, rows={"started": {"enabled": False}}),
                   busy=True)
    check("mid-print: a row change restarts Moonraker only, print carries on",
          rc == 0 and calls == ["S61moonraker"] and "carries on" in out,
          (rc, calls, out))
    rc, out = save(dict(base, rows={"complete": {"image": False},
                                    "paused": {"enabled": False}}),
                   busy=True)
    check("mid-print: a print-end macro change is refused, naming it",
          rc == "ERROR" and "Print complete (image)" in out
          and "Nothing was saved" in out, (rc, out))
    rc, out = save(dict(base, rows={"complete": {"priority": "high"}}),
                   busy=True)
    check("mid-print: a print-end row change that leaves the macros alone "
          "is fine", rc == 0 and calls == ["S61moonraker"], (rc, calls, out))
    nc.subprocess.call = lambda argv: 1
    rc, out = save(dict(base, name="U1b"))
    check("a failed restart is owed", rc != 0
          and os.path.exists(nc.PENDING_RESTART["moonraker"]), (rc, out))
    nc.subprocess.call = lambda argv: calls.append(os.path.basename(argv[0])) or 0
    rc, out = save(dict(base, name="U1b"))
    check("...and done on the next save even with nothing changed",
          rc == 0 and calls == ["S61moonraker"], (rc, calls, out))
    rc, out = save(dict(base, name="U1b"))
    check("nothing owed, nothing changed: no restart",
          rc == 0 and calls == [], (rc, calls, out))
    # A firmware update changed the macro template while the stored
    # settings stayed the same: mid-print, save and owe the Klipper restart
    with open(nc.KLIPPER_CFG, "a") as f:
        f.write("# old template\n")
    rc, out = save(dict(base, name="U1b"), busy=True)
    check("mid-print: out-of-date print-end macros refuse the save",
          rc == "ERROR" and "out of date" in out and "Nothing was saved" in out
          and "# old template" in open(nc.KLIPPER_CFG).read(), (rc, out))
    rc, out = save(dict(base, name="U1b"))
    check("...and the idle save rewrites them and restarts Klipper",
          rc == 0 and calls == ["S60klipper"]
          and "# old template" not in open(nc.KLIPPER_CFG).read(),
          (rc, calls, out))

    check("Discord: mention only on high/max",
          "ping=everyone" in nc.row_url(d, "error", "max", True)
          and "ping" not in nc.row_url(d, "complete", "default", True))


def main():
    print(f"Testing {os.path.relpath(COMPONENTS)}")
    asyncio.run(run_component_tests())
    run_config_tests()
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{_passes} passed, {_failures} failed")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
