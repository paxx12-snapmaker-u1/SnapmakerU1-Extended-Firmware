# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @noon92

# Discord senders for push_notifications, registered as Apprise plugins so
# they get the same queues, retries and fallbacks as the other services:
#
#   discordhook://<webhook id>/<webhook token>?name=U1&ping=<mention>
#       A server channel, through a channel webhook.
#   discorddm://<bot token>@<user id>
#
# silent=yes sends without a phone notification sound (Discord's @silent).
#       A direct message from the user's own bot. Discord only lets a bot
#       message someone it shares a server with.
#
# Each notification is one embed: the title, the text (Markdown), a colour
# from the notification type, and the image inside the embed.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests
from apprise.decorators import notify

API = "https://discord.com/api/v10"
USER_AGENT = ("DiscordBot (https://github.com/paxx12-snapmaker-u1/"
              "SnapmakerU1-Extended-Firmware, 1)")
TIMEOUT = (10., 60.)      # connect, read: same as push_notifications
# Embed bar colours, matching the spool pictures
COLOURS = {"info": 0x5F6269, "success": 0x24743A, "warning": 0xC46800,
           "failure": 0xB02626}
TITLE_MAX = 256
TEXT_MAX = 4096
# Error codes: https://discord.com/developers/docs/topics/opcodes-and-status-codes
CANNOT_DM = 50007
UNKNOWN_WEBHOOK = 10015
SUPPRESS_NOTIFICATIONS = 1 << 12

_dm_channels: Dict[Tuple[str, str], str] = {}
log = logging.getLogger("apprise")


def escape(text: str) -> str:
    """Markdown special characters shown as typed (file names, spool
    names)."""
    for c in "\\*_~`|>[]#":
        text = text.replace(c, "\\" + c)
    return text


def link(name: str, url: str) -> str:
    return f"[{escape(name)}]({url.replace(')', '%29').replace(' ', '%20')})"


def _why(e: Exception) -> str:
    """A failed request, without its URL: requests puts the URL (which
    holds the webhook token) in the exception's text, so only the kind of
    failure is logged."""
    if isinstance(e, requests.Timeout):
        return "timed out"
    if isinstance(e, requests.ConnectionError):
        return "connection failed"
    return type(e).__name__


def _error(r: requests.Response) -> Tuple[int, str]:
    try:
        data = r.json()
        return int(data.get("code") or 0), str(data.get("message") or "")
    except (ValueError, AttributeError, TypeError):
        return 0, (r.text or "")[:200]


def _post(url: str, headers: Dict[str, str], payload: Dict[str, Any],
          attach: Any) -> requests.Response:
    """Post a message, with the first attachment shown inside the embed."""
    path = None
    for a in attach or []:
        path = a.path if hasattr(a, "path") else str(a)
        if path and os.path.exists(path):
            break
        path = None
    headers = dict(headers, **{"User-Agent": USER_AGENT})
    if not path:
        return requests.post(url, headers=headers, json=payload,
                             timeout=TIMEOUT)
    name = "image" + (os.path.splitext(path)[1].lower() or ".jpg")
    payload["embeds"][0]["image"] = {"url": f"attachment://{name}"}
    payload["attachments"] = [{"id": 0, "filename": name}]
    with open(path, "rb") as f:
        return requests.post(
            url, headers=headers, timeout=TIMEOUT,
            data={"payload_json": json.dumps(payload)},
            files={"files[0]": (name, f, "image/jpeg")})


def _payload(body: str, title: str, notify_type: Any, qsd: Dict[str, str],
             content: str = "") -> Dict[str, Any]:
    embed = {"title": title[:TITLE_MAX], "description": body[:TEXT_MAX],
             "color": COLOURS.get(str(notify_type), COLOURS["info"])}
    payload: Dict[str, Any] = {"embeds": [embed]}
    if qsd.get("silent") == "yes":
        payload["flags"] = SUPPRESS_NOTIFICATIONS
    if content:
        payload["content"] = content
        # Only the mention in the settings may ping
        payload["allowed_mentions"] = {"parse": ["everyone", "roles",
                                                 "users"]}
    else:
        payload["allowed_mentions"] = {"parse": []}
    return payload


def mention(value: str) -> str:
    """Settings value to a Discord mention: a user ID, role:ID,
    everyone or here."""
    v = (value or "").strip()
    if v in ("everyone", "here"):
        return "@" + v
    if v.startswith("role:"):
        return f"<@&{v[5:]}>"
    return f"<@{v}>" if v else ""


