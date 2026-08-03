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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient, HttpResponse
    from .klippy_apis import KlippyAPI as APIComp

RESOLVE_METHOD = "spoollink_resolve_spool"
SET_ENDPOINT = "spoollink/set"


def _unquote(value: str) -> str:
    s = value.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _parse_card_uids(spool: dict) -> List[str]:
    raw = _unquote((spool.get("extra") or {}).get("card_uids") or "")
    return [u.strip().upper() for u in raw.split(",") if u.strip()]


def _parse_variant(vendor: str, filament: dict) -> str:
    variant = _unquote((filament.get("extra") or {}).get("variant") or "")
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
        self._cache_dir: Optional[str] = config.get("cache_dir", None)
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")

        self._channel_uids: Dict[int, str] = {}
        self._toolhead_extruder: str = "extruder"
        self._ptc_spool_ids: List[int] = []
        self._active_spool_id: Optional[int] = None

        self.server.register_remote_method(RESOLVE_METHOD, self._resolve_spool)
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_klippy_disconnect)
        self.server.register_event_handler(
            "spoolman:active_spool_set", self._handle_active_spool_set)

    async def component_init(self) -> None:
        logging.info(
            "spoollink starting (spoolman: %s, cache: %s)",
            self._spoolman_url, self._cache_dir or "disabled")
        await self._ensure_fields()

    # -- Klippy lifecycle ---------------------------------------------------

    async def _handle_klippy_ready(self) -> None:
        logging.info("[spoollink] Klippy ready, subscribing to objects")
        status = await self.klippy_apis.subscribe_objects({
            "filament_detect": None,
            "print_task_config": ["filament_spool_id"],
            "toolhead": ["extruder"],
        }, self._handle_status_update, {})
        self._handle_status_update(status, 0.)

    def _handle_klippy_disconnect(self) -> None:
        logging.info("[spoollink] Klippy disconnected")
        self._channel_uids = {}
        self._ptc_spool_ids = []
        self._active_spool_id = None

    # -- Remote method / subscription callbacks -----------------------------

    def _handle_active_spool_set(self, payload: Dict[str, Any]) -> None:
        spool_id = payload.get("spool_id")
        if spool_id != self._active_spool_id:
            self._active_spool_id = spool_id
            logging.info("[spoollink] active spool received: spool_id=%s", spool_id)
            self._fire(self._sync_active_spool())

    def _handle_status_update(self, status: Dict[str, Any], eventtime: float) -> None:
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
        uid_hex = self._uid_to_hex(info.get("CARD_UID"))
        prev = self._channel_uids.get(ch, "")
        self._channel_uids[ch] = uid_hex
        if uid_hex and uid_hex != prev:
            logging.info("[spoollink] ch%d: card UID changed to %s, resolving",
                         ch, uid_hex)
            self._fire(self._resolve_spool(ch, card_uid=uid_hex))

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
        return os.path.join(self._cache_dir, f"{card_uid.upper()}.json")

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

    # -- Spoolman REST ------------------------------------------------------

    async def _ensure_fields(self) -> None:
        await self._ensure_field("spool", "card_uids", "Card UIDs")
        await self._ensure_field("filament", "variant", "Variant")

    async def _ensure_field(self, entity_type: str, key: str, name: str) -> None:
        base = f"{self._spoolman_url}/api/v1/field/{entity_type}"
        try:
            resp = await self.http_client.get(base, enable_cache=False)
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
            resp = await self.http_client.post(f"{base}/{key}", body=body)
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
        resp = await self.http_client.get(
            f"{self._spoolman_url}/api/v1/spool/{spool_id}", enable_cache=False)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

    async def _spoolman_find_by_card(self, card_uid: str) -> List[dict]:
        resp = await self.http_client.get(
            f"{self._spoolman_url}/api/v1/spool?limit=1000", enable_cache=False)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")
        spools = resp.json()
        uid_upper = card_uid.upper()
        return [s for s in spools if uid_upper in _parse_card_uids(s)]

    async def _spoolman_patch_card_uids(self, spool: dict, uids: List[str]) -> dict:
        encoded = json.dumps(",".join(uids))
        resp = await self.http_client.request(
            "PATCH", f"{self._spoolman_url}/api/v1/spool/{spool['id']}",
            body={"extra": {"card_uids": encoded}})
        if resp.status_code == 200:
            return resp.json()
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text()}")

    async def _spoolman_add_card_uid(self, spool: dict, card_uid: str) -> dict:
        uid_upper = card_uid.upper()
        existing = _parse_card_uids(spool)
        if uid_upper in existing:
            return spool
        return await self._spoolman_patch_card_uids(spool, existing + [uid_upper])

    async def _spoolman_remove_card_uid(self, spool: dict, card_uid: str) -> dict:
        uid_upper = card_uid.upper()
        existing = _parse_card_uids(spool)
        if uid_upper not in existing:
            return spool
        return await self._spoolman_patch_card_uids(
            spool, [u for u in existing if u != uid_upper])

    # -- Resolution ---------------------------------------------------------

    async def _resolve_spool(self, channel: int, spool_id: Any = None,
                             card_uid: Any = None) -> None:
        spool_id = spool_id or None
        card_uid = card_uid or None
        if channel is None:
            logging.error("[spoollink] resolve_spool: missing channel")
            return
        logging.debug("[spoollink] ch%d: resolve spool_id=%s card_uid=%s",
                      channel, spool_id, card_uid)
        spool_by_id = None
        spools_by_card: List[dict] = []
        spoolman_ok = True

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
                    self._spoolman_find_by_card, card_uid)
            except Exception as e:
                logging.error("[spoollink] ch%d: fetch by card failed: %s",
                              channel, e)
                spoolman_ok = False

        if len(spools_by_card) > 1:
            ids = ", ".join(f"#{s['id']}" for s in spools_by_card)
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
                if spool is not None:
                    logging.warning("[spoollink] ch%d: using cached data for card %s",
                                    channel, card_uid)
            cached = spool is not None and not spoolman_ok
            if spool is None:
                if card_uid is not None:
                    if spoolman_ok:
                        self._delete_cache(card_uid)
                    await self._spoollink_set(
                        channel,
                        f"SpoolLink: E{channel + 1} no spool found for card {card_uid}",
                        status="error")
                return

        if card_uid is not None and spool_by_id is not None:
            if card_uid.upper() not in _parse_card_uids(spool_by_id):
                try:
                    spool = await self._retry(
                        self._spoolman_add_card_uid, spool_by_id, card_uid)
                    logging.info("[spoollink] ch%d: bound spool %s to card %s",
                                 channel, spool_by_id["id"], card_uid)
                except Exception as e:
                    logging.error("[spoollink] ch%d: bind spool %s failed: %s",
                                  channel, spool_by_id["id"], e)

            for stale in spools_by_card:
                if stale["id"] == spool_by_id["id"]:
                    continue
                try:
                    await self._retry(
                        self._spoolman_remove_card_uid, stale, card_uid)
                    logging.info("[spoollink] ch%d: unbound card %s from spool %s",
                                 channel, card_uid, stale["id"])
                except Exception as e:
                    logging.error(
                        "[spoollink] ch%d: unbind card %s from spool %s failed: %s",
                        channel, card_uid, stale["id"], e)

        if card_uid is not None and spoolman_ok:
            self._save_cache(card_uid, spool)

        await self._apply_spool(channel, spool, card_uid or "", cached=cached)

    async def _apply_spool(self, channel: int, spool: dict, uid_hex: str,
                           cached: bool = False) -> None:
        spool_id = spool.get("id", 0)
        filament = spool.get("filament", {})
        material = filament.get("material", "PLA")
        vendor = (filament.get("vendor") or {}).get("name", "Generic")
        variant = _parse_variant(vendor, filament)

        raw_multi = filament.get("multi_color_hexes") or ""
        colors = [c.strip().upper()[:6] for c in raw_multi.split(",") if c.strip()]
        color_hex = colors[0] if colors else (filament.get("color_hex") or "FFFFFF")[:6].upper()

        color_list = colors or [color_hex]
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
