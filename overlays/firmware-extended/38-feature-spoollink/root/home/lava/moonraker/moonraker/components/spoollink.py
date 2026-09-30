# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

# SpoolLink — bridge between Spoolman and the Snapmaker AFC/RFID stack.
#
# Runs inside Moonraker as a component. It registers the
# `spoollink_resolve_spool` remote method for the Klipper `[spoollink]`
# router, resolves scanned cards (or explicit spool IDs) against the
# Spoolman REST API, binds card UIDs to spools, keeps Moonraker's active
# spool in sync with the toolhead, and pushes the resolved filament info
# back into Klipper via the `spoollink/set` endpoint.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient, HttpResponse
    from .klippy_apis import KlippyAPI as APIComp

RESOLVE_METHOD = "spoollink_resolve_spool"
SET_ENDPOINT = "spoollink/set"


def _unquote(value: str) -> str:
    s = str(value).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _normalize_uid(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, (list, tuple)):
        if all(b == 0 for b in val):
            return ""
        return "".join(f"{b:02X}" for b in val)
    s = str(val).strip()
    s = _unquote(s)
    return re.sub(r'["\':\s\-_]', "", s).upper()


def _is_cascade_uid(norm: str) -> bool:
    # 4 bytes (8 hex characters) starting with 88 is an ISO14443-A Cascade Tag (CT) fragment
    return len(norm) == 8 and norm.startswith("88")


def _extract_primary_uids(spool: dict) -> Set[str]:
    uids: Set[str] = set()
    for k in ("rfid_uid", "rfid_uid_2"):
        val = spool.get(k)
        if val:
            norm = _normalize_uid(val)
            if norm:
                uids.add(norm)
    for field in ("custom_fields", "extra"):
        custom = spool.get(field) or {}
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except Exception:
                custom = {}
        if isinstance(custom, dict):
            for k in ("rfid_uid", "rfid_uid_2"):
                val = custom.get(k)
                if isinstance(val, str):
                    for part in _unquote(val).split(","):
                        norm = _normalize_uid(part)
                        if norm:
                            uids.add(norm)
    return uids

def _extract_secondary_uids(spool: dict) -> Set[str]:
    uids: Set[str] = set()
    for k in ("bambu_tray_uid", "card_uids"):
        val = spool.get(k)
        if val:
            norm = _normalize_uid(val)
            if norm:
                uids.add(norm)
    
    for field in ("custom_fields", "extra"):
        custom = spool.get(field) or {}
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except Exception:
                custom = {}
        if isinstance(custom, dict):
            for k, v in custom.items():
                if k in ("card_uids", "previous_tag", "bambu_tray_uid", "tray_uid", "nfc_id", "tag"):
                    if isinstance(v, str):
                        for part in _unquote(v).split(","):
                            norm = _normalize_uid(part)
                            if norm:
                                uids.add(norm)
    return uids


def _parse_card_uids(spool: dict) -> List[str]:
    return list(_extract_all_uids(spool))


def _parse_variant(vendor: str, filament: dict) -> str:
    extra = filament.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    variant = _unquote(extra.get("variant") or "")
    if not variant:
        custom = filament.get("custom_fields") or {}
        if isinstance(custom, str):
            try:
                custom = json.loads(custom)
            except Exception:
                custom = {}
        if isinstance(custom, dict):
            variant = _unquote(custom.get("variant") or "")
    if not variant and filament.get("material_subgroup"):
        sub = filament.get("material_subgroup", "")
        variant = sub.capitalize() if sub.lower() != "basic" else ""
    if variant:
        return variant
    return "Basic" if vendor.lower() == "snapmaker" else ""


