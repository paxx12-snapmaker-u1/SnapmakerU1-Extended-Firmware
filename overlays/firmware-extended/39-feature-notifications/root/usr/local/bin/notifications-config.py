#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Push notifications (ntfy, Gotify, Pushover, Telegram or Discord), email (SMTP) and a webhook
# configuration.
#
# `[notifications]` in extended2.cfg is the single source of truth. This tool:
#   get                  print the current settings (secret redacted) as JSON
#   save                 read settings JSON on stdin, validate, store, apply
#   test                 read settings JSON on stdin, send a test notification
#   render-moonraker     (re)generate extended/moonraker/notifications.cfg
#   render-klipper       (re)generate extended/klipper/notifications.cfg
#
# The render commands are also run by the Moonraker and Klipper service
# hooks, so generated files always match extended2.cfg and the firmware.

import configparser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

ROOT = os.environ.get("NOTIFICATIONS_ROOT", "/oem/printer_data/config/extended")
EXTENDED_CFG = os.path.join(ROOT, "extended2.cfg")
MOONRAKER_CFG = os.path.join(ROOT, "moonraker", "notifications.cfg")
KLIPPER_CFG = os.path.join(ROOT, "klipper", "notifications.cfg")
MOONRAKER_URL = os.environ.get("NOTIFICATIONS_MOONRAKER", "http://127.0.0.1:7125")
INIT_D = os.environ.get("NOTIFICATIONS_INIT_D", "/etc/init.d")
SNAPSHOT_HOST = os.environ.get("NOTIFICATIONS_SNAPSHOT_HOST", "http://127.0.0.1")
# Secrets (tokens, passwords) live in Moonraker's secrets file, which it
# never serves or logs, as {secrets.notifications.<name>} templates in the
# generated config; extended2.cfg (served by Moonraker) holds none.
SECRETS_FILE = os.environ.get("NOTIFICATIONS_SECRETS",
                              "/home/lava/printer_data/moonraker.secrets")
SECRETS_SECTION = "notifications"
# A restart that was skipped or failed is owed: done on the next save
PENDING_RESTART = {"moonraker": os.path.join(ROOT, ".notifications-restart-moonraker"),
                   "klipper": os.path.join(ROOT, ".notifications-restart-klipper")}
SECTION = "notifications"
COMPONENT = "push_notifications"
REMOTE_METHOD = "push_notification"
LIFT_MM = 15
# The stock PRINT_END / CANCEL_PRINT macros raise Z by +5 mm (relative,
# computed before our hook runs) below this height, so the lift must keep
# Z + lift <= LIFT_CEILING to stay inside the stock macros' range.
LIFT_CEILING = 265
ERROR_INDEX_URL = ("https://wiki.snapmaker.com/en/snapmaker_u1/"
                   "troubleshooting/u1_error_codes")
SNAPSHOT_PATH = {"internal": "/webcam/snapshot.jpg",
                 "usb": "/webcam2/snapshot.jpg"}
LIVE_PATH = {"internal": "/webcam/webrtc", "usb": "/webcam2/webrtc"}
PROVIDERS = ["ntfy", "gotify", "pushover", "telegram", "discord",
             "discord_dm"]
EMAIL_SECURITY = ["starttls", "ssl", "none"]
PRIORITIES = ["min", "low", "default", "high", "max"]
# Pushover emergency (2) repeats until acknowledged, beyond ntfy's "max";
# both high and max map to Pushover high priority.
PUSHOVER_PRIORITY = {"min": -2, "low": -1, "default": 0, "high": 1, "max": 1}
GOTIFY_PRIORITY = {"min": "low", "low": "moderate", "default": "normal",
                   "high": "high", "max": "emergency"}
# Telegram has no priorities: min and low arrive without a sound
TELEGRAM_SILENT = ("min", "low")
TELEGRAM_API = "https://api.telegram.org/bot"
# Discord: min and low arrive without a sound; the channel mention is only
# added to high and max
DISCORD_PING = ("high", "max")
# The Discord senders (Apprise plugins) live with the Moonraker component
COMPONENTS_DIR = os.environ.get(
    "NOTIFICATIONS_COMPONENTS", "/home/lava/moonraker/moonraker/components")
AUTH_MODES = ["none", "token", "basic"]

# Rows that can have a picture but don't by default
IMAGE_OFF_BY_DEFAULT = {"driver_hot"}
# id, label, icon, default push, default priority, image ("render",
# "snapshot", "spool" (drawn spool picture) or None), group, default email
ROWS = [
    ("started", "Print started", "▶️", True, "default", "render", "print",
     False),
    ("paused", "Print paused", "⏸️", True, "default", "snapshot", "print",
     False),
    ("resumed", "Print resumed", "▶️", False, "default", "snapshot", "print",
     False),
    # Off by default: for watching the first layer go down after a long
    # start routine (homing, bed mesh, flow calibration)
    ("layer_started", "First layer started", "🎬", False, "default",
     "snapshot", "print", False),
    ("first_layer", "Layer complete", "🔍", False, "default",
     "snapshot", "print", False),
    ("progress_25", "25% complete", "⏳", False, "low", "snapshot", "print",
     False),
    ("progress_50", "50% complete", "⏳", False, "low", "snapshot", "print",
     False),
    ("progress_75", "75% complete", "⏳", False, "low", "snapshot", "print",
     False),
    ("complete", "Print complete", "✅", True, "default", "snapshot", "print",
     False),
    ("cancelled", "Print cancelled", "🚫", True, "default", "snapshot",
     "print", False),
    ("calibration", "Manual calibrations", "📐", True, "high", "snapshot",
     "print", False),
    ("error", "Error", "⚠️", True, "max", "snapshot", "fault", True),
    ("warning", "Warning", "⚠️", True, "max", "snapshot", "fault", True),

    ("shutdown", "Klipper shutdown", "🛑", True, "max", "snapshot", "fault",
     True),
    ("power_loss", "Power loss recovery", "⚡", True, "max", "snapshot", "fault",
     True),
    # X or Y stepper driver at or over the set temperature; once per print
    # job, again after a pause
    ("driver_hot", "Driver running hot", "🌡️", True, "max", "snapshot",
     "fault", True),
    # Need the Spoolman integration
    ("filament_short", "Not enough filament", "🧵", True, "high", "spool",
     "spoolman", True),
    ("filament_low", "Spool running low", "🧵", True, "default", "spool",
     "spoolman", False),
    ("spool_switch", "Switched to backup spool", "🔄", True, "default",
     "spool", "spoolman", False),
]
ROW_IDS = [r[0] for r in ROWS]
# Print-by-object: "Print complete" for each object is sent as its own
# notification (so a webhook sees event=object_complete), with the Print
# complete row's settings. It isn't a row of its own in the form.
OBJECT_ROW = "object_complete"
ROW = {r[0]: r for r in ROWS}
ERROR_ROWS = ("error", "warning", "shutdown")
SPOOLMAN_ROWS = ("filament_short", "filament_low", "spool_switch")
SPOOLMAN_REV = "2"   # bump when saved Spoolman settings need migrating

RX_SERVER = re.compile(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?/?")
RX_TOPIC = re.compile(r"[A-Za-z0-9_\-]{1,64}")
RX_TOKEN = re.compile(r"[A-Za-z0-9_\-]{1,128}")
RX_USER = re.compile(r"[^\s:]{1,64}")
RX_PUSHOVER_KEY = re.compile(r"[A-Za-z0-9]{20,40}")
# Gotify server (may sit under a path behind a proxy) and app token
RX_GOTIFY_SERVER = re.compile(
    r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?")
RX_GOTIFY_TOKEN = re.compile(r"[A-Za-z0-9._\-]{8,64}")
RX_PUSHOVER_DEVICE = re.compile(r"[A-Za-z0-9_\-]{0,25}")
RX_TELEGRAM_TOKEN = re.compile(r"\d{5,16}:[A-Za-z0-9_\-]{30,64}")
RX_TELEGRAM_CHAT = re.compile(r"-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")
RX_DISCORD_WEBHOOK = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?"
    r"/webhooks/(\d{5,25})/([A-Za-z0-9_\-]{20,100})/?")
RX_DISCORD_TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}"
                              r"\.[A-Za-z0-9_\-]{20,60}")
