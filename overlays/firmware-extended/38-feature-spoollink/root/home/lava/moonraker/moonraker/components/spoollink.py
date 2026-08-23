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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient, HttpResponse
    from .klippy_apis import KlippyAPI as APIComp

RESOLVE_METHOD = "spoollink_resolve_spool"
SET_ENDPOINT = "spoollink/set"
RFID_REFRESH_WINDOW = 5.0
# Do not include terminal or unknown feeder states in empty-UID recovery.
EMPTY_UID_RECOVERY_LOAD_STATES = {"load_heating"}


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
        configured_channels = config.getintlist(
            "external_channels", [], separator=",")
        invalid_channels = sorted({
            ch for ch in configured_channels if ch < 0 or ch > 3})
        if invalid_channels:
            raise config.error(
                "[spoollink] external_channels must contain only channel "
                f"numbers from 0 to 3; invalid: {invalid_channels}")
        self._external_channels: Set[int] = set(configured_channels)
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")

        self._channel_uids: Dict[int, str] = {}
        self._channel_event_times: Dict[int, float] = {}
        self._channel_signatures: Dict[int, Tuple[Any, ...]] = {}
        self._resolved_uids: Dict[int, str] = {}
        self._fd_states: List[Any] = []
        self._refreshing_channels: Dict[int, bool] = {}
        self._refresh_deadlines: Dict[int, float] = {}
        self._metadata_clear_deadlines: Dict[int, float] = {}
        self._resolution_tasks: Dict[int, "asyncio.Future"] = {}
        self._resolution_task_uids: Dict[int, str] = {}
        self._resolution_task_event_times: Dict[int, Optional[float]] = {}
        self._queued_resolutions: Dict[
            int, Tuple[str, Optional[float]]] = {}
        self._recovery_holds: Dict[int, Tuple[Optional[str], int]] = {}
        self._known_spools: Dict[int, Tuple[str, dict]] = {}
        self._retention_tasks: Dict[int, "asyncio.Future"] = {}
        self._feeder_eligible: Dict[int, bool] = {}
        self._feeder_load_states: Dict[int, Tuple[Any, Any]] = {}
        self._toolhead_sensors: Dict[int, Dict[str, Any]] = {}
        self._sensor_refresh_deadlines: Dict[int, float] = {}
        self._toolhead_extruder: str = "extruder"
        self._ptc_vendors: List[str] = []
        self._ptc_types: List[str] = []
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
            "spoollink starting (spoolman: %s, cache: %s, "
            "external channels: %s)",
            self._spoolman_url, self._cache_dir or "disabled",
            sorted(self._external_channels) or "none")
        await self._ensure_fields()

    # -- Klippy lifecycle ---------------------------------------------------

    async def _handle_klippy_ready(self) -> None:
        logging.info("[spoollink] Klippy ready, subscribing to objects")
        status = await self.klippy_apis.subscribe_objects({
            "filament_detect": None,
            "print_task_config": [
                "filament_spool_id", "filament_vendor", "filament_type"],
            "toolhead": ["extruder"],
            "filament_feed left": None,
            "filament_feed right": None,
            "filament_motion_sensor e0_filament": [
                "filament_detected", "enabled"],
            "filament_motion_sensor e1_filament": [
                "filament_detected", "enabled"],
            "filament_motion_sensor e2_filament": [
                "filament_detected", "enabled"],
            "filament_motion_sensor e3_filament": [
                "filament_detected", "enabled"],
        }, self._handle_status_update, {})
        self._handle_status_update(status, 0.)

    def _handle_klippy_disconnect(self) -> None:
        logging.info("[spoollink] Klippy disconnected")
        for task in self._resolution_tasks.values():
            task.cancel()
        for task in self._retention_tasks.values():
            task.cancel()
        self._channel_uids = {}
        self._channel_event_times = {}
        self._channel_signatures = {}
        self._resolved_uids = {}
        self._fd_states = []
        self._refreshing_channels = {}
        self._refresh_deadlines = {}
        self._metadata_clear_deadlines = {}
        self._resolution_tasks = {}
        self._resolution_task_uids = {}
        self._resolution_task_event_times = {}
        self._queued_resolutions = {}
        self._recovery_holds = {}
        self._known_spools = {}
        self._retention_tasks = {}
        self._feeder_eligible = {}
        self._feeder_load_states = {}
        self._toolhead_sensors = {}
        self._sensor_refresh_deadlines = {}
        self._ptc_vendors = []
        self._ptc_types = []
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

        # Feeder presence and toolhead-sensor transitions distinguish an
        # automatic RFID refresh from a manual clear. Process them first so a
        # combined update can qualify the RFID and spool-ID changes below.
        self._handle_feeder_status(status)
        self._handle_toolhead_sensors(status, eventtime)

        # Capture same-update clear evidence before the empty-UID handler can
        # discard the known spool. The spool-ID decision still runs afterward.
        ptc = status.get("print_task_config")
        combined_clear_spool_ids = self._combined_ptc_clear_spool_ids(ptc)

        # Process the RFID object before print_task_config. A single Klippy
        # status update may contain both a new card and a cleared spool ID;
        # using the current UID avoids resolving the card that was just removed.
        fd = status.get("filament_detect")
        if fd is not None:
            states = fd.get("state")
            if states is not None:
                self._handle_filament_detect_states(states, eventtime)
            info_list = fd.get("info")
            if info_list is not None:
                for ch, info in enumerate(info_list):
                    self._handle_filament_detect_channel(
                        ch, info, eventtime,
                        combined_clear_spool_ids.get(ch, 0))

        if ptc is not None:
            self._handle_ptc_metadata(ptc, eventtime)
            spool_ids = ptc.get("filament_spool_id")
            if spool_ids is not None:
                new_ids = list(spool_ids or [])
            else:
                new_ids = self._ptc_spool_ids
            if new_ids != self._ptc_spool_ids:
                logging.info("[spoollink] spool_ids changed: %s → %s",
                             self._ptc_spool_ids, new_ids)
                channels = max(len(self._ptc_spool_ids), len(new_ids))
                for ch in range(channels):
                    old_id = (self._ptc_spool_ids[ch]
                              if ch < len(self._ptc_spool_ids) else 0) or 0
                    new_id = (new_ids[ch] if ch < len(new_ids) else 0) or 0
                    if old_id > 0 and new_id == 0:
                        self._start_same_uid_recovery(
                            ch, old_id, eventtime,
                            combined_clear_spool_ids.get(ch) == old_id
                            and self._has_automatic_load_evidence(ch))
                self._ptc_spool_ids = new_ids
                self._fire(self._sync_active_spool())

    def _handle_feeder_status(self, status: Dict[str, Any]) -> None:
        modules = {
            "filament_feed left": {"extruder0": 0, "extruder1": 1},
            "filament_feed right": {"extruder2": 2, "extruder3": 3},
        }
        for object_name, channels in modules.items():
            feed = status.get(object_name)
            if not isinstance(feed, dict):
                continue
            for extruder, ch in channels.items():
                state = feed.get(extruder)
                if not isinstance(state, dict):
                    continue
                self._feeder_load_states[ch] = (
                    state.get("channel_state"),
                    state.get("channel_action_state"))
                module_exist = bool(state.get("module_exist"))
                filament_detected = bool(state.get("filament_detected"))
                disable_auto = bool(state.get("disable_auto"))
                eligible = bool(
                    module_exist and filament_detected and not disable_auto)
                was_eligible = self._feeder_eligible.get(ch)
                self._feeder_eligible[ch] = eligible
                if was_eligible and not eligible:
                    uid = self._channel_uids.get(ch, "")
                    known = self._known_spools.get(ch)
                    known_id = (
                        known[1].get("id", 0)
                        if known is not None else 0) or 0
                    retain_known_spool = (
                        self._can_retain_external_spool(ch))
                    logging.info(
                        "[spoollink] ch%d: feeder no longer retains filament: "
                        "external_channel=%s module_exist=%s "
                        "filament_detected=%s disable_auto=%s "
                        "uid_present=%s uid_resolved=%s known_spool_id=%s "
                        "retain_known_spool=%s",
                        ch, ch in self._external_channels, module_exist,
                        filament_detected, disable_auto, bool(uid),
                        bool(uid and self._resolved_uids.get(ch) == uid),
                        known_id, retain_known_spool)
                    if not retain_known_spool:
                        self._cancel_detected_resolution(ch)
                        self._forget_known_spool(ch)

    def _can_retain_external_spool(self, ch: int) -> bool:
        if ch not in self._external_channels:
            return False
        uid = self._channel_uids.get(ch, "")
        known = self._known_spools.get(ch)
        return bool(
            uid
            and self._resolved_uids.get(ch) == uid
            and known is not None
            and known[0] == uid
            and (known[1].get("id", 0) or 0) > 0)

    def _handle_toolhead_sensors(
            self, status: Dict[str, Any], eventtime: float) -> None:
        for ch in range(4):
            key = f"filament_motion_sensor e{ch}_filament"
            update = status.get(key)
            if not isinstance(update, dict):
                continue
            state = self._toolhead_sensors.setdefault(ch, {})
            previous = state.get("filament_detected")
            state.update(update)
            present = state.get("filament_detected")
            if (eventtime > 0. and previous is not None
                    and present is not None and present != previous
                    and state.get("enabled", True)
                    and self._feeder_eligible.get(ch, False)):
                self._sensor_refresh_deadlines[ch] = (
                    eventtime + RFID_REFRESH_WINDOW)
                logging.debug(
                    "[spoollink] ch%d: toolhead filament transition", ch)

    def _has_sensor_refresh_evidence(
            self, ch: int, eventtime: float) -> bool:
        return (
            eventtime > 0.
            and eventtime <= self._sensor_refresh_deadlines.get(ch, 0.)
            and self._feeder_eligible.get(ch, False))

    def _has_automatic_load_evidence(self, ch: int) -> bool:
        feeder_state, feeder_action = self._feeder_load_states.get(
            ch, (None, None))
        sensor = self._toolhead_sensors.get(ch, {})
        return bool(
            self._feeder_eligible.get(ch, False)
            and feeder_state == feeder_action
            and feeder_state in EMPTY_UID_RECOVERY_LOAD_STATES
            and sensor.get("filament_detected") is True
            and sensor.get("enabled", True)
            and self._extruder_to_channel(self._toolhead_extruder) == ch)

    @staticmethod
    def _deadline_remaining(deadline: float, eventtime: float) -> str:
        if deadline <= 0. or eventtime <= 0.:
            return "none"
        return f"{deadline - eventtime:+.3f}s"

    @staticmethod
    def _field_is_clear(value: Any) -> bool:
        return value in (None, "", "NONE", "None")

    def _combined_ptc_clear_spool_ids(
            self, ptc: Any) -> Dict[int, int]:
        if not isinstance(ptc, dict):
            return {}
        spool_ids = ptc.get("filament_spool_id")
        if spool_ids is None:
            return {}
        new_ids = list(spool_ids or [])
        vendors = ptc.get("filament_vendor")
        types = ptc.get("filament_type")
        new_vendors = (
            list(vendors) if vendors is not None else self._ptc_vendors)
        new_types = list(types) if types is not None else self._ptc_types
        channels = max(
            len(self._ptc_spool_ids), len(new_ids),
            len(self._ptc_vendors), len(self._ptc_types),
            len(new_vendors), len(new_types))
        cleared: Dict[int, int] = {}
        for ch in range(channels):
            old_id = (self._ptc_spool_ids[ch]
                      if ch < len(self._ptc_spool_ids) else 0) or 0
            new_id = (new_ids[ch] if ch < len(new_ids) else 0) or 0
            old_vendor = (self._ptc_vendors[ch]
                          if ch < len(self._ptc_vendors) else None)
            old_type = (self._ptc_types[ch]
                        if ch < len(self._ptc_types) else None)
            new_vendor = (
                new_vendors[ch] if ch < len(new_vendors) else None)
            new_type = new_types[ch] if ch < len(new_types) else None
            was_populated = not (
                self._field_is_clear(old_vendor)
                and self._field_is_clear(old_type))
            is_cleared = (
                self._field_is_clear(new_vendor)
                and self._field_is_clear(new_type))
            if old_id > 0 and new_id == 0 and was_populated and is_cleared:
                cleared[ch] = old_id
        return cleared

    def _handle_ptc_metadata(
            self, ptc: Dict[str, Any], eventtime: float) -> None:
        vendors = ptc.get("filament_vendor")
        types = ptc.get("filament_type")
        new_vendors = list(vendors) if vendors is not None else self._ptc_vendors
        new_types = list(types) if types is not None else self._ptc_types
        channels = max(
            len(self._ptc_vendors), len(self._ptc_types),
            len(new_vendors), len(new_types))
        for ch in range(channels):
            old_vendor = (self._ptc_vendors[ch]
                          if ch < len(self._ptc_vendors) else None)
            old_type = (self._ptc_types[ch]
                        if ch < len(self._ptc_types) else None)
            new_vendor = new_vendors[ch] if ch < len(new_vendors) else None
            new_type = new_types[ch] if ch < len(new_types) else None
            was_populated = not (self._field_is_clear(old_vendor)
                                 and self._field_is_clear(old_type))
            is_cleared = (self._field_is_clear(new_vendor)
                          and self._field_is_clear(new_type))
            if eventtime > 0. and was_populated and is_cleared:
                self._metadata_clear_deadlines[ch] = (
                    eventtime + RFID_REFRESH_WINDOW)
        self._ptc_vendors = new_vendors
        self._ptc_types = new_types

    def _handle_filament_detect_states(
            self, states: Any, eventtime: float) -> None:
        if not isinstance(states, (list, tuple)):
            return
        new_states = list(states)
        for ch, state in enumerate(new_states):
            prev = self._fd_states[ch] if ch < len(self._fd_states) else None
            if state not in (None, 0) and prev in (None, 0):
                self._refreshing_channels[ch] = True
                logging.debug(
                    "[spoollink] ch%d: RFID reader active", ch)
            elif (state in (None, 0) and prev not in (None, 0)
                  and self._refreshing_channels.pop(ch, None)):
                self._mark_rfid_refresh(ch, eventtime, "reader completed")
        self._fd_states = new_states

    @staticmethod
    def _filament_signature(info: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            info.get("VENDOR"), info.get("MAIN_TYPE"), info.get("SUB_TYPE"),
            info.get("OFFICIAL"), info.get("SKU"), info.get("SPOOL_ID"))

    @staticmethod
    def _card_event_time(info: Dict[str, Any]) -> Optional[float]:
        value = info.get("CARD_EVENT_TIME")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        event_time = float(value)
        return event_time if event_time > 0. else None

    def _mark_rfid_refresh(
            self, ch: int, eventtime: float, reason: str) -> None:
        self._refresh_deadlines[ch] = eventtime + RFID_REFRESH_WINDOW
        logging.debug(
            "[spoollink] ch%d: RFID refresh detected (%s)", ch, reason)

    def _handle_filament_detect_channel(
            self, ch: int, info: Any, eventtime: float = 0.,
            combined_clear_spool_id: int = 0) -> None:
        if not isinstance(info, dict):
            return
        card_event_time = self._card_event_time(info)
        previous_card_event_time = self._channel_event_times.get(ch)
        if card_event_time is not None:
            if (previous_card_event_time is not None
                    and card_event_time <= previous_card_event_time):
                return
            self._channel_event_times[ch] = card_event_time
        uid_hex = self._uid_to_hex(info.get("CARD_UID"))
        prev = self._channel_uids.get(ch, "")
        signature = self._filament_signature(info)
        prev_signature = self._channel_signatures.get(ch)
        self._channel_uids[ch] = uid_hex
        self._channel_signatures[ch] = signature

        if not uid_hex:
            self._cancel_detected_resolution(ch)
            self._resolved_uids.pop(ch, None)
            self._refreshing_channels.pop(ch, None)
            self._refresh_deadlines.pop(ch, None)
            self._metadata_clear_deadlines.pop(ch, None)
            hold = self._recovery_holds.get(ch)
            retained_restore = bool(
                ch in self._retention_tasks
                or (hold is not None and hold[0] is None))
            known = self._known_spools.get(ch)
            known_id = (
                known[1].get("id", 0) if known is not None else 0) or 0
            feeder_eligible = self._feeder_eligible.get(ch, False)
            sensor_evidence = self._has_sensor_refresh_evidence(
                ch, eventtime)
            feeder_state, feeder_action = self._feeder_load_states.get(
                ch, (None, None))
            load_clear_evidence = bool(
                known_id > 0
                and known_id == combined_clear_spool_id
                and self._has_automatic_load_evidence(ch))
            if prev:
                logging.info(
                    "[spoollink] ch%d: UID clear decision: "
                    "known_spool_id=%s feeder_eligible=%s "
                    "feeder_state=%s feeder_action=%s "
                    "sensor_evidence=%s sensor_window=%s "
                    "toolhead_sensor=%s selected_toolhead=%s "
                    "retained_restore=%s load_clear_evidence=%s",
                    ch, known_id, feeder_eligible, feeder_state,
                    feeder_action, sensor_evidence,
                    self._deadline_remaining(
                        self._sensor_refresh_deadlines.get(ch, 0.),
                        eventtime),
                    self._toolhead_sensors.get(ch, {}).get(
                        "filament_detected"),
                    self._extruder_to_channel(self._toolhead_extruder) == ch,
                    retained_restore, load_clear_evidence)
            if (ch in self._known_spools
                    and ch not in self._external_channels
                    and feeder_eligible
                    and (sensor_evidence
                         or retained_restore
                         or load_clear_evidence)):
                logging.info(
                    "[spoollink] ch%d: UID cleared during qualified load; "
                    "retaining spool", ch)
            else:
                self._forget_known_spool(ch)
            return

        if uid_hex and uid_hex != prev:
            known = self._known_spools.get(ch)
            same_known_uid = known is not None and known[0] == uid_hex
            if same_known_uid:
                self._cancel_retention_task(ch)
                known_id = (known[1].get("id", 0) or 0)
                if known_id:
                    self._recovery_holds[ch] = (uid_hex, known_id)
            else:
                self._forget_known_spool(ch)
            self._resolved_uids.pop(ch, None)
            self._refreshing_channels.pop(ch, None)
            self._refresh_deadlines.pop(ch, None)
            self._metadata_clear_deadlines.pop(ch, None)
            if not same_known_uid:
                self._release_recovery_hold(ch)
            if ch in self._external_channels and eventtime > 0.:
                self._mark_rfid_refresh(
                    ch, eventtime, "external UID changed")
            if card_event_time is not None:
                logging.info(
                    "[spoollink] ch%d: card event %.6f changed UID to %s, "
                    "resolving", ch, card_event_time, uid_hex)
            else:
                logging.info(
                    "[spoollink] ch%d: card UID changed to %s, resolving",
                    ch, uid_hex)
            self._schedule_detected_resolution(
                ch, uid_hex, card_event_time)
        elif card_event_time is not None:
            known = self._known_spools.get(ch)
            known_id = (
                known[1].get("id", 0) if known is not None else 0) or 0
            if (known is not None and known[0] == uid_hex
                    and self._resolved_uids.get(ch) == uid_hex
                    and known_id > 0):
                self._recovery_holds[ch] = (uid_hex, known_id)
            if eventtime > 0.:
                self._mark_rfid_refresh(ch, eventtime, "card event")
            logging.info(
                "[spoollink] ch%d: card event %.6f for %s, resolving",
                ch, card_event_time, uid_hex)
            self._schedule_detected_resolution(
                ch, uid_hex, card_event_time)
        elif (eventtime > 0. and prev_signature is not None
              and signature != prev_signature):
            self._mark_rfid_refresh(ch, eventtime, "card data updated")

    def _start_same_uid_recovery(
            self, ch: int, old_spool_id: int, eventtime: float,
            load_clear_evidence: bool = False) -> None:
        uid = self._channel_uids.get(ch, "")
        refresh_deadline = self._refresh_deadlines.get(ch, 0.)
        metadata_deadline = self._metadata_clear_deadlines.get(ch, 0.)
        has_refresh_evidence = (
            eventtime > 0.
            and eventtime <= max(refresh_deadline, metadata_deadline))
        known = self._known_spools.get(ch)
        known_id = (
            known[1].get("id", 0) if known is not None else 0) or 0
        feeder_eligible = self._feeder_eligible.get(ch, False)
        sensor_evidence = self._has_sensor_refresh_evidence(ch, eventtime)
        feeder_state, feeder_action = self._feeder_load_states.get(
            ch, (None, None))
        logging.info(
            "[spoollink] ch%d: spool clear decision: old_spool_id=%s "
            "uid_present=%s uid_resolved=%s known_spool_id=%s "
            "feeder_eligible=%s feeder_state=%s feeder_action=%s "
            "sensor_evidence=%s sensor_window=%s reader_window=%s "
            "metadata_window=%s toolhead_sensor=%s selected_toolhead=%s "
            "load_clear_evidence=%s",
            ch, old_spool_id, bool(uid),
            bool(uid and self._resolved_uids.get(ch) == uid), known_id,
            feeder_eligible, feeder_state, feeder_action, sensor_evidence,
            self._deadline_remaining(
                self._sensor_refresh_deadlines.get(ch, 0.), eventtime),
            self._deadline_remaining(refresh_deadline, eventtime),
            self._deadline_remaining(metadata_deadline, eventtime),
            self._toolhead_sensors.get(ch, {}).get("filament_detected"),
            self._extruder_to_channel(self._toolhead_extruder) == ch,
            load_clear_evidence)
        if (uid and self._resolved_uids.get(ch) == uid
                and has_refresh_evidence):
            self._refresh_deadlines.pop(ch, None)
            self._metadata_clear_deadlines.pop(ch, None)
            self._recovery_holds[ch] = (uid, old_spool_id)
            logging.info(
                "[spoollink] ch%d: same card %s cleared spool %s; re-resolving",
                ch, uid, old_spool_id)
            self._schedule_detected_resolution(
                ch, uid, self._channel_event_times.get(ch))
            return

        if (not uid and ch not in self._external_channels
                and known is not None and known_id == old_spool_id
                and (sensor_evidence or load_clear_evidence)):
            self._sensor_refresh_deadlines.pop(ch, None)
            self._metadata_clear_deadlines.pop(ch, None)
            self._recovery_holds[ch] = (None, old_spool_id)
            logging.info(
                "[spoollink] ch%d: automatic read lost UID; restoring spool %s",
                ch, old_spool_id)
            self._schedule_retained_restore(ch, known[0], known[1])
            return

        if not uid or self._resolved_uids.get(ch) == uid:
            self._cancel_detected_resolution(ch)
        if known_id == old_spool_id:
            self._forget_known_spool(ch)

    def _release_recovery_hold(self, ch: int) -> None:
        if self._recovery_holds.pop(ch, None) is not None:
            self._fire(self._sync_active_spool())

    def _forget_known_spool(self, ch: int) -> None:
        self._known_spools.pop(ch, None)
        self._sensor_refresh_deadlines.pop(ch, None)
        self._resolved_uids.pop(ch, None)
        self._cancel_retention_task(ch)
        self._release_recovery_hold(ch)

    def _cancel_detected_resolution(self, ch: int) -> None:
        task = self._resolution_tasks.pop(ch, None)
        self._resolution_task_uids.pop(ch, None)
        self._resolution_task_event_times.pop(ch, None)
        self._queued_resolutions.pop(ch, None)
        if task is not None and not task.done():
            task.cancel()

    def _cancel_retention_task(self, ch: int) -> None:
        task = self._retention_tasks.pop(ch, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_retained_restore(
            self, ch: int, uid: str, spool: dict) -> None:
        task = self._retention_tasks.get(ch)
        if task is not None and not task.done():
            return
        task = asyncio.ensure_future(
            self._restore_known_spool(ch, uid, spool))
        self._retention_tasks[ch] = task
        task.add_done_callback(
            lambda completed, channel=ch, card_uid=uid:
                self._retained_restore_done(channel, card_uid, completed))

    def _can_restore_known_spool(
            self, ch: int, uid: str, spool_id: int) -> bool:
        known = self._known_spools.get(ch)
        return bool(
            known is not None
            and known[0] == uid
            and (known[1].get("id", 0) or 0) == spool_id
            and not self._channel_uids.get(ch, "")
            and ch not in self._external_channels
            and self._feeder_eligible.get(ch, False))

    async def _restore_known_spool(
            self, ch: int, uid: str, spool: dict) -> Optional[int]:
        await asyncio.sleep(0)
        spool_id = (spool.get("id", 0) or 0)
        if not self._can_restore_known_spool(ch, uid, spool_id):
            return None
        # The reader reported no UID. Restore the spool metadata without
        # claiming that a card is currently readable.
        return await self._apply_spool(ch, spool, "")

    def _retained_restore_done(
            self, ch: int, uid: str, task: "asyncio.Future") -> None:
        if self._retention_tasks.get(ch) is not task:
            return
        self._retention_tasks.pop(ch, None)
        if task.cancelled():
            return
        try:
            spool_id = task.result()
        except Exception as e:
            logging.error(
                "[spoollink] ch%d: retained spool restore failed: %s",
                ch, e, exc_info=e)
            self._forget_known_spool(ch)
            return
        if not spool_id or not self._can_restore_known_spool(ch, uid, spool_id):
            self._forget_known_spool(ch)
            return
        while len(self._ptc_spool_ids) <= ch:
            self._ptc_spool_ids.append(0)
        if not self._ptc_spool_ids[ch]:
            self._ptc_spool_ids[ch] = spool_id
        hold = self._recovery_holds.get(ch)
        if hold == (None, spool_id):
            self._recovery_holds.pop(ch, None)
        self._fire(self._sync_active_spool())

    def _detected_resolution_is_current(
            self, ch: int, uid: str,
            card_event_time: Optional[float]) -> bool:
        return bool(
            self._channel_uids.get(ch) == uid
            and (card_event_time is None
                 or self._channel_event_times.get(ch) == card_event_time))

    def _schedule_detected_resolution(
            self, ch: int, uid: str,
            card_event_time: Optional[float] = None) -> None:
        task = self._resolution_tasks.get(ch)
        if task is not None and not task.done():
            active = (
                self._resolution_task_uids.get(ch),
                self._resolution_task_event_times.get(ch))
            queued = (uid, card_event_time)
            if active != queued:
                self._queued_resolutions[ch] = queued
            return
        task = asyncio.ensure_future(
            self._resolve_spool(
                ch, card_uid=uid, expected_uid=uid,
                expected_card_event_time=card_event_time))
        self._resolution_tasks[ch] = task
        self._resolution_task_uids[ch] = uid
        self._resolution_task_event_times[ch] = card_event_time
        task.add_done_callback(
            lambda completed, channel=ch, card_uid=uid,
            event_time=card_event_time:
                self._detected_resolution_done(
                    channel, card_uid, event_time, completed))

    def _detected_resolution_done(
            self, ch: int, uid: str, card_event_time: Optional[float],
            task: "asyncio.Future") -> None:
        if self._resolution_tasks.get(ch) is not task:
            return
        self._resolution_tasks.pop(ch, None)
        self._resolution_task_uids.pop(ch, None)
        self._resolution_task_event_times.pop(ch, None)

        spool_id = None
        if not task.cancelled():
            try:
                spool_id = task.result()
            except Exception as e:
                logging.error(
                    "[spoollink] ch%d: card %s resolution failed: %s",
                    ch, uid, e, exc_info=e)

        is_current = self._detected_resolution_is_current(
            ch, uid, card_event_time)
        if spool_id and is_current:
            self._resolved_uids[ch] = uid
            while len(self._ptc_spool_ids) <= ch:
                self._ptc_spool_ids.append(0)
            if not self._ptc_spool_ids[ch]:
                self._ptc_spool_ids[ch] = spool_id

        hold = self._recovery_holds.get(ch)
        if hold is not None and hold[0] == uid and is_current:
            self._recovery_holds.pop(ch, None)
            self._fire(self._sync_active_spool())

        queued = self._queued_resolutions.pop(ch, None)
        if queued is not None:
            queued_uid, queued_event_time = queued
            if self._detected_resolution_is_current(
                    ch, queued_uid, queued_event_time):
                self._schedule_detected_resolution(
                    ch, queued_uid, queued_event_time)

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
        hold = self._recovery_holds.get(channel)
        if spool_id == 0 and hold is not None:
            hold_uid, hold_id = hold
            known = self._known_spools.get(channel)
            retained_hold = bool(
                hold_uid is None
                and channel not in self._external_channels
                and known is not None
                and (known[1].get("id", 0) or 0) == hold_id
                and self._feeder_eligible.get(channel, False))
            if ((hold_uid is not None
                 and self._channel_uids.get(channel) == hold_uid)
                    or retained_hold):
                spool_id = hold_id
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

    async def _resolve_spool(
            self, channel: int, spool_id: Any = None, card_uid: Any = None,
            expected_uid: Optional[str] = None,
            expected_card_event_time: Optional[float] = None) -> Optional[int]:
        spool_id = spool_id or None
        card_uid = card_uid or None
        if channel is None:
            logging.error("[spoollink] resolve_spool: missing channel")
            return None
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
            return None
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
                return None

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

        if (expected_uid is not None
                and not self._detected_resolution_is_current(
                    channel, expected_uid, expected_card_event_time)):
            logging.info(
                "[spoollink] ch%d: discarding stale resolution for card %s "
                "event=%s", channel, expected_uid,
                expected_card_event_time)
            return None

        return await self._apply_spool(
            channel, spool, card_uid or "", cached=cached)

    async def _apply_spool(self, channel: int, spool: dict, uid_hex: str,
                           cached: bool = False) -> Optional[int]:
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
            if uid_hex:
                self._resolved_uids[channel] = uid_hex
                self._known_spools[channel] = (uid_hex, spool)
            return spool_id
        return None


def load_component(config: ConfigHelper) -> SpoolLink:
    return SpoolLink(config)