class SpoolLink:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        url = config.get("server").strip().rstrip("/")
        if "://" not in url:
            url = "http://" + url
        self._spoolman_url = url
        self._api_key: Optional[str] = config.get("api_key", None)
        self._is_filaman: bool = bool(self._api_key or "filaman" in url.lower())
        self._last_unresolved: Dict[int, str] = {}
        self._channel_card_uids: Dict[int, str] = {}
        self._cache_dir: Optional[str] = config.get("cache_dir", None)
        self._force_generic_vendor = config.getboolean(
            "force_generic_vendor", False)
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")

        self._channel_event_times: Dict[int, float] = {}
        self._toolhead_extruder: str = "extruder"
        self._ptc_spool_ids: List[int] = []
        self._active_spool_id: Optional[int] = None
        self._printer_state: str = "standby"

        self.server.register_remote_method(RESOLVE_METHOD, self._resolve_spool)
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_klippy_disconnect)
        self.server.register_event_handler(
            "spoolman:active_spool_set", self._handle_active_spool_set)

    async def component_init(self) -> None:
        logging.info(
            "spoollink starting (server: %s, is_filaman: %s, cache: %s, "
            "force generic vendor: %s)",
            self._spoolman_url, self._is_filaman, self._cache_dir or "disabled",
            self._force_generic_vendor)
        if not self._is_filaman:
            await self._ensure_fields()

    # -- Klippy lifecycle ---------------------------------------------------

    async def _handle_klippy_ready(self) -> None:
        logging.info("[spoollink] Klippy ready, subscribing to objects")
        status = await self.klippy_apis.subscribe_objects({
            "filament_detect": None,
            "print_task_config": ["filament_spool_id"],
            "toolhead": ["extruder"],
            "print_stats": ["state"],
        }, self._handle_status_update, {})
        self._handle_status_update(status, 0.)

    def _handle_klippy_disconnect(self) -> None:
        logging.info("[spoollink] Klippy disconnected")
        self._channel_event_times = {}
        self._channel_card_uids = {}
        self._last_unresolved = {}
        self._ptc_spool_ids = []
        self._active_spool_id = None
        self._printer_state = "standby"

    def _is_printing(self) -> bool:
        return self._printer_state.lower() in ("printing", "paused")

    # -- Remote method / subscription callbacks -----------------------------

    def _handle_active_spool_set(self, payload: Dict[str, Any]) -> None:
        spool_id = payload.get("spool_id")
        if spool_id != self._active_spool_id:
            self._active_spool_id = spool_id
            logging.info("[spoollink] active spool received: spool_id=%s", spool_id)
            self._fire(self._sync_active_spool())

    def _handle_status_update(self, status: Dict[str, Any], eventtime: float) -> None:
        ps = status.get("print_stats")
        if ps is not None:
            state = ps.get("state")
            if state is not None and state != self._printer_state:
                logging.info("[spoollink] printer state: %s → %s",
                             self._printer_state, state)
                self._printer_state = state

        th = status.get("toolhead")
        if th is not None:
            extruder = th.get("extruder")
            if extruder is not None and extruder != self._toolhead_extruder:
                logging.info("[spoollink] toolhead extruder changed: %s → %s",
                             self._toolhead_extruder, extruder)
                self._toolhead_extruder = extruder
                self._fire(self._sync_active_spool())

        ptc = status.get("print_task_config")
        if ptc is not None:
            spool_ids = ptc.get("filament_spool_id")
            new_ids = list(spool_ids or [])
            if new_ids != self._ptc_spool_ids:
                logging.info("[spoollink] spool_ids changed: %s → %s",
                             self._ptc_spool_ids, new_ids)
                self._ptc_spool_ids = new_ids
                self._fire(self._sync_active_spool())

        fd = status.get("filament_detect")
        if fd is None:
            return
        info_list = fd.get("info", [])
        for ch, info in enumerate(info_list):
            self._handle_filament_detect_channel(ch, info)

    def _handle_filament_detect_channel(self, ch: int, info: Any) -> None:
        if not isinstance(info, dict):
            return
        event_time = info.get("CARD_EVENT_TIME") or 0
        if event_time <= self._channel_event_times.get(ch, 0):
            return
        self._channel_event_times[ch] = event_time
        uid_hex = self._uid_to_hex(info.get("CARD_UID"))
        tray_uid = info.get("TRAY_UID") or ""

        if not uid_hex:
            # Card removed or cleared
            self._channel_card_uids.pop(ch, None)
            self._last_unresolved.pop(ch, None)
            return

        # Ignore invalid truncated ISO14443-A cascade tag fragments (e.g. 8804A27C)
        if _is_cascade_uid(uid_hex):
            logging.warning("[spoollink] ch%d: ignoring truncated ISO14443-A cascade UID %s",
                            ch, uid_hex)
            return

        # Suppress duplicate resolutions and console error spam
        if uid_hex == self._channel_card_uids.get(ch):
            if self._ptc_spool_ids and ch < len(self._ptc_spool_ids) and self._ptc_spool_ids[ch] != 0:
                return
            if self._last_unresolved.get(ch) == uid_hex:
                return

        self._channel_card_uids[ch] = uid_hex

        logging.info("[spoollink] ch%d: detected at %.3f (card %s), resolving",
                     ch, event_time, uid_hex)
        self._fire(self._resolve_spool(ch, card_uid=uid_hex, tray_uid=tray_uid))

    # -- Active spool sync --------------------------------------------------

    @staticmethod
    def _uid_to_hex(uid_raw: Any) -> str:
        if not uid_raw:
            return ""
        if isinstance(uid_raw, (list, tuple)):
            if all(b == 0 for b in uid_raw):
                return ""
            return "".join(f"{b:02X}" for b in uid_raw)
        return ""

    @staticmethod
    def _extruder_to_channel(extruder: str) -> int:
        if extruder == "extruder":
            return 0
        try:
            return int(extruder.replace("extruder", ""))
        except ValueError:
            return 0

    async def _sync_active_spool(self) -> None:
        channel = self._extruder_to_channel(self._toolhead_extruder)
        spool_id = (self._ptc_spool_ids[channel]
                    if channel < len(self._ptc_spool_ids) else 0) or 0
        if spool_id == self._active_spool_id:
            return
        logging.info("[spoollink] set active spool: channel=%d spool_id=%s → %s",
                     channel, self._active_spool_id, spool_id)
        self._active_spool_id = spool_id
        spoolman = self.server.lookup_component("spoolman", None)
        if spoolman is None:
            return
        try:
            spoolman.set_active_spool(spool_id or None)
        except Exception:
            self._active_spool_id = None
            raise

    # -- Klipper push -------------------------------------------------------

    async def _spoollink_set(self, channel: int, message: str,
                             info: Optional[dict] = None,
                             status: str = "ok") -> Optional[dict]:
        params: Dict[str, Any] = {
            "channel": channel, "message": message, "status": status}
        if info is not None:
            params["info"] = info
        try:
            return await self.klippy_apis._send_klippy_request(SET_ENDPOINT, params)
        except self.server.error as e:
            logging.error("[spoollink] ch%d: %s failed: %s", channel, SET_ENDPOINT, e)
            await self.klippy_apis.run_gcode(
                'RESPOND TYPE=error MSG="SpoolLink: Spoolman integration '
                'appears disabled on the printer — re-enable it via '
                'firmware-config and reboot"', None)
            return None

    # -- Task helpers -------------------------------------------------------

    def _fire(self, coro) -> "asyncio.Future":
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._task_done)
        return task

    @staticmethod
    def _task_done(task: "asyncio.Future") -> None:
        if not task.cancelled() and task.exception() is not None:
            logging.error("[spoollink] background task failed: %s",
                          task.exception(), exc_info=task.exception())

    async def _retry(self, fn, *args, retries=3, **kwargs):
        delay = 1.0
        for attempt in range(retries + 1):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                if attempt == retries:
                    raise
                logging.debug("[spoollink] attempt %d/%d failed: %s",
                              attempt + 1, retries, e)
                await asyncio.sleep(delay)
                delay *= 2

    # -- Local cache --------------------------------------------------------

    def _cache_path(self, card_uid: str) -> Optional[str]:
        if not self._cache_dir:
            return None
        return os.path.join(self._cache_dir, f"{_normalize_uid(card_uid)}.json")

    def _load_cache(self, card_uid: str) -> Optional[dict]:
        path = self._cache_path(card_uid)
        if not path:
            return None
        try:
            with open(path) as f:
                spool = json.load(f)
            logging.info("[spoollink] cache hit for card %s (spool %s)",
                         card_uid, spool.get("id"))
            return spool
        except FileNotFoundError:
            return None
        except Exception as e:
            logging.warning("[spoollink] cache read failed for %s: %s", card_uid, e)
            return None

    def _save_cache(self, card_uid: str, spool: dict) -> None:
        path = self._cache_path(card_uid)
        if not path:
            return
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(path, "w") as f:
                json.dump(spool, f)
            logging.info("[spoollink] cached spool %s for card %s",
                         spool.get("id"), card_uid)
        except Exception as e:
            logging.warning("[spoollink] cache write failed for %s: %s", card_uid, e)

    def _delete_cache(self, card_uid: str) -> None:
        path = self._cache_path(card_uid)
        if not path:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.warning("[spoollink] cache delete failed for %s: %s", card_uid, e)

    # -- FilaMan / Spoolman REST --------------------------------------------

    async def _ensure_fields(self) -> None:
        await self._ensure_field("spool", "card_uids", "Card UIDs")
        await self._ensure_field("filament", "variant", "Variant")

    async def _ensure_field(self, entity_type: str, key: str, name: str) -> None:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        base = f"{self._spoolman_url}/api/v1/field/{entity_type}"
        try:
            resp = await self.http_client.get(base, headers=headers, enable_cache=False)
            if resp.status_code != 200:
                logging.warning(
                    "[spoollink] could not read custom fields for %s (HTTP %s)",
                    entity_type, resp.status_code)
                return
            fields = resp.json()
            if any(f.get("key") == key for f in fields):
                logging.info("[spoollink] field %s/%s: exists", entity_type, key)
                return
            body = {
                "name": name,
                "field_type": "text",
                "order": 1,
                "default_value": json.dumps(""),
            }
            resp = await self.http_client.post(f"{base}/{key}", body=body, headers=headers)
            if resp.status_code in (200, 201):
                logging.info("[spoollink] field %s/%s: created", entity_type, key)
            else:
                logging.warning(
                    "[spoollink] could not create field %s/%s: HTTP %s %s",
                    entity_type, key, resp.status_code, resp.text())
        except Exception as e:
            logging.warning(
                "[spoollink] custom fields check failed (%s/%s): %s",
                entity_type, key, e)

    async def _spoolman_get_by_id(self, spool_id: int) -> Optional[dict]:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        endpoint = f"/api/v1/spools/{spool_id}" if self._is_filaman else f"/api/v1/spool/{spool_id}"
        resp = await self.http_client.get(
            f"{self._spoolman_url}{endpoint}", headers=headers, enable_cache=False)
        if resp.status_code == 404:
            fallbacks = ["/spoolman/api/v1/spool", "/api/v1/spool", "/api/v1/spools"]
            for fb in fallbacks:
                alt = f"{fb}/{spool_id}"
                if alt == endpoint:
                    continue
                resp = await self.http_client.get(
                    f"{self._spoolman_url}{alt}", headers=headers, enable_cache=False)
                if resp.status_code == 200:
                    break
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

    async def _spoolman_find_by_card(self, card_uid: str,
                                     tray_uid: Optional[str] = None) -> List[dict]:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"

        is_filaman = self._is_filaman
        primary_endpoint = "/api/v1/spools?limit=1000" if is_filaman else "/api/v1/spool?limit=1000"

        resp = await self.http_client.get(
            f"{self._spoolman_url}{primary_endpoint}",
            headers=headers, enable_cache=False)

        if resp.status_code == 404:
            fallbacks = ["/spoolman/api/v1/spool", "/api/v1/spool", "/api/v1/spools?limit=1000"]
            for fb in fallbacks:
                if fb == primary_endpoint:
                    continue
                resp = await self.http_client.get(
                    f"{self._spoolman_url}{fb}",
                    headers=headers, enable_cache=False)
                if resp.status_code == 200:
                    break

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

        data = resp.json()
        if isinstance(data, dict):
            spools = data.get("items", [])
            total = data.get("total", len(spools))
            page = data.get("page", 1)
            page_size = data.get("page_size", len(spools))
            while len(spools) < total and page * page_size < total:
                page += 1
                next_resp = await self.http_client.get(
                    f"{self._spoolman_url}/api/v1/spools?page={page}&page_size={page_size}",
                    headers=headers, enable_cache=False)
                if next_resp.status_code == 200:
                    next_data = next_resp.json()
                    if isinstance(next_data, dict):
                        spools.extend(next_data.get("items", []))
                    else:
                        break
                else:
                    break
        elif isinstance(data, list):
            spools = data
        else:
            spools = []

        norm_card = _normalize_uid(card_uid)
        norm_tray = _normalize_uid(tray_uid) if tray_uid else ""

        primary_matches = []
        secondary_matches = []
        for s in spools:
            if not isinstance(s, dict):
                continue
            primaries = _extract_primary_uids(s)
            secondaries = _extract_secondary_uids(s)
            
            if norm_card and norm_card in primaries:
                primary_matches.append(s)
            elif norm_tray and norm_tray in primaries:
                primary_matches.append(s)
            elif norm_card and norm_card in secondaries:
                secondary_matches.append(s)
            elif norm_tray and norm_tray in secondaries:
                secondary_matches.append(s)

        candidate_pool = primary_matches if primary_matches else secondary_matches

        if len(candidate_pool) > 1:
            active_candidates = []
            for s in candidate_pool:
                try:
                    weight = s.get("remaining_weight")
                    if weight is None:
                        weight = s.get("remaining_weight_g")
                    is_active = False
                    if weight is None or float(weight) > 0:
                        if not s.get("archived", False):
                            if s.get("status_id") != 5:
                                is_active = True
                    if is_active:
                        active_candidates.append(s)
                except Exception:
                    pass
            
            if active_candidates and len(active_candidates) < len(candidate_pool):
                candidate_pool = active_candidates

        return candidate_pool

    async def _spoolman_patch_card_uids(self, spool: dict, uids: List[str]) -> dict:
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"ApiKey {self._api_key}"
        spool_id = spool["id"]
        if self._is_filaman:
            body: Dict[str, Any] = {
                "custom_fields": {"card_uids": ",".join(uids)}
            }
            if uids and not spool.get("rfid_uid"):
                body["rfid_uid"] = uids[0]
            resp = await self.http_client.request(
                "PATCH", f"{self._spoolman_url}/api/v1/spools/{spool_id}",
                body=body, headers=headers)
            if resp.status_code in (200, 204):
                return resp.json() if resp.status_code == 200 else spool
            if resp.status_code != 404:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

        encoded = json.dumps(",".join(uids))
        resp = await self.http_client.request(
            "PATCH", f"{self._spoolman_url}/api/v1/spool/{spool_id}",
            body={"extra": {"card_uids": encoded}}, headers=headers)
        if resp.status_code in (200, 204):
            return resp.json() if resp.status_code == 200 else spool
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

    async def _spoolman_add_card_uid(self, spool: dict, card_uid: str,
                                     tray_uid: Optional[str] = None) -> dict:
        norm_card = _normalize_uid(card_uid)
        to_add = [norm_card]
        if tray_uid:
            norm_tray = _normalize_uid(tray_uid)
            if norm_tray:
                to_add.append(norm_tray)
        existing = _parse_card_uids(spool)
        new_uids = list(existing)
        for u in to_add:
            if u not in new_uids:
                new_uids.append(u)
        if set(new_uids) == set(existing):
            return spool
        return await self._spoolman_patch_card_uids(spool, new_uids)

    async def _spoolman_remove_card_uid(self, spool: dict, card_uid: str) -> dict:
        norm_card = _normalize_uid(card_uid)
        existing = _parse_card_uids(spool)
        if norm_card not in existing:
            return spool
        return await self._spoolman_patch_card_uids(
            spool, [u for u in existing if u != norm_card])

    # -- Resolution ---------------------------------------------------------

    async def _resolve_spool(self, channel: int, spool_id: Any = None,
                             card_uid: Any = None, tray_uid: Optional[str] = None) -> None:
        spool_id = spool_id or None
        card_uid = card_uid or None
        if channel is None:
            logging.error("[spoollink] resolve_spool: missing channel")
            return

        if card_uid and _is_cascade_uid(_normalize_uid(card_uid)):
            logging.warning("[spoollink] ch%d: ignoring truncated ISO14443-A cascade UID %s",
                            channel, card_uid)
            return

        logging.debug("[spoollink] ch%d: resolve spool_id=%s card_uid=%s",
                      channel, spool_id, card_uid)
        spool_by_id = None
        spools_by_card: List[dict] = []
        spoolman_ok = True
        if tray_uid:
            logging.info("[spoollink] ch%d: resolved Bambu Tray UID %s for card %s",
                         channel, tray_uid, card_uid)

        if spool_id is not None:
            try:
                spool_by_id = await self._retry(self._spoolman_get_by_id, spool_id)
            except Exception as e:
                logging.error("[spoollink] ch%d: fetch spool %s failed: %s",
                              channel, spool_id, e)
                spoolman_ok = False

        if card_uid is not None:
            try:
                spools_by_card = await self._retry(
                    self._spoolman_find_by_card, card_uid, tray_uid=tray_uid)
            except Exception as e:
                logging.error("[spoollink] ch%d: fetch by card failed: %s",
                              channel, e)
                spoolman_ok = False

        if len(spools_by_card) > 1:
            ids = ", ".join(f"#{s.get('id')}" for s in spools_by_card)
            logging.warning("[spoollink] ch%d: card %s assigned to multiple spools: %s",
                            channel, card_uid, ids)
            await self._spoollink_set(
                channel,
                f"SpoolLink: E{channel + 1} card {card_uid} "
                f"assigned to multiple spools: {ids}",
                status="error")
            return
        spool_by_card = spools_by_card[0] if spools_by_card else None
        spool = spool_by_id or spool_by_card
        cached = False
        if spool is None:
            if card_uid is not None and not spoolman_ok:
                spool = self._load_cache(card_uid)
                if spool is None and tray_uid:
                    spool = self._load_cache(tray_uid)
                if spool is not None:
                    logging.warning("[spoollink] ch%d: using cached data for card %s",
                                    channel, card_uid)
            cached = spool is not None and not spoolman_ok
            if spool is None:
                # Active print protection: never unbind an existing mapped spool due to a transient scan issue
                current_spool_id = (self._ptc_spool_ids[channel]
                                    if channel < len(self._ptc_spool_ids) else 0) or 0
                if self._is_printing() and current_spool_id != 0:
                    logging.warning(
                        "[spoollink] ch%d: card %s unresolved during active print; "
                        "preserving existing spool #%s to prevent unbind",
                        channel, card_uid, current_spool_id)
                    return

                if card_uid is not None:
                    if spoolman_ok:
                        self._delete_cache(card_uid)
                        if tray_uid:
                            self._delete_cache(tray_uid)
                    if self._last_unresolved.get(channel) == card_uid:
                        logging.debug(
                            "[spoollink] ch%d: card %s still unresolved (suppressing repeat notice)",
                            channel, card_uid)
                    else:
                        self._last_unresolved[channel] = card_uid
                        logging.warning("[spoollink] ch%d: no spool found for card %s",
                                        channel, card_uid)
                        await self._spoollink_set(
                            channel,
                            f"SpoolLink: E{channel + 1} no spool found for card {card_uid}",
                            status="error")
                return

        # Spool found / resolved! Clear unresolved state for channel
        self._last_unresolved.pop(channel, None)

        if card_uid is not None and spool_by_id is not None:
            norm_card = _normalize_uid(card_uid)
            if norm_card not in (_extract_primary_uids(spool_by_id) | _extract_secondary_uids(spool_by_id)):
                try:
                    spool = await self._retry(
                        self._spoolman_add_card_uid, spool_by_id, card_uid,
                        tray_uid=tray_uid)
                    logging.info("[spoollink] ch%d: bound spool %s to card %s (tray %s)",
                                 channel, spool_by_id["id"], card_uid, tray_uid or "none")
                except Exception as e:
                    logging.error("[spoollink] ch%d: bind spool %s failed: %s",
                                  channel, spool_by_id["id"], e)

            for stale in spools_by_card:
                if stale.get("id") == spool_by_id.get("id"):
                    continue
                try:
                    await self._retry(
                        self._spoolman_remove_card_uid, stale, card_uid)
                    logging.info("[spoollink] ch%d: unbound card %s from spool %s",
                                 channel, card_uid, stale.get("id"))
                except Exception as e:
                    logging.error(
                        "[spoollink] ch%d: unbind card %s from spool %s failed: %s",
                        channel, card_uid, stale.get("id"), e)

        if card_uid is not None and spoolman_ok:
            self._save_cache(card_uid, spool)
            if tray_uid:
                self._save_cache(tray_uid, spool)

        await self._apply_spool(channel, spool, card_uid or "", cached=cached)

    async def _apply_spool(self, channel: int, spool: dict, uid_hex: str,
                           cached: bool = False) -> None:
        spool_id = spool.get("id", 0)
        filament = spool.get("filament", {})
        mat_raw = (filament.get("material") or filament.get("material_type") or "PLA").strip()
        mat_upper = mat_raw.upper().replace("_", "-")
        if mat_upper in ("PLA-PLUS", "PLA+", "PLA PLUS", "APLA"):
            material = "PLA"
        elif mat_upper in ("PETG-PLUS", "PETG+", "PETG PLUS"):
            material = "PETG"
        elif mat_upper in ("ABS-PLUS", "ABS+", "ABS PLUS"):
            material = "ABS"
        else:
            material = mat_raw
        vendor = (filament.get("vendor") or filament.get("manufacturer") or {}).get("name", "Generic")
        variant = _parse_variant(vendor, filament)

        if (self._force_generic_vendor
                and vendor.strip().lower() != "snapmaker"):
            vendor = "Generic"
            variant = ""

        raw_multi = filament.get("multi_color_hexes") or ""
        colors = [c.strip().upper().lstrip("#")[:6] for c in raw_multi.split(",") if c.strip()]
        if not colors and filament.get("colors"):
            for c in filament.get("colors", []):
                if isinstance(c, dict):
                    hex_val = (c.get("color") or {}).get("hex_code") or ""
                    hex_clean = hex_val.strip().upper().lstrip("#")[:6]
                    if hex_clean:
                        colors.append(hex_clean)
        color_hex = colors[0] if colors else (filament.get("color_hex") or "FFFFFF").strip().upper().lstrip("#")[:6]

        color_list = list(colors) or [color_hex]
        color_nums = len(color_list)
        while len(color_list) < 5:
            color_list.append("000000")

        card_uid = [int(uid_hex[i:i+2], 16)
                    for i in range(0, len(uid_hex), 2)] if uid_hex else []
        alpha = 0xFF
        info = {
            "VENDOR": vendor,
            "MAIN_TYPE": material,
            "SUB_TYPE": variant,
            "RGB_1": int(color_list[0], 16),
            "RGB_2": int(color_list[1], 16),
            "RGB_3": int(color_list[2], 16),
            "RGB_4": int(color_list[3], 16),
            "RGB_5": int(color_list[4], 16),
            "ALPHA": alpha,
            "ARGB_COLOR": (alpha << 24) | int(color_list[0], 16),
            "COLOR_NUMS": color_nums,
            "MULTI_MODE": 0,
            "OFFICIAL": True,
            "SKU": 0,
            "SPOOL_ID": spool_id,
            "CARD_UID": card_uid,
            "CARD_TYPE": 0,
        }

        label = f"{vendor} {material}"
        if variant:
            label += f" {variant}"
        label += f" #{color_list[0]} (spool #{spool_id}, card {uid_hex or 'none'})"
        if cached:
            label += " [cached]"
        message = f"SpoolLink: E{channel + 1} loaded {label}"

        logging.info(
            "[spoollink] ch%d: applying spool %s — %s %s%s #%s (card %s)",
            channel, spool_id, vendor, material,
            f" {variant}" if variant else "",
            color_hex, uid_hex or "none")
        reply = await self._spoollink_set(channel, message, info=info)
        if reply is not None:
            logging.info("[spoollink] ch%d: spool %s applied", channel, spool_id)


def load_component(config: ConfigHelper) -> SpoolLink:
    return SpoolLink(config)