RX_DISCORD_ID = re.compile(r"\d{5,25}")
RX_DISCORD_MENTION = re.compile(r"|\d{5,25}|role:\d{5,25}|everyone|here")
RX_WEBHOOK = re.compile(r"https?://[^\s]{3,2000}")
RX_PRINTER_URL = re.compile(r"https?://[A-Za-z0-9.\-\[\]:]+(/[^\s]*)?")
RX_NAME = re.compile(r"[\w .()\-]{1,32}")     # \w: letters in any script
RX_TIMEZONE = re.compile(r"[A-Za-z0-9_+\-/]{0,64}")
RX_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
RX_HOST = re.compile(r"[A-Za-z0-9.\-]{1,253}")
RX_EMAIL = re.compile(r"[^@\s,;:<>\"'()\[\]\\]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,63}")
RX_EMAIL_USER = re.compile(r"[^\s]{1,128}")
MAX_RECIPIENTS = 5
SEND_CONNECT_TIMEOUT = 10.   # match push_notifications.py
SEND_READ_TIMEOUT = 60.
STRING_FIELDS = ("provider", "server", "topic", "auth", "username",
                 "pushover_user", "pushover_device", "telegram_chat",
                 "gotify_server",
                 "discord_user", "discord_mention", "printer_url",
                 "camera", "name", "timezone", "first_layer_objects",
                 "complete_objects",
                 "email_host",
                 "email_security", "email_username", "email_from",
                 "email_to")
SECRET_FIELDS = ("secret", "email_password", "webhook_url")
# Settings the form fills in for common providers
EMAIL_PRESETS = {
    "gmail": {"label": "Gmail", "host": "smtp.gmail.com", "port": 587,
              "security": "starttls"},
    "brevo": {"label": "Brevo", "host": "smtp-relay.brevo.com", "port": 587,
              "security": "starttls"},
}


class ConfigError(Exception):
    pass


# ---- storage ------------------------------------------------------------

def defaults():
    return {
        "enabled": False,
        # Push, email and the webhook are each behind their own checkbox
        "push_enabled": True,
        "provider": "ntfy",
        "server": "",
        "topic": "u1",
        "auth": "none",
        "username": "",
        "pushover_user": "",
        "pushover_device": "",
        "gotify_server": "",
        "telegram_chat": "",
        "discord_user": "",
        "discord_mention": "",
        "secret": "",
        "secret_for": "",       # the service the push secret belongs to
        "printer_url": "",
        "camera": "internal",
        "name": "U1",
        "timezone": "",
        "layers_complete": 2,
        # Print-by-object: "layer N complete" and "Print complete" for
        # each object, or once per print job
        "first_layer_objects": "each",
        "complete_objects": "each",
        "filament_low_percent": 10,
        "filament_low_grams": 50,
        "driver_temp": 105,     # °C, X or Y stepper driver (row: driver_hot)
        "email_enabled": False,
        "webhook_enabled": False,
        "webhook_url": "",
        "email_host": "",
        "email_port": 587,
        "email_security": "starttls",
        "email_username": "",
        "email_password": "",
        "email_from": "",
        "email_to": "",
        "rows": {r[0]: {"enabled": r[3], "priority": r[4],
                        "image": r[5] is not None
                        and r[0] not in IMAGE_OFF_BY_DEFAULT,
                        "email": r[7]}
                 for r in ROWS},
    }


def read_cfg():
    cfg = configparser.RawConfigParser(strict=False)
    try:
        cfg.read(EXTENDED_CFG)
    except configparser.Error:
        pass
    return cfg


def read_secrets_file():
    """(values of our section, the whole file's data, its format) from
    Moonraker's secrets file: INI (sections) or JSON (objects)."""
    try:
        with open(SECRETS_FILE) as f:
            text = f.read()
    except OSError:
        return {}, None, "ini"
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            sec = data.get(SECRETS_SECTION) or {}
            return ({k: str(v) for k, v in sec.items()}
                    if isinstance(sec, dict) else {}), data, "json"
    except ValueError:
        pass
    cfg = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cfg.read_string(text)
    except configparser.Error:
        return {}, None, "ini"
    sec = dict(cfg[SECRETS_SECTION]) if cfg.has_section(SECRETS_SECTION) \
        else {}
    return sec, cfg, "ini"


def write_secrets(values):
    """Replace our section of Moonraker's secrets file (0600, owned by
    Moonraker's user); other sections are kept. Empty removes it."""
    _, data, kind = read_secrets_file()
    values = {k: v for k, v in (values or {}).items() if v}
    if kind == "json" and isinstance(data, dict):
        if values:
            data[SECRETS_SECTION] = values
        else:
            data.pop(SECRETS_SECTION, None)
        text = json.dumps(data, indent=2) + "\n"
    else:
        cfg = data if isinstance(data, configparser.RawConfigParser) \
            else configparser.RawConfigParser(strict=False, interpolation=None)
        if cfg.has_section(SECRETS_SECTION):
            cfg.remove_section(SECRETS_SECTION)
        if values:
            cfg.add_section(SECRETS_SECTION)
            for k, v in values.items():
                cfg.set(SECRETS_SECTION, k, v)
        if not cfg.sections():
            text = ""
        else:
            buf = io.StringIO()
            cfg.write(buf)
            text = buf.getvalue()
    old = None
    try:
        with open(SECRETS_FILE) as f:
            old = f.read()
    except OSError:
        pass
    if old == text or (not text and old is None):
        return False
    if not text:
        os.remove(SECRETS_FILE)
        return True
    tmp = SECRETS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    chown_lava(tmp)
    os.replace(tmp, SECRETS_FILE)
    return True


def chown_lava(path):
    try:
        import pwd
        pw = pwd.getpwnam("lava")
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError, ImportError, OSError):
        pass


class SecretBag:
    """Collects the secrets a generated config refers to by template; the
    plain one hands them back as they are (for Send Test)."""

    def __init__(self, plain=False):
        self.values = {}
        self.plain_values = plain

    @classmethod
    def plain(cls):
        return cls(plain=True)

    def put(self, name, value):
        if self.plain_values:
            return value
        self.values[name] = value
        return "{secrets.%s.%s}" % (SECRETS_SECTION, name)


def load():
    s = load_from(read_cfg())
    # Secrets come from Moonraker's secrets file; a value still in
    # extended2.cfg (older settings) is taken until the next save moves it
    stored, _, _ = read_secrets_file()
    for key in SECRET_FIELDS:
        if stored.get(key):
            s[key] = stored[key]
    s["secret_for"] = stored.get("secret_for", s.get("secret_for", ""))
    return s


def load_from(cfg):
    """Settings from a parsed extended2.cfg (or a plain dict of the
    [notifications] section), over the defaults."""
    s = defaults()
    if isinstance(cfg, dict):
        sec = cfg
    elif not cfg.has_section(SECTION):
        return s
    else:
        sec = cfg[SECTION]
    s["enabled"] = sec.get("enabled", "false").strip().lower() == "true"
    s["push_enabled"] = \
        sec.get("push_enabled", "true").strip().lower() == "true"
    if sec.get("provider", "").strip() == "none":
        # Older settings: "none" meant no push service
        s["push_enabled"] = False
    s["email_enabled"] = \
        sec.get("email_enabled", "false").strip().lower() == "true"
    s["webhook_enabled"] = \
        sec.get("webhook_enabled", "false").strip().lower() == "true"
    for key in STRING_FIELDS + SECRET_FIELDS:
        if key in sec:
            s[key] = sec.get(key).strip()
    for key in ("layers_complete", "email_port", "filament_low_percent",
                "filament_low_grams", "driver_temp"):
        try:
            s[key] = int(sec.get(key, str(s[key])))
        except ValueError:
            pass
    for rid in ROW_IDS:
        raw = sec.get(rid)
        if not raw:
            continue
        parts = [p.strip().lower() for p in raw.split(",")]
        row = s["rows"][rid]
        row["enabled"] = parts[0] == "on"
        if len(parts) > 1 and parts[1] in PRIORITIES:
            row["priority"] = parts[1]
        if len(parts) > 2:
            row["image"] = parts[2] == "image"
        if len(parts) > 3:
            row["email"] = parts[3] == "email"
    if sec.get("spoolman_rev", "").strip() != SPOOLMAN_REV:
        # Saved before the Spoolman rows had pictures and the low-spool
        # alert became "% of a full spool or grams, whichever first"
        for rid in SPOOLMAN_ROWS:
            s["rows"][rid]["image"] = True
        if not s["filament_low_grams"]:
            s["filament_low_grams"] = defaults()["filament_low_grams"]
    return s


def camera_status():
    """Which snapshot sources are served (only the paxx12 service has one)."""
    cfg = read_cfg()
    internal = cfg.get("camera", "internal", fallback="paxx12").strip()
    usb = cfg.get("camera", "usb", fallback="none").strip()
    return {"internal": internal == "paxx12", "usb": usb == "paxx12"}