@notify(on="discordhook", name="Discord channel")
def send_webhook(body: str, title: str, notify_type: Any, attach: Any,
                 meta: Dict[str, Any], *args: Any, **kwargs: Any) -> bool:
    hook_id = meta.get("host") or ""
    token = (meta.get("fullpath") or meta.get("path") or "").strip("/")
    qsd = meta.get("qsd") or {}
    payload = _payload(body, title, notify_type, qsd,
                       mention(qsd.get("ping", "")))
    if qsd.get("name"):
        payload["username"] = qsd["name"][:80]
    try:
        r = _post(f"{API}/webhooks/{hook_id}/{token}?wait=true", {},
                  payload, attach)
    except requests.RequestException as e:
        log.warning("Discord: could not connect (%s)", _why(e))
        return False
    if r.status_code in (200, 204):
        return True
    code, msg = _error(r)
    if code == UNKNOWN_WEBHOOK or r.status_code == 401:
        log.warning("Discord: the webhook no longer exists or the URL is "
                    "wrong; copy it again from the channel's Integrations "
                    "settings")
    else:
        log.warning("Discord: HTTP %d, %s", r.status_code, msg or "error")
    return False


def bot_info(token: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The bot's application (id, owner), or (None, reason)."""
    try:
        r = requests.get(f"{API}/applications/@me", timeout=TIMEOUT,
                         headers={"Authorization": f"Bot {token}",
                                  "User-Agent": USER_AGENT})
    except requests.RequestException as e:
        return None, f"could not connect to Discord ({_why(e)})"
    if r.status_code == 401:
        return None, "Discord doesn't accept this bot token"
    if r.status_code != 200:
        return None, "Discord: HTTP %d, %s" % (r.status_code, _error(r)[1])
    return r.json(), ""


def owner_id(app: Dict[str, Any]) -> str:
    """The user who made the bot (a team's owner, for a team's bot)."""
    team = app.get("team") or {}
    if team.get("owner_user_id"):
        return str(team["owner_user_id"])
    return str((app.get("owner") or {}).get("id") or "")


def bot_servers(token: str) -> Optional[int]:
    """How many servers the bot is in (None if unknown)."""
    try:
        r = requests.get(f"{API}/users/@me/guilds", timeout=TIMEOUT,
                         headers={"Authorization": f"Bot {token}",
                                  "User-Agent": USER_AGENT})
        return len(r.json()) if r.status_code == 200 else None
    except (requests.RequestException, ValueError, TypeError):
        return None


def invite_url(app_id: str) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            "&scope=bot&permissions=0")


@notify(on="discorddm", name="Discord DM")
def send_dm(body: str, title: str, notify_type: Any, attach: Any,
            meta: Dict[str, Any], *args: Any, **kwargs: Any) -> bool:
    token = meta.get("user") or ""
    user_id = meta.get("host") or ""
    auth = {"Authorization": f"Bot {token}"}
    try:
        channel = _dm_channels.get((token, user_id))
        if channel is None:
            r = requests.post(f"{API}/users/@me/channels", timeout=TIMEOUT,
                              headers=dict(auth, **{"User-Agent": USER_AGENT}),
                              json={"recipient_id": user_id})
            if r.status_code != 200:
                code, msg = _error(r)
                if r.status_code == 401:
                    log.warning("Discord: the bot token is wrong or was "
                                "reset; copy a new one from the Developer "
                                "Portal (Bot > Reset Token)")
                else:
                    log.warning("Discord: could not open the DM: HTTP %d, %s",
                                r.status_code, msg or "error")
                return False
            channel = str(r.json()["id"])
            _dm_channels[(token, user_id)] = channel
        r = _post(f"{API}/channels/{channel}/messages", auth,
                  _payload(body, title, notify_type, meta.get("qsd") or {}),
                  attach)
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("Discord: could not send (%s)", _why(e))
        return False
    if r.status_code == 200:
        return True
    code, msg = _error(r)
    if code == CANNOT_DM:
        app, _ = bot_info(token)
        how = (f" Add the bot to a server you are in: "
               f"{invite_url(app['id'])}" if app else "")
        log.warning("Discord: the bot can't message you. Discord only "
                    "allows it when you and the bot share a server, and "
                    "your Privacy settings allow DMs from members of that "
                    "server.%s", how)
    else:
        log.warning("Discord: HTTP %d, %s", r.status_code, msg or "error")
    return False
