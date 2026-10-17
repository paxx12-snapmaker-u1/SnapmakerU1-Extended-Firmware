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
import shutil
import smtplib
import sys
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


class Loop:
    def create_task(self, coro):
        return asyncio.get_event_loop().create_task(coro)

    async def run_in_thread(self, fn, *args):
        return fn(*args)


class Meta:
    def get(self, key, default=None):
        return dict(META)


class FileManager:
    def get_metadata_storage(self):
        return Meta()


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


async def run_component_tests():
    for t in (test_order_and_priority, test_expiry_and_independence,
              test_fault_dedup, test_same_preparation_everywhere,
              test_email_threads, test_filament_measured,
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
    bad(dict(provider="discord", secret="https://example.com/x"),
        "rejects a URL that isn't a Discord webhook")

    s = nc.validate(dict(enabled=True, provider="ntfy",
                         server="https://ntfy.example.com", topic="u1",
                         printer_url="http://u1"), nc.load())
    moon = nc.render_moonraker(s)
    check("Moonraker config lists the enabled notifications",
          "started: render, ntfys://ntfy.example.com/u1" in moon)
    check("ntfy buttons from the printer URL",
          "view%2C%20Web%20UI%2C%20http%3A%2F%2Fu1%2F" in moon)
    klip = nc.render_klipper(s)
    check("print-end and cancel hooks are generated",
          "[gcode_macro _PRINT_END_NTFY]" in klip
          and "[gcode_macro _CANCEL_PRINT_NTFY]" in klip)
    check("lift only when homed, capped below the stock moves",
          '"z" in th.homed_axes' in klip and "[15, 265 - z]|min" in klip)
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
          and "\ncomplete_webhook: snapshot, jsons://" in wmoon)
    check("webhook gets notifications that push and email don't",
          "progress_25_webhook:" in wmoon and "\nprogress_25:" not in wmoon)
    hook_only = nc.validate(dict(enabled=True, provider="none",
                                 webhook_enabled=True,
                                 webhook_url="http://hooks.local/x"),
                            nc.load())
    check("a webhook alone is enough (print hooks generated)",
          nc.configured(hook_only) and nc.render_klipper(hook_only))
    bad(dict(provider="none", webhook_enabled=True,
             webhook_url="ftp://example.com/x"),
        "rejects a webhook that isn't http(s)")
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