def section_lines(s):
    lines = [
        f"[{SECTION}]",
        "# Push notifications; managed by firmware-config.",
        f"enabled: {'true' if s['enabled'] else 'false'}",
        f"push_enabled: {'true' if s['push_enabled'] else 'false'}",
        f"provider: {s['provider']}",
        f"server: {s['server']}",
        f"topic: {s['topic']}",
        f"auth: {s['auth']}",
        f"username: {s['username']}",
        f"pushover_user: {s['pushover_user']}",
        f"pushover_device: {s['pushover_device']}",
        f"gotify_server: {s['gotify_server']}",
        f"telegram_chat: {s['telegram_chat']}",
        f"discord_user: {s['discord_user']}",
        f"discord_mention: {s['discord_mention']}",
        "secret:",
        f"printer_url: {s['printer_url']}",
        f"camera: {s['camera']}",
        f"name: {s['name']}",
        f"timezone: {s['timezone']}",
        f"layers_complete: {s['layers_complete']}",
        f"first_layer_objects: {s['first_layer_objects']}",
        f"complete_objects: {s['complete_objects']}",
        f"filament_low_percent: {s['filament_low_percent']}",
        f"filament_low_grams: {s['filament_low_grams']}",
        f"driver_temp: {s['driver_temp']}",
        f"spoolman_rev: {SPOOLMAN_REV}",
        f"email_enabled: {'true' if s['email_enabled'] else 'false'}",
        f"email_host: {s['email_host']}",
        f"email_port: {s['email_port']}",
        f"email_security: {s['email_security']}",
        f"email_username: {s['email_username']}",
        "email_password:",
        f"email_from: {s['email_from']}",
        f"email_to: {s['email_to']}",
        f"webhook_enabled: {'true' if s['webhook_enabled'] else 'false'}",
        "webhook_url:",
        "# <notification>: on|off (push), <priority>, image|no_image, "
        "email|no_email",
    ]
    for rid in ROW_IDS:
        r = s["rows"][rid]
        lines.append(f"{rid}: {'on' if r['enabled'] else 'off'}, "
                     f"{r['priority']}, "
                     f"{'image' if r['image'] else 'no_image'}, "
                     f"{'email' if r['email'] else 'no_email'}")
    return [ln + "\n" for ln in lines]


def store(s):
    """Replace the [notifications] block. Secrets aren't written here:
    they go to Moonraker's secrets file (write_secrets)."""
    with open(EXTENDED_CFG, "r") as f:
        lines = f.readlines()
    header = re.compile(rf"^\s*\[\s*{SECTION}\s*\]\s*$")
    any_header = re.compile(r"^\s*\[.*\]\s*$")
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
    block = section_lines(s)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend(block)
    else:
        # Up to the next section header: configparser reads on past a
        # blank line, so a stray line there would otherwise survive
        end = start + 1
        while end < len(lines) and not any_header.match(lines[end]):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[start:end] = block
    tmp = EXTENDED_CFG + ".tmp"
    st = os.stat(EXTENDED_CFG)
    with open(tmp, "w") as f:
        f.writelines(lines)
    try:
        os.chmod(tmp, st.st_mode & 0o7777)
        os.chown(tmp, st.st_uid, st.st_gid)
    except OSError:
        pass
    os.replace(tmp, EXTENDED_CFG)


# ---- validation ---------------------------------------------------------

def validate(data, current):
    """Merge submitted JSON over current settings and validate."""
    if not isinstance(data, dict):
        raise ConfigError("Invalid request")
    s = json.loads(json.dumps(current))
    if "enabled" in data:
        s["enabled"] = bool(data["enabled"])
    if "push_enabled" in data:
        s["push_enabled"] = bool(data["push_enabled"])
    if "secret" in data and str(data["secret"]):
        s["secret_for"] = ""     # set below, once the service is known
    if "email_enabled" in data:
        s["email_enabled"] = bool(data["email_enabled"])
    if "webhook_enabled" in data:
        s["webhook_enabled"] = bool(data["webhook_enabled"])
    for key in STRING_FIELDS:
        if key in data:
            s[key] = str(data[key] or "").strip()
    for key in SECRET_FIELDS:
        if data.get(key):
            s[key] = str(data[key])
    for key, lo, hi in (("filament_low_percent", 0, 90),
                        ("filament_low_grams", 0, 5000)):
        if key in data:
            try:
                s[key] = int(data[key] or 0)
            except (TypeError, ValueError):
                raise ConfigError("Filament alert values must be numbers")
        if not lo <= s[key] <= hi:
            raise ConfigError("Filament alert: 0-90 % and 0-5000 g")
    if "email_port" in data:
        try:
            s["email_port"] = int(data["email_port"])
        except (TypeError, ValueError):
            raise ConfigError("Email port must be a number")
    if "layers_complete" in data:
        try:
            s["layers_complete"] = int(data["layers_complete"])
        except (TypeError, ValueError):
            raise ConfigError("Layer must be 1, 2 or 3")
    if "driver_temp" in data:
        try:
            s["driver_temp"] = int(data["driver_temp"])
        except (TypeError, ValueError):
            raise ConfigError("Driver temperature must be a number")
    if not 40 <= s["driver_temp"] <= 150:
        raise ConfigError("Driver temperature alert: 40 to 150 °C")
    rows = data.get("rows") or {}
    if not isinstance(rows, dict):
        raise ConfigError("Invalid request")
    for rid, row in rows.items():
        if rid not in s["rows"] or not isinstance(row, dict):
            continue
        r = s["rows"][rid]
        r["enabled"] = bool(row.get("enabled", r["enabled"]))
        if row.get("priority") in PRIORITIES:
            r["priority"] = row["priority"]
        r["image"] = bool(row.get("image", r["image"]))
        r["email"] = bool(row.get("email", r["email"]))
    for rid in ROW_IDS:
        if ROW[rid][5] is None:
            s["rows"][rid]["image"] = False

    # Values end up in extended2.cfg and generated config files
    for key in STRING_FIELDS + SECRET_FIELDS:
        if RX_CONTROL.search(s[key]):
            raise ConfigError(f"{key}: control characters are not allowed")
    s["server"] = s["server"].rstrip("/")
    s["email_password"] = s["email_password"].strip()
    # The push secret is one field for every service (ntfy token or
    # password, Pushover app token, Telegram bot token, Discord token or
    # webhook). A stored one is only reused for the service, server and
    # account it was entered for: switching services means entering the
    # new one, and a saved token is never sent to another host.
    scope = secret_scope(s)
    if s["secret"] and s.get("secret_for", "") not in ("", scope):
        s["secret"] = ""
        if s["push_enabled"]:
            raise ConfigError(
                "The saved token or password was entered for another "
                "service or server. Enter the one for this one.")
    s["secret_for"] = scope if s["secret"] else ""
    if s["provider"] not in PROVIDERS:
        raise ConfigError("Invalid notification service")
    if s["auth"] not in AUTH_MODES:
        raise ConfigError("Invalid authentication mode")
    if s["camera"] not in SNAPSHOT_PATH:
        raise ConfigError("Invalid camera")
    if s["layers_complete"] not in (1, 2, 3):
        raise ConfigError("Layer must be 1, 2 or 3")
    for key in ("first_layer_objects", "complete_objects"):
        if s[key] not in ("each", "once"):
            raise ConfigError("When printing by object: once per object "
                              "or once per print job")
    if not RX_NAME.fullmatch(s["name"]):
        raise ConfigError("Printer name: up to 32 letters, digits, spaces "
                          "and . _ - ( )")
    if not RX_TIMEZONE.fullmatch(s["timezone"]):
        raise ConfigError("Invalid time zone")
    if s["printer_url"] and not RX_PRINTER_URL.fullmatch(s["printer_url"]):
        raise ConfigError("Printer URL must look like http://100.64.0.1")
    if s["printer_url"] and s["provider"] == "telegram" and s["push_enabled"]:
        host = urlparse(printer_root(s)).hostname or ""
        if "." not in host and ":" not in host:
            # Telegram drops text links whose host has no dot; the Tailscale
            # name was already replaced by the IP in printer_root
            raise ConfigError("Telegram doesn't link to a name without a "
                              "dot, such as http://u1. Use the Tailscale "
                              "name (Use Tailscale) or an IP address.")

    validate_email(s)
    if s["webhook_enabled"]:
        try:
            u = urlparse(s["webhook_url"])
            ok = bool(RX_WEBHOOK.fullmatch(s["webhook_url"]) and u.hostname
                      and (u.port is None or 0 < u.port < 65536))
        except ValueError:
            ok = False
        if not ok:
            raise ConfigError("Webhook URL must look like "
                              "https://example.com/hook")
    if s["enabled"] and not s["push_enabled"] and \
            not s["email_enabled"] and not s["webhook_enabled"]:
        raise ConfigError("Enable push notifications, email or a webhook")
    if not s["push_enabled"]:
        pass
    elif s["provider"] == "ntfy":
        if s["enabled"] or s["server"]:
            if not RX_SERVER.fullmatch(s["server"]):
                raise ConfigError(
                    "Server must look like https://ntfy.example.com or "
                    "http://192.168.1.10:8080 (no path)")
            if not RX_TOPIC.fullmatch(s["topic"]):
                raise ConfigError("Topic: 1-64 letters, digits, - or _")
            if is_public(s["server"]) and len(s["topic"]) < 16:
                raise ConfigError("On the public ntfy.sh server use a topic "
                                  "of at least 16 characters")
        if s["auth"] == "token":
            if not RX_TOKEN.fullmatch(s["secret"]):
                raise ConfigError("Access token is missing or invalid")
        elif s["auth"] == "basic":
            if not RX_USER.fullmatch(s["username"]):
                raise ConfigError("Username is missing or invalid")
            if not s["secret"] or s["secret"] != s["secret"].strip():
                raise ConfigError("Password is missing or has leading or "
                                  "trailing spaces")
        if s["auth"] != "basic":
            s["username"] = ""
        if s["auth"] == "none":
            s["secret"] = ""
    elif s["provider"] == "gotify":
        if s["enabled"] or s["gotify_server"]:
            if not RX_GOTIFY_SERVER.fullmatch(s["gotify_server"]):
                raise ConfigError("Gotify server must look like "
                                  "https://gotify.example.com")
            try:
                urlparse(s["gotify_server"]).port
            except ValueError:
                raise ConfigError("Gotify server: port out of range")
        if s["enabled"] or s["secret"]:
            if not RX_GOTIFY_TOKEN.fullmatch(s["secret"]):
                raise ConfigError("Gotify app token is missing or invalid "
                                  "(Gotify web UI > Apps > Create "
                                  "Application)")
    elif s["provider"] == "telegram":
        if s["enabled"] or s["secret"]:
            if not RX_TELEGRAM_TOKEN.fullmatch(s["secret"]):
                raise ConfigError("Telegram bot token is missing or invalid "
                                  "(from @BotFather, like 123456789:ABC...)")
        if s["telegram_chat"] and \
                not RX_TELEGRAM_CHAT.fullmatch(s["telegram_chat"]):
            raise ConfigError("Telegram chat ID: a number like 123456789 "
                              "(or -100... for a group), or @channelname")
    elif s["provider"] == "discord":
        if s["enabled"] or s["secret"]:
            if not RX_DISCORD_WEBHOOK.fullmatch(s["secret"]):
                raise ConfigError("Discord webhook URL is missing or invalid "
                                  "(https://discord.com/api/webhooks/...)")
        if not RX_DISCORD_MENTION.fullmatch(s["discord_mention"]):
            raise ConfigError("Discord mention: a user ID, role:<role ID>, "
                              "everyone or here (or empty)")
    elif s["provider"] == "discord_dm":
        if s["enabled"] or s["secret"]:
            if not RX_DISCORD_TOKEN.fullmatch(s["secret"]):
                raise ConfigError("Discord bot token is missing or invalid "
                                  "(Developer Portal > your app > Bot > "
                                  "Reset Token)")
        if s["discord_user"] and not RX_DISCORD_ID.fullmatch(
                s["discord_user"]):
            raise ConfigError("Discord user ID: digits only")
    else:
        if s["enabled"] or s["pushover_user"]:
            if not RX_PUSHOVER_KEY.fullmatch(s["pushover_user"]):
                raise ConfigError("Pushover user key is missing or invalid")
            if not RX_PUSHOVER_KEY.fullmatch(s["secret"]):
                raise ConfigError("Pushover API token is missing or invalid")
        if not RX_PUSHOVER_DEVICE.fullmatch(s["pushover_device"]):
            raise ConfigError("Pushover device: up to 25 letters, digits, "
                              "- or _")
    return s


