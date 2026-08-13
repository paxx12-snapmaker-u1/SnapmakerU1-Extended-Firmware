from __future__ import annotations
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest

logger = logging.getLogger(__name__)


class SchedulePrint:

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self._timer_handle = None
        self._scheduled: Optional[Dict[str, Any]] = None

        file_manager = self.server.lookup_component("file_manager")
        self._state_file: Path = (
            file_manager.datapath / "config" / "extended" / "schedule_print.json"
        )

        self.server.register_endpoint(
            "/server/schedule_print",
            ["GET", "POST", "DELETE"],
            self._handle_request,
        )
        self.server.register_event_handler(
            "server:klippy_ready", self._on_klippy_ready
        )
        self._load_state()

    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_file.read_text())
        except FileNotFoundError:
            return
        except (ValueError, KeyError):
            logger.warning("schedule_print: invalid persisted state, ignored")
            return
        if data.get("target_ts", 0) > time.time():
            self._scheduled = data
            self._arm()
        else:
            self._clear_state()

    def _save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._scheduled))

    def _clear_state(self) -> None:
        self._scheduled = None
        try:
            self._state_file.unlink()
        except FileNotFoundError:
            pass

    def _arm(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle.cancel()
            self._timer_handle = None
        if not self._scheduled:
            return
        delay = max(0.0, self._scheduled["target_ts"] - time.time())
        self._timer_handle = self.eventloop.delay_callback(delay, self._fire)
        logger.info(
            "schedule_print: armed for '%s' in %ds",
            self._scheduled["filename"],
            int(delay),
        )

    async def _fire(self) -> None:
        self._timer_handle = None
        sched = self._scheduled
        if not sched:
            return
        filename = sched["filename"]
        extruder_map = sched.get("extruder_map")
        preferences = sched.get("preferences")
        self._clear_state()
        kapis = self.server.lookup_component("klippy_apis")
        await self._apply_preferences(kapis, preferences)
        if extruder_map:
            for logical_idx, physical_idx in enumerate(extruder_map):
                if physical_idx is None:
                    continue
                try:
                    await kapis.run_gcode(
                        f"SET_PRINT_EXTRUDER_MAP CONFIG_EXTRUDER={logical_idx} MAP_EXTRUDER={physical_idx}"
                    )
                    logger.info(
                        "schedule_print: remapped T%d → physical extruder %d", logical_idx, physical_idx
                    )
                except Exception:
                    logger.exception(
                        "schedule_print: SET_PRINT_EXTRUDER_MAP failed for T%d → %d, proceeding anyway",
                        logical_idx, physical_idx,
                    )
        try:
            await kapis.start_print(filename)
            logger.info("schedule_print: print started '%s'", filename)
        except Exception:
            logger.exception("schedule_print: failed to start print '%s'", filename)

    async def _apply_preferences(self, kapis, preferences) -> None:
        # Opt-in only: a preference is forced ON when set, never forced off.
        # Runs while the printer is idle (before start_print), so
        # SET_PRINT_PREFERENCES is allowed by the firmware.
        if not preferences:
            return
        params = []
        if preferences.get("bed_level"):
            params.append("BED_LEVEL=1")
        if preferences.get("flow_calibrate"):
            params.append("FLOW_CALIBRATE=1")
        if preferences.get("timelapse"):
            params.append("TIME_LAPSE_CAMERA=1")
        if not params:
            return
        command = "SET_PRINT_PREFERENCES " + " ".join(params)
        try:
            await kapis.run_gcode(command)
            logger.info("schedule_print: applied preferences (%s)", " ".join(params))
        except Exception:
            logger.exception(
                "schedule_print: SET_PRINT_PREFERENCES failed, proceeding anyway"
            )

    def _on_klippy_ready(self) -> None:
        if self._scheduled:
            self._arm()

    async def _handle_request(self, web_request: WebRequest) -> Dict[str, Any]:
        action = web_request.get_action()
        if action == "GET":
            return self._status()
        if action == "DELETE":
            was_scheduled = self._scheduled is not None
            self._clear_state()
            self._arm()
            return {"cancelled": was_scheduled}
        # POST
        filename = web_request.get_str("filename")
        time_str = web_request.get_str("time")
        timezone = web_request.get_str("timezone", default="UTC")
        extruder_map_str = web_request.get_str("extruder_map", default=None)
        extruder_map = json.loads(extruder_map_str) if extruder_map_str else None
        preferences = self._parse_preferences(web_request)
        target_ts = self._parse_time(time_str)
        self._scheduled = {
            "filename": filename,
            "target_ts": target_ts,
            "timezone": timezone,
            "extruder_map": extruder_map,
            "preferences": preferences,
        }
        self._save_state()
        self._arm()
        return self._status()

    def _status(self) -> Dict[str, Any]:
        if not self._scheduled:
            return {"scheduled": None}
        return {
            "scheduled": {
                "filename": self._scheduled["filename"],
                "target_ts": self._scheduled["target_ts"],
                "timezone": self._scheduled.get("timezone", "UTC"),
                "extruder_map": self._scheduled.get("extruder_map"),
                "preferences": self._scheduled.get("preferences"),
                "seconds_remaining": int(
                    self._scheduled["target_ts"] - time.time()
                ),
            }
        }

    def _parse_preferences(
        self, web_request: WebRequest
    ) -> Optional[Dict[str, bool]]:
        bed_level = web_request.get_boolean("bed_level", default=None)
        flow_calibrate = web_request.get_boolean("flow_calibrate", default=None)
        timelapse = web_request.get_boolean("timelapse", default=None)
        if bed_level is None and flow_calibrate is None and timelapse is None:
            return None
        return {
            "bed_level": bool(bed_level),
            "flow_calibrate": bool(flow_calibrate),
            "timelapse": bool(timelapse),
        }

    def _parse_time(self, time_str: str) -> float:
        try:
            dt = datetime.fromisoformat(time_str)
        except ValueError:
            raise self.server.error(
                f"Invalid time '{time_str}', expected ISO 8601 with UTC offset"
                f" (e.g. 2026-06-27T07:30:00+02:00)",
                400,
            )
        if dt.tzinfo is None:
            raise self.server.error(
                f"Time '{time_str}' is missing UTC offset (e.g. +02:00)",
                400,
            )
        ts = dt.timestamp()
        if ts <= time.time():
            raise self.server.error(
                f"Scheduled time '{time_str}' is in the past", 400
            )
        return ts


def load_component(config: ConfigHelper) -> SchedulePrint:
    return SchedulePrint(config)
