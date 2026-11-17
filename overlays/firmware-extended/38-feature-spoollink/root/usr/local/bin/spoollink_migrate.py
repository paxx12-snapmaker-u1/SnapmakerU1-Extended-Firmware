#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

"""Move SpoolLink card links between Spoolman's two tag stores.

Stdlib only, so it runs on the U1's stock Python.

    spoollink_migrate.py --host URL native     card_uids -> native tags
    spoollink_migrate.py --host URL card_uids  native tags -> card_uids

Every link is moved, then removed from the store it came from, so only one
store holds links afterwards. A UID that cannot be moved stays where it was and
is reported; running the migration again retries it. Native tags need
Spoolman v0.27.0 or newer.

This script only moves the links. Setting `[spoolman] tag_store` and
restarting Moonraker are done by the caller (the settings-YAML cmd), with
Moonraker stopped so SpoolLink writes nothing meanwhile.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

TAG_API_MIN_VERSION = (0, 27, 0)


def log(msg):
    print(msg, flush=True)


class Spoolman:
    def __init__(self, host):
        self.base = host.rstrip("/") + "/api/v1"

    def request(self, method, path, body=None, ok=(200, 201, 204)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                code, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        payload = json.loads(raw) if raw else None
        if code not in ok:
            raise RuntimeError(f"{method} {path}: HTTP {code} {raw.decode(errors='replace')}")
        return code, payload

    def version(self):
        return str(self.request("GET", "/info")[1].get("version") or "")

    def spools(self):
        return self.request("GET", "/spool?limit=1000&allow_archived=true")[1]

    def set_card_uids(self, spool_id, uids):
        self.request("PATCH", f"/spool/{spool_id}",
                     {"extra": {"card_uids": json.dumps(",".join(uids))}})

    # Returns the ID of another spool already holding the tag, else None.
    def link_tag(self, spool_id, uid):
        code, payload = self.request(
            "POST", f"/spool/{spool_id}/tag", {"uid": uid}, ok=(200, 201, 409))
        if code == 409:
            holder = (payload or {}).get("spool_id")
            if holder is None or holder == spool_id:
                raise RuntimeError(f"tag {uid}: HTTP 409 {payload}")
            return holder
        return None

    def unlink_tag(self, spool_id, uid):
        self.request("DELETE", f"/spool/{spool_id}/tag/{uid}", ok=(200, 204, 404))

    def ensure_card_uids_field(self):
        fields = self.request("GET", "/field/spool")[1]
        if any(f.get("key") == "card_uids" for f in fields):
            return
        self.request("POST", "/field/spool/card_uids", {
            "name": "Card UIDs", "field_type": "text", "order": 1,
            "default_value": json.dumps(""),
        })


def parse_card_uids(spool):
    raw = ((spool.get("extra") or {}).get("card_uids") or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = raw[1:-1]
    seen = []
    for uid in (u.strip().upper() for u in raw.split(",")):
        if uid and uid not in seen:
            seen.append(uid)
    return seen


def parse_tag_uids(spool):
    return [str(t["uid"]).upper() for t in spool.get("tags") or [] if t.get("uid")]


def check_native(sm):
    version = sm.version()
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m or tuple(int(g) for g in m.groups()) < TAG_API_MIN_VERSION:
        raise RuntimeError(
            f"Spoolman {version or '?'} has no native tags; v0.27.0 or newer is required")


# Links every `card_uids` entry as a native tag, then drops it from the field.
# A UID listed on several spools is kept for the user to resolve; one already
# tagged on another spool is dropped, as that tag is the newer link.
def to_native(sm):
    check_native(sm)
    spools = sm.spools()
    owners = {}
    for spool in spools:
        for uid in parse_card_uids(spool):
            owners.setdefault(uid, []).append(spool["id"])
    moved = failed = 0
    for spool in spools:
        uids = parse_card_uids(spool)
        if not uids:
            continue
        keep = []
        for uid in uids:
            if len(owners[uid]) > 1:
                log(f"WARN: card {uid} is on spools "
                    f"{', '.join(f'#{i}' for i in owners[uid])}, not migrated")
                keep.append(uid)
                failed += 1
                continue
            if uid not in parse_tag_uids(spool):
                try:
                    holder = sm.link_tag(spool["id"], uid)
                except Exception as e:
                    log(f"WARN: card {uid} on spool #{spool['id']}: {e}")
                    keep.append(uid)
                    failed += 1
                    continue
                if holder is not None:
                    log(f"card {uid} on spool #{spool['id']} is already tagged "
                        f"on spool #{holder}, dropping it")
                    continue
            moved += 1
        if keep != uids:
            sm.set_card_uids(spool["id"], keep)
    return moved, failed


# Copies every spool's native tags into `card_uids`, then unlinks them. Any
# other spool still listing the UID in `card_uids` has it removed, as the tag
# was the newer link.
def to_card_uids(sm):
    try:
        check_native(sm)
    except RuntimeError:
        return 0, 0  # an older server cannot hold native tags
    sm.ensure_card_uids_field()
    spools = sm.spools()
    tagged = {uid: spool["id"] for spool in spools for uid in parse_tag_uids(spool)}
    moved = failed = 0
    for spool in spools:
        uids = parse_card_uids(spool)
        keep = [u for u in uids if tagged.get(u, spool["id"]) == spool["id"]]
        for uid in uids:
            if uid not in keep:
                log(f"card {uid} on spool #{spool['id']} is tagged on spool "
                    f"#{tagged[uid]}, dropping it")
        tags = [u for u in parse_tag_uids(spool) if u not in keep]
        if keep + tags != uids:
            sm.set_card_uids(spool["id"], keep + tags)
        for uid in parse_tag_uids(spool):
            try:
                sm.unlink_tag(spool["id"], uid)
                moved += 1
            except Exception as e:
                log(f"WARN: unlinking tag {uid} from spool #{spool['id']}: {e}")
                failed += 1
    return moved, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True, help="Spoolman URL")
    ap.add_argument("store", choices=["native", "card_uids"],
                    help="store to move every link into")
    args = ap.parse_args()
    sm = Spoolman(args.host)
    try:
        moved, failed = (to_native if args.store == "native" else to_card_uids)(sm)
    except Exception as e:
        log(f"ERROR: {e}")
        return 1
    target = "native tags" if args.store == "native" else "card_uids"
    log(f"Moved {moved} card(s) to {target}"
        + (f"; {failed} left in place, see above" if failed else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