def recipients(s):
    return [a.strip() for a in s["email_to"].split(",") if a.strip()]


def validate_email(s):
    # Only when email is on: the form always sends its preset server, and
    # half-filled email settings must not block saving push settings.
    if not s["email_enabled"]:
        return
    if s["email_security"] not in EMAIL_SECURITY:
        raise ConfigError("Invalid email security")
    if not 1 <= s["email_port"] <= 65535:
        raise ConfigError("Email port must be 1-65535")
    if not RX_HOST.fullmatch(s["email_host"]):
        raise ConfigError("SMTP server must be a host name like "
                          "smtp.example.com")
    if s["email_username"] and not RX_EMAIL_USER.fullmatch(
            s["email_username"]):
        raise ConfigError("Email username must not contain spaces")
    if s["email_username"] and not s["email_password"]:
        raise ConfigError("Email password is missing")
    if not RX_EMAIL.fullmatch(s["email_from"]):
        raise ConfigError("From must be an email address")
    to = recipients(s)
    if not to or len(to) > MAX_RECIPIENTS or \
            not all(RX_EMAIL.fullmatch(a) for a in to):
        raise ConfigError(f"To: 1 to {MAX_RECIPIENTS} email addresses, "
                          "separated by commas")
    s["email_to"] = ", ".join(to)
    if not s["email_username"]:
        s["email_password"] = ""


def is_public(server):
    return urlparse(server).hostname in ("ntfy.sh", "www.ntfy.sh")


def secret_scope(s):
    """What a stored push secret is bound to."""
    if s["provider"] == "ntfy":
        return f"ntfy|{s['auth']}|{s['server'].lower()}|{s['username']}"
    if s["provider"] == "gotify":
        return f"gotify|{s['gotify_server'].lower().rstrip('/')}"
    return s["provider"]


def push_configured(s):
    if not s["enabled"] or not s["push_enabled"]:
        return False
    if s["provider"] == "ntfy":
        return bool(s["server"] and s["topic"])
    if s["provider"] == "pushover":
        return bool(s["pushover_user"] and s["secret"])
    if s["provider"] == "gotify":
        return bool(s["gotify_server"] and s["secret"])
    if s["provider"] == "telegram":
        return bool(s["secret"] and s["telegram_chat"])
    if s["provider"] == "discord":
        return bool(s["secret"])
    if s["provider"] == "discord_dm":
        return bool(s["secret"] and s["discord_user"])
    return False


def email_configured(s):
    return bool(s["enabled"] and s["email_enabled"] and s["email_host"]
                and s["email_from"] and recipients(s))


def webhook_configured(s):
    return bool(s["enabled"] and s["webhook_enabled"] and s["webhook_url"])


def configured(s):
    return push_configured(s) or email_configured(s) or webhook_configured(s)


# ---- rendering ----------------------------------------------------------

def links(s, rid, cam):
    """(title, url) buttons for a notification."""
    out = []
    if s["printer_url"]:
        root = printer_root(s)
        out.append(("Web UI", root + "/"))
        if cam:
            out.append(("Live Camera", root + LIVE_PATH[s["camera"]]))
    if rid in ERROR_ROWS:
        out.append(("Error Index", ERROR_INDEX_URL))
    return out


def query(params):
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())


def row_url(s, rid, priority, cam, sec=None):
    """Apprise URL for one notification row. With `sec`, secrets go in as
    templates (for the generated config); without, as they are (Send
    Test)."""
    btns = links(s, rid, cam)
    sec = sec or SecretBag.plain()
    if s["provider"] == "pushover":
        # One link only: the Web UI, or the error index for faults when no
        # printer URL is set
        params = {"priority": PUSHOVER_PRIORITY[priority]}
        if btns:
            title, url = btns[0]
            params["url"], params["url_title"] = url, title
        device = f"/{s['pushover_device']}" if s["pushover_device"] else ""
        token = sec.put("push_token", s["secret"])
        return (f"pover://{s['pushover_user']}@{token}{device}"
                f"?{query(params)}")
    if s["provider"] == "gotify":
        # Markdown, links in the text (push_notifications adds them); no
        # pictures: Gotify messages can't carry attachments
        u = urlparse(s["gotify_server"])
        scheme = "gotifys" if u.scheme == "https" else "gotify"
        port = f":{u.port}" if u.port else ""
        path = (u.path or "/").rstrip("/") + "/"
        token = sec.put("push_token", s["secret"])
        params = {"priority": GOTIFY_PRIORITY[priority],
                  "format": "markdown"}
        return (f"{scheme}://{u.hostname}{port}{path}{token}"
                f"?{query(params)}")
    if s["provider"] == "telegram":
        # Sent as HTML, links in the text (push_notifications adds them);
        # image=no: no Apprise icon; content=after: text under the picture
        params = {"format": "html", "image": "no", "content": "after",
                  "preview": "no", "detect": "no",
                  "silent": "yes" if priority in TELEGRAM_SILENT else "no"}
        token = sec.put("push_token", s["secret"])
        return (f"tgram://{token}/{quote(s['telegram_chat'], safe='@-')}"
                f"?{query(params)}")
    if s["provider"] in ("discord", "discord_dm"):
        # discordhook:// and discorddm:// (push_notifications_discord.py)
        params = {"silent": "yes" if priority in TELEGRAM_SILENT else "no"}
        if s["provider"] == "discord_dm":
            token = sec.put("push_token", s["secret"])
            return (f"discorddm://{token}@{s['discord_user']}"
                    f"?{query(params)}")
        m = RX_DISCORD_WEBHOOK.fullmatch(s["secret"])
        params["name"] = s["name"]
        if s["discord_mention"] and priority in DISCORD_PING:
            params["ping"] = s["discord_mention"]
        token = sec.put("push_token", m.group(2))
        return f"discordhook://{m.group(1)}/{token}?{query(params)}"
    u = urlparse(s["server"])
    scheme = "ntfys" if u.scheme == "https" else "ntfy"
    params = {"image": "no", "priority": priority}
    if btns:
        params["actions"] = "; ".join(
            f"view, {t}, {url}" + (", clear=true" if t == "Web UI" else "")
            for t, url in btns)
    userinfo = ""
    extra = ""
    if s["auth"] == "basic":
        userinfo = (quote(s["username"], safe="") + ":" +
                    sec.put("push_password", quote(s["secret"], safe=""))
                    + "@")
    elif s["auth"] == "token":
        # Not through query(): a template must not be percent-encoded
        extra = "&token=" + sec.put("push_token", s["secret"])
    return (f"{scheme}://{userinfo}{u.netloc}/{s['topic']}?{query(params)}"
            f"{extra}")


def webhook_url(s, rid, cam=False, sec=None):
    """Apprise JSON URL for the webhook: POSTs {"title", "message", "type",
    "attachments": [image as base64], "event": <notification>, "printer":
    <name>}. The webhook's own query parameters are kept (Apprise sends
    "-name" parameters as-is)."""
    u = urlparse(s["webhook_url"])
    scheme = "jsons" if u.scheme == "https" else "json"
    sec = sec or SecretBag.plain()
    # urlparse leaves userinfo percent-encoded: keep it as given
    userinfo = u.netloc.rsplit("@", 1)[0] + "@" if "@" in u.netloc else ""
    host = u.netloc.rsplit("@", 1)[-1]      # keeps IPv6 brackets and port
    own = [f"-{k}={v}" for k, v in
           (part.split("=", 1) if "=" in part else (part, "")
            for part in u.query.split("&") if part)]
    # The whole address is the secret (a token may be anywhere in it)
    base = sec.put("webhook", f"{scheme}://{userinfo}{host}{u.path or '/'}"
                   + ("?" + "&".join(own) if own else ""))
    params = [f":event={quote(rid, safe='')}",
              f":printer={quote(s['name'], safe='')}"]
    # The links, as fields (a webhook has no buttons)
    names = {"Web UI": "web_ui", "Live Camera": "live_camera",
             "Error Index": "error_index"}
    params += [f":{names[t]}={quote(url, safe='')}"
               for t, url in links(s, rid, cam)]
    return base + ("&" if own else "?") + "&".join(params)


def spoolman_url():
    """Spoolman URL when the Spoolman integration is enabled, else ''."""
    url = read_cfg().get("spoolman", "host", fallback="").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


def available_rows():
    """The Spoolman rows need the Spoolman integration."""
    if spoolman_url():
        return ROW_IDS
    return [rid for rid in ROW_IDS if rid not in SPOOLMAN_ROWS]


def push_rows(s):
    if not push_configured(s):
        return []
    return [rid for rid in available_rows() if s["rows"][rid]["enabled"]]


def email_rows(s):
    if not email_configured(s):
        return []
    return [rid for rid in available_rows() if s["rows"][rid]["email"]]


def webhook_rows(s):
    """The webhook gets every notification (the receiver picks by
    "event"), whatever the push and email choices."""
    if not webhook_configured(s):
        return []
    return list(available_rows())


def enabled_rows(s):
    """Notifications sent to at least one destination."""
    push, email, hook = push_rows(s), email_rows(s), webhook_rows(s)
    return [rid for rid in ROW_IDS if rid in push or rid in email
            or rid in hook]


def email_url(s, sec=None):
    """Apprise URL for email (SMTP)."""
    sec = sec or SecretBag.plain()
    scheme = "mailto" if s["email_security"] == "none" else "mailtos"
    userinfo = ""
    if s["email_username"]:
        userinfo = (quote(s["email_username"], safe="") + ":" +
                    sec.put("email_password",
                            quote(s["email_password"], safe="")) + "@")
    # HTML card (push_notifications_email.py) with the image inline;
    # Apprise adds a plain-text version
    params = {"from": s["email_from"], "to": ",".join(recipients(s)),
              "name": s["name"], "format": "html", "inline": "yes"}
    if s["email_security"] != "none":
        params["mode"] = s["email_security"]
    return (f"{scheme}://{userinfo}{s['email_host']}:{s['email_port']}"
            f"?{query(params)}")


def image_kind(s, rid, cam):
    kind = ROW[rid][5]
    if not s["rows"][rid]["image"] or kind is None:
        return "none"
    if kind == "snapshot" and not cam:
        return "none"
    return kind


def render_moonraker(s, sec=None):
    """The generated Moonraker config. Secrets go into `sec` (a SecretBag)
    and the text refers to them by template; see write_secrets."""
    sec = sec if sec is not None else SecretBag()
    if not configured(s):
        return None
    rows = enabled_rows(s)
    if not rows:
        return None
    cam = camera_status()[s["camera"]]
    push, email, hook = push_rows(s), email_rows(s), webhook_rows(s)
    out = [
        "# Push notifications; generated from extended2.cfg [notifications].",
        "# Do not edit: changes are overwritten on Moonraker start. Use "
        "firmware-config.",
        "",
        f"[{COMPONENT}]",
        f"name: {s['name']}",
    ]
    if cam:
        out.append(f"snapshot_url: {SNAPSHOT_HOST}{SNAPSHOT_PATH[s['camera']]}")
    out.append(f"layers_complete: {s['layers_complete']}")
    out.append(f"first_layer_objects: {s['first_layer_objects']}")
    if "driver_hot" in rows:
        out.append(f"driver_temp: {s['driver_temp']}")
    # complete_objects isn't a component option: it decides whether the
    # object_complete rows below exist
    if s["timezone"]:
        out.append(f"timezone: {s['timezone']}")
    if any(r in rows for r in SPOOLMAN_ROWS):
        out.append(f"spoolman_url: {spoolman_url()}")
        out.append(f"filament_low_percent: {s['filament_low_percent']}")
        out.append(f"filament_low_grams: {s['filament_low_grams']}")
    # Links in the text: email and Telegram have no buttons
    text_links = email or (push and s["provider"] in (
        "gotify", "telegram", "discord", "discord_dm"))
    if text_links and s["printer_url"]:
        root = printer_root(s)
        out.append(f"web_ui_url: {root}/")
        if cam:
            out.append(f"camera_url: {root}{LIVE_PATH[s['camera']]}")
    for rid in rows:
        kind = image_kind(s, rid, cam)
        # The object-complete notification follows the Print complete row
        for name in ([rid, OBJECT_ROW] if rid == "complete"
                     and s["complete_objects"] == "each" else [rid]):
            if rid in push:
                url = row_url(s, rid, s["rows"][rid]["priority"], cam, sec)
                pkind = "none" if s["provider"] == "gotify" else kind
                out.append(f"{name}: {pkind}, {url}")
            if rid in email:
                out.append(f"{name}_email: {kind}, {email_url(s, sec)}")
            if rid in hook:
                out.append(f"{name}_webhook: {kind}, "
                           f"{webhook_url(s, name, cam, sec)}")
    return "\n".join(out) + "\n"


def klipper_lift():
    return f"""[gcode_macro _NTFY_LIFT]
description: Lift up to {LIFT_MM} mm before the print-end snapshot
gcode:
    {{% set th = printer.toolhead %}}
    {{% set z = printer.gcode_move.gcode_position.z %}}
    {{% set lift = [{LIFT_MM}, {LIFT_CEILING} - z]|min %}}
    {{% if "z" in th.homed_axes and lift > 0 %}}
    SAVE_GCODE_STATE NAME=ntfy_lift
    # Z only: retraction is the slicer's and the printer's own
    G91
    # The U1's max Z speed (30 mm/s): 15 mm takes about half a second
    G1 Z{{lift}} F1800
    RESTORE_GCODE_STATE NAME=ntfy_lift
    {{% endif %}}
    # Klipper reads G-code ahead of the motion: wait until the toolhead
    # is really up, so the snapshot isn't taken with the nozzle on the part
    M400

[gcode_macro _NTFY_SEND]
description: Report a print hook event to the push_notifications component
gcode:
    {{action_call_remote_method("{REMOTE_METHOD}", event=params.EVENT)}}"""


def render_klipper(s):
    """Print-end and cancel hooks. They run before the stock macros: lift
    (only if homed, and only as far as the stock macros allow) and report,
    so the snapshot shows the part. There is no hold: the stock macro's own
    rise carries on while the snapshot is taken, so the nozzle doesn't sit
    over the part and ooze onto it. A hook must never fail, or
    the stock macro (heaters off, motors off) would not run."""
    if not configured(s):
        return None
    rows = enabled_rows(s)
    cam = camera_status()[s["camera"]]
    parts = []

    def snap(rid):
        return bool(rid) and image_kind(s, rid, cam) == "snapshot"

    if "complete" in rows:
        lift = snap("complete")
        parts.append(f"""[gcode_macro _PRINT_END_NTFY]
description: Notify print complete (runs before PRINT_END parks)
gcode:
    {{% if printer.print_stats.state == "printing" %}}
    {"_NTFY_LIFT" if lift else ""}
    _NTFY_SEND EVENT=complete
    {{% endif %}}""")

    fault_rid = "error" if "error" in rows else (
        "cancelled" if "cancelled" in rows else None)
    plain_rid = "cancelled" if "cancelled" in rows else None
    if fault_rid or plain_rid:
        parts.append(f"""[gcode_macro _CANCEL_PRINT_NTFY]
description: Notify print cancelled (runs before CANCEL_PRINT parks)
gcode:
    {{% set ps = printer.print_stats %}}
    {{% set fault = ps.exception and ps.exception.code is defined %}}
    {{% set notify = {bool(fault_rid)} if fault else {bool(plain_rid)} %}}
    {{% set lift = {snap(fault_rid)} if fault else {snap(plain_rid)} %}}
    {{% if notify and ps.state in ["printing", "paused"] %}}
    {{% if lift %}}
    _NTFY_LIFT
    {{% endif %}}
    _NTFY_SEND EVENT=cancelled
    {{% endif %}}""")

    if not parts:
        return None
    head = ("# Push notifications; generated from extended2.cfg "
            "[notifications].\n# Do not edit: changes are overwritten on "
            "Klipper start. Use firmware-config.")
    text = "\n\n".join([head, klipper_lift()] + parts) + "\n"
    return re.sub(r"\n    \n", "\n", text)


def write_generated(path, text):
    """Write or remove a generated file; return True if it changed."""
    old = None
    if os.path.exists(path):
        with open(path, "r") as f:
            old = f.read()
    if text == old:
        return False
    if text is None:
        os.remove(path)
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    try:
        import pwd
        pw = pwd.getpwnam("lava")
        os.chown(tmp, pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError, ImportError):
        pass
    os.replace(tmp, path)
    return True


# ---- commands -----------------------------------------------------------

def telegram_find_chat(s):
    """Fill in an empty Telegram chat ID from the bot's recent messages:
    the chat of the latest message sent to the bot (a private chat, or a
    group the bot was added to). Telegram keeps them for 24 hours."""
    if not s["push_enabled"] or s["provider"] != "telegram" or \
            s["telegram_chat"] or not s["secret"]:
        return
    print("Looking up the Telegram chat (the latest message your bot "
          "received)...", flush=True)
    url = f"{TELEGRAM_API}{s['secret']}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise ConfigError("Telegram doesn't accept this bot token; copy "
                              "it again from @BotFather")
        raise ConfigError(f"Could not reach Telegram ({e})")
    except Exception as e:
        raise ConfigError(f"Could not reach Telegram ({e})")
    chat = None
    for upd in data.get("result") or []:
        msg = (upd.get("message") or upd.get("my_chat_member")
               or upd.get("channel_post") or {})
        chat = msg.get("chat") or chat
    if not chat or "id" not in chat:
        raise ConfigError("Open your bot in Telegram and press Start (or "
                          "send it any message), then try again. To use a "
                          "group, add the bot to it and send a message there.")
    s["telegram_chat"] = str(chat["id"])
    name = chat.get("title") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
        or chat.get("username") or ""
    print(f"Telegram chat found: {name} ({s['telegram_chat']})")


def printer_busy():
    """True if printing or paused, or if that cannot be confirmed."""
    try:
        url = MOONRAKER_URL + "/printer/objects/query?print_stats=state"
        with urllib.request.urlopen(url, timeout=5) as resp:
            state = json.load(resp)["result"]["status"]["print_stats"]["state"]
        return state in ("printing", "paused")
    except Exception:
        return True


def local_ip():
    """The printer's LAN address (the source address of its default route)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # no packet is sent
            return sock.getsockname()[0]
    except OSError:
        return ""


_TAILSCALE = None


def tailscale_info():
    """(MagicDNS name, IPv4) of this printer, if Tailscale is enabled and
    running; ("", "") otherwise. Looked up once per run."""
    global _TAILSCALE
    if _TAILSCALE is None:
        _TAILSCALE = _tailscale_info()
    return _TAILSCALE


def printer_root(s):
    """The Printer URL without a trailing slash, as sent: for Telegram the
    Tailscale name becomes the Tailscale IP, because Telegram drops text
    links whose host has no dot (the text stays, the link is gone)."""
    root = s["printer_url"].rstrip("/")
    if s["provider"] == "telegram" and s["push_enabled"]:
        host, ip = tailscale_info()
        u = urlparse(root)
        if host and ip and (u.hostname or "").lower() == host.lower():
            port = f":{u.port}" if u.port else ""
            root = u._replace(netloc=f"{ip}{port}").geturl()
    return root


def _tailscale_info():
    if read_cfg().get("remote_access", "vpn", fallback="none").strip() \
            != "tailscale":
        return "", ""
    try:
        out = subprocess.run(["/usr/local/bin/tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=5)
        st = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", ""
    if st.get("BackendState") != "Running":
        return "", ""
    me = st.get("Self") or {}
    dns = (me.get("DNSName") or "").rstrip(".")
    # The short MagicDNS name resolves on devices with MagicDNS enabled
    host = dns.split(".")[0] if dns else (me.get("HostName") or "")
    ip = next((a for a in me.get("TailscaleIPs") or [] if ":" not in a), "")
    return host, ip


def cmd_status():
    """One line for the settings list: what's on."""
    s = load()
    try:
        validate({}, s)
    except ConfigError:
        print("Needs attention")
        return 0
    if not s["enabled"]:
        print("Disabled")
        return 0
    on = []
    if push_configured(s):
        on.append({"discord_dm": "Discord"}.get(
            s["provider"], s["provider"].capitalize()
            if s["provider"] != "ntfy" else "ntfy"))
    if email_configured(s):
        on.append("email")
    if webhook_configured(s):
        on.append("webhook")
    print("Enabled: " + ", ".join(on) if on else "Enabled (not set up)")
    return 0


def cmd_get():
    ts_host, ts_ip = tailscale_info()
    s = load()
    stored_error = ""
    try:
        validate({}, s)
    except ConfigError as e:
        stored_error = str(e)
    for key in SECRET_FIELDS:
        s[f"has_{key}"] = bool(s[key])
        s[key] = ""
    s["meta"] = {
        "rows": [{"id": r[0], "label": r[1], "icon": r[2],
                  "image": r[5], "group": r[6]} for r in ROWS],
        "email_presets": EMAIL_PRESETS,
        "spoolman": bool(spoolman_url()),
        "priorities": PRIORITIES,
        "providers": PROVIDERS,
        "cameras": camera_status(),
        "local_ip": local_ip(),
        "tailscale_host": ts_host,
        "tailscale_ip": ts_ip,
        # Printing or paused: the form asks before a save that restarts
        # Moonraker (read again right before saving)
        "busy": printer_busy(),
        "lift_mm": LIFT_MM,
        # The saved settings no longer pass validation (e.g. after an
        # update): the generated config is left as it was until fixed
        "stored_error": stored_error,
    }
    print(json.dumps(s))


def read_stdin_json():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        raise ConfigError("Invalid JSON")


def components_module(name):
    """A helper module that lives with the Moonraker component, loaded from
    its file. The components folder must not go on sys.path: Moonraker's own
    components there (secrets.py, ...) would shadow standard modules that
    requests and Apprise import, and fail on their relative imports."""
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(COMPONENTS_DIR, name + ".py")
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    except (ImportError, OSError) as e:
        sys.modules.pop(name, None)
        raise ConfigError(f"{name} is not installed ({e})")


def discord_module():
    return components_module("push_notifications_discord")


def discord_find_user(s):
    """Discord DM: check the bot token and send to the bot's owner (the
    person who made it in the Developer Portal) unless a user ID is set.
    The bot must share a server with that user."""
    if not s["push_enabled"] or s["provider"] != "discord_dm" or \
            not s["secret"]:
        return
    print("Checking the Discord bot...", flush=True)
    d = discord_module()
    app, why = d.bot_info(s["secret"])
    if app is None:
        raise ConfigError(why + " (Developer Portal > your app > Bot > "
                          "Reset Token, then copy it)")
    if not s["discord_user"]:
        s["discord_user"] = d.owner_id(app)
        if not s["discord_user"]:
            raise ConfigError("Could not find who owns the bot; enter your "
                              "Discord user ID")
        print(f"Discord: messages go to the bot's owner "
              f"({(app.get('owner') or {}).get('username') or s['discord_user']})")
    if d.bot_servers(s["secret"]) == 0:
        raise ConfigError("Add the bot to a server you are in (Discord only "
                          "lets a bot message people it shares a server "
                          f"with): {d.invite_url(app['id'])}")


# The settings the Klipper print-end macros depend on: Klipper only reads
# them at start, so they can't change during a print job
KLIPPER_ROWS = ("complete", "cancelled", "error")
KLIPPER_SETTINGS = (("enabled", "Enable notifications"),
                    ("push_enabled", "Send push notifications"),
                    ("email_enabled", "Send email notifications"),
                    ("webhook_enabled", "Send every notification to a webhook"),
                    ("camera", "Camera for snapshots"))


def klipper_settings_changed(old, new):
    """Which print-end settings differ, as labels for the user."""
    out = [label for key, label in KLIPPER_SETTINGS if old[key] != new[key]]
    for rid in KLIPPER_ROWS:
        for key, what in (("enabled", "push"), ("email", "email"),
                          ("image", "image")):
            if old["rows"][rid][key] != new["rows"][rid][key]:
                out.append(f"{ROW[rid][1]} ({what})")
    return out


def current_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def restart_service(name, script):
    print(f"Restarting {name}...", flush=True)
    try:
        rc = subprocess.call([script, "restart"])
    except OSError as e:
        rc = 1
        print(f"ERROR: could not restart {name}: {e}")
    if rc != 0:
        print(f"ERROR: {name} restart failed (exit code {rc}); it is "
              "retried on the next Save & Apply")
        with open(PENDING_RESTART[name.lower()], "w") as f:
            f.write("restart owed\n")
    else:
        try:
            os.remove(PENDING_RESTART[name.lower()])
        except OSError:
            pass
    return rc


def apprise_check(s):
    """Every address the generated config will hold must be one Apprise
    accepts (as the component would otherwise fail to load with all
    notifications off). Apprise is big: the test cgroup holds it."""
    if not configured(s):
        return
    enter_test_cgroup()
    import apprise
    discord_module()        # registers the Discord senders
    cam = camera_status()[s["camera"]]
    urls = []
    if push_configured(s):
        urls.append(("push service", row_url(s, "complete", "default", cam)))
    if email_configured(s):
        urls.append(("email", email_url(s)))
    if webhook_configured(s):
        urls.append(("webhook", webhook_url(s, "complete", cam)))
    for what, url in urls:
        if not apprise.Apprise().add(url):
            raise ConfigError(f"The {what} settings don't make a valid "
                              "address; check them (server, user name, "
                              "addresses)")


def cmd_save():
    current = load()
    s = validate(read_stdin_json(), current)
    klipper_text = render_klipper(s)
    busy = printer_busy()
    if busy and klipper_text != current_text(KLIPPER_CFG):
        # Nothing that can't take effect now is saved: Klipper only reads
        # its config at start
        changed = klipper_settings_changed(current, s)
        if changed:
            raise ConfigError(
                "The printer is printing. These settings change the "
                "print-end macros, which Klipper only reads at start, so "
                "they can't be applied now: " + ", ".join(changed) +
                ". Nothing was saved; change them back, or save after the "
                "print job.")
        # Same settings, different macro text: the print-end macros on the
        # printer are older than this firmware (kept at boot because the
        # saved settings needed attention)
        raise ConfigError(
            "The printer is printing, and its print-end macros are out of "
            "date (from before this firmware), so Klipper would need a "
            "restart to apply these settings. Nothing was saved; save after "
            "the print job.")
    if s["enabled"]:
        if s["push_enabled"] and s["provider"] == "discord_dm":
            enter_test_cgroup()     # the Discord lookup imports requests
        telegram_find_chat(s)
        discord_find_user(s)
    # Render before storing: a value no URL can be built from is an
    # error now, not a broken config at the next start
    sec = SecretBag()
    try:
        moonraker_text = render_moonraker(s, sec)
    except ValueError as e:
        raise ConfigError(f"Invalid settings: {e}")
    apprise_check(s)
    store(s)
    write_secrets(dict(sec.values, **{k: s[k] for k in SECRET_FIELDS},
                       secret_for=s["secret_for"]))
    print("Settings saved.")
    if push_configured(s) and s["provider"] == "gotify":
        print("WARNING: Text only: picture attachments not supported by "
              "Gotify.")
    if configured(s) and not camera_status()[s["camera"]]:
        print(f"WARNING: the {s['camera']} camera has no snapshot service "
              "running; notifications will be sent without camera images.")
    # Services only read their config at start, so writing now is safe
    # even mid-print; the service hooks re-render the same content later.
    restart = []
    if write_generated(MOONRAKER_CFG, moonraker_text) or \
            os.path.exists(PENDING_RESTART["moonraker"]):
        restart.append(("Moonraker", os.path.join(INIT_D, "S61moonraker")))
    if write_generated(KLIPPER_CFG, klipper_text) or \
            os.path.exists(PENDING_RESTART["klipper"]):
        restart.append(("Klipper", os.path.join(INIT_D, "S60klipper")))
    if not restart:
        print("No configuration change; nothing to restart.")
        return 0
    if busy:
        if any(n == "Klipper" for n, _ in restart):
            # Changes to the macros were refused above, so this is a
            # restart an earlier save still owes (it failed or was skipped):
            # Klipper stays as it is until the print job is done
            restart = [r for r in restart if r[0] != "Klipper"]
            with open(PENDING_RESTART["klipper"], "w") as f:
                f.write("restart owed\n")
            print("Klipper restart still owed from an earlier save; it's "
                  "done on the next Save & Apply once the print job is "
                  "done.")
            if not restart:
                return 0
        # The print job doesn't depend on Moonraker: Klipper runs the file
        print("The printer is printing: Moonraker restarts now (the print "
              "job carries on; Fluidd reconnects in a moment).")
    # Klipper first: Moonraker reconnects to it
    rc = 0
    for name, script in reversed(restart):
        rc = restart_service(name, script) or rc
    if rc == 0:
        print("Notifications applied.")
    return rc


def fetch_snapshot(s):
    """Snapshot to a temp file, or None (sent without image)."""
    if not camera_status()[s["camera"]]:
        print(f"The {s['camera']} camera has no snapshot service; sending "
              "without an image")
        return None
    url = SNAPSHOT_HOST + SNAPSHOT_PATH[s["camera"]]
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read(5 * 1024 * 1024 + 1)
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("not a JPEG image")
    except Exception as e:
        print(f"Camera snapshot unavailable ({e}); sending without an image")
        return None
    # Named like the notifications' pictures: time, printer, "test"
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    printer = re.sub(r"[^A-Za-z0-9._-]+", "-", s["name"]).strip("-.")[:32]
    path = os.path.join(tempfile.mkdtemp(prefix="notifications_test_"),
                        "_".join(p for p in (stamp, printer, "test") if p)
                        + ".jpg")
    with open(path, "wb") as f:
        f.write(data)
    print(f"Attaching camera snapshot ({s['camera']})")
    return path


# Firmware Config runs in a small memory cgroup (ext/firmware-config, 48M)
# that Apprise doesn't fit in; the test runs in its own.
TEST_CGROUP = "/sys/fs/cgroup/ext/notifications-test"
TEST_MEMORY_MAX = "128M"


def enter_test_cgroup():
    try:
        os.makedirs(TEST_CGROUP, exist_ok=True)
        with open(os.path.join(TEST_CGROUP, "memory.max"), "w") as f:
            f.write(TEST_MEMORY_MAX)
        with open(os.path.join(TEST_CGROUP, "cgroup.procs"), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass  # no cgroup v2 (e.g. off the printer): run where we are


# Apprise debug lines that say nothing about why a send failed
DEBUG_NOISE = ("Language set", "Plugin(s)", "Threaded notification",
               "Preparing", "Loading attachment", "Throttling",
               "completed in")


class _Collector:
    """Keeps Apprise's debug lines, to show them only if the test fails."""

    def __init__(self, secrets):
        self.lines = []
        self.secrets = [x for x in secrets if x]

    def redact(self, text):
        for x in self.secrets:
            text = text.replace(x, "***")
            text = text.replace(quote(x, safe=""), "***")
        return text


def print_email_hint(lines):
    text = "\n".join(lines)
    if "Unauthorized IP" in text or "Unauthorised IP" in text:
        print("The mail server doesn't accept connections from this "
              "network's IP address. On Brevo, allow it, or turn off IP "
              "blocking, under Security > Authorised IPs "
              "(https://app.brevo.com/security/authorised_ips).")
    elif "(535" in text or "(534" in text:
        print("The mail server rejected the username or password. Gmail "
              "needs an app password; Brevo needs an SMTP key (not your "
              "account password).")


def cmd_test():
    # Firmware Config streams this output: print each step as it happens
    sys.stdout.reconfigure(line_buffering=True)
    enter_test_cgroup()
    data = read_stdin_json()
    if not isinstance(data, dict):
        raise ConfigError("Invalid request")
    data["enabled"] = True
    s = validate(data, load())
    telegram_find_chat(s)
    discord_find_user(s)
    print("Loading the notification library...")
    import time
    t0 = time.monotonic()
    import logging
    import apprise
    import base64
    secrets = [s["secret"], s["pushover_user"], s["email_password"],
               s["webhook_url"]]
    m = RX_DISCORD_WEBHOOK.fullmatch(s["secret"])
    if m:
        secrets.append(m.group(2))
    if s["provider"] in ("discord", "discord_dm"):
        discord_module()        # registers the Discord senders
    secrets.append(base64.b64encode(
        f"{s['username']}:{s['secret']}".encode()).decode())
    secrets.append(base64.b64encode(
        f"\0{s['email_username']}\0{s['email_password']}".encode()).decode())
    col = _Collector(secrets)

    class Live(logging.Handler):
        def emit(self, record):
            msg = col.redact(record.getMessage())
            if record.levelno >= logging.INFO:
                print(f"{record.levelname}: {msg}", flush=True)
            elif not any(n in msg for n in DEBUG_NOISE):
                col.lines.append(msg)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(Live())
    cam = camera_status()[s["camera"]]
    title = f"🔔 {s['name']} test"
    body = "Test notification from Firmware Config."
    dests = []
    if push_configured(s):
        target = {"ntfy": f"{s['server']}/{s['topic']}",
                  "gotify": s["gotify_server"],
                  "pushover": "Pushover",
                  "telegram": f"Telegram chat {s['telegram_chat']}",
                  "discord": "the Discord channel",
                  "discord_dm": f"Discord user {s['discord_user']}"}[
                      s["provider"]]
        text = body
        if s["provider"] == "telegram":
            import html
            text = body + "\n \n" + " · ".join(
                f'<a href="{html.escape(u)}">{html.escape(t)}</a>'
                for t, u in links(s, "test", cam))
        elif s["provider"] in ("discord", "discord_dm"):
            d = discord_module()
            text = body + "\n\n" + " · ".join(
                d.link(t, u) for t, u in links(s, "test", cam))
        elif s["provider"] == "gotify":
            text = body + "\n\n" + " · ".join(
                f"[{t}]({u})" for t, u in links(s, "test", cam))
        # Max priority: the test must come through however the phone's
        # notification settings treat lower priorities
        dests.append((target, row_url(s, "test", "max", cam),
                      text.strip()))
    if email_configured(s):
        dests.append((f"email ({s['email_to']}) via {s['email_host']}",
                      email_url(s), None))
    if webhook_configured(s):
        dests.append(("the webhook", webhook_url(s, "test", cam), body))
    if not dests:
        raise ConfigError("Set up a push service, email or a webhook first")
    if push_configured(s) and s["provider"] == "gotify":
        print("WARNING: Text only: picture attachments not supported by "
              "Gotify.")
    print("Taking a camera snapshot...")
    path = fetch_snapshot(s)
    for n, (target, url, text) in enumerate(dests):
        if text is None:        # email: the same HTML card as notifications
            import datetime
            em = components_module("push_notifications_email")
            dests[n] = (target, url, em.render(
                title, body, links(s, "test", cam), "info",
                os.path.basename(path) if path else None,
                datetime.datetime.now(), s["name"]))
    failed = 0
    try:
        for target, url, text in dests:
            ap = apprise.Apprise()
            if not ap.add(url):
                print(f"ERROR: could not build a notification URL for "
                      f"{target}")
                failed += 1
                continue
            # Same timeouts as real notifications: a slow link (e.g. a
            # relayed Tailscale connection) needs time to upload the image
            for srv in ap:
                srv.socket_connect_timeout = max(srv.socket_connect_timeout,
                                                 SEND_CONNECT_TIMEOUT)
                srv.socket_read_timeout = max(srv.socket_read_timeout,
                                              SEND_READ_TIMEOUT)
            col.lines = []
            print(f"\nSending to {target}...", flush=True)
            # Gotify can't carry attachments: text only
            ok = ap.notify(title=title, body=text,
                           attach=None if url.startswith("gotify") else path)
            if not ok and path:
                print("Sending failed; retrying without the image",
                      flush=True)
                if text.startswith("<!doctype html>"):
                    retry = components_module(
                        "push_notifications_email").without_image(text)
                else:
                    retry = text + "\n(image could not be attached)"
                ok = ap.notify(title=title, body=retry)
            if ok:
                print(f"Sent to {target} ({time.monotonic() - t0:.0f} s).")
            else:
                failed += 1
                print("Details:")
                for line in col.lines[-30:]:
                    print(f"  {line}")
                print(f"Not delivered to {target}.")
                if url.startswith("mailto"):
                    print_email_hint(col.lines)
    finally:
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
    if failed:
        print(f"\nERROR: {failed} of {len(dests)} test notifications were "
              "not delivered.")
        return 1
    print("\nTest notification sent.")
    return 0


def cmd_render(path, renderer):
    """Service start: regenerate from the settings. Invalid settings keep
    the last generated file (Firmware Config shows the error)."""
    try:
        s = validate({}, load())
    except ConfigError as e:
        print(f"notifications: invalid [notifications] settings ({e}); "
              "keeping the last generated config", file=sys.stderr)
        return 0
    service = "moonraker" if path == MOONRAKER_CFG else "klipper"
    try:
        os.remove(PENDING_RESTART[service])    # this start is it
    except OSError:
        pass
    try:
        if service == "moonraker":
            sec = SecretBag()
            text = renderer(s, sec)
            write_secrets(dict(sec.values, **{k: s[k] for k in SECRET_FIELDS},
                               secret_for=s["secret_for"]))
            if any(read_cfg()[SECTION].get(k) for k in SECRET_FIELDS
                   if read_cfg().has_section(SECTION)):
                store(s)    # older settings: move secrets out of the cfg
        else:
            text = renderer(s)
    except (ValueError, OSError) as e:
        print(f"notifications: could not generate {path} ({e}); keeping "
              "the last one", file=sys.stderr)
        return 0
    if write_generated(path, text):
        print(f"Updated {path}")
    return 0


def main(argv):
    cmds = {
        "get": cmd_get,
        "status": cmd_status,
        "save": cmd_save,
        "test": cmd_test,
        "render-moonraker": lambda: cmd_render(MOONRAKER_CFG,
                                               render_moonraker),
        "render-klipper": lambda: cmd_render(KLIPPER_CFG, render_klipper),
    }
    if len(argv) != 2 or argv[1] not in cmds:
        print(f"Usage: {argv[0]} {{{'|'.join(cmds)}}}", file=sys.stderr)
        return 2
    try:
        return cmds[argv[1]]() or 0
    except ConfigError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
