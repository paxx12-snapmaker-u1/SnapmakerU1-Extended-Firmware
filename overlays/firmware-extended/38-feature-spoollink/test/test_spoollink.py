#!/usr/bin/env python3

import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock


COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "root/home/lava/moonraker/moonraker/components/spoollink.py")
SPEC = importlib.util.spec_from_file_location("spoollink_component", COMPONENT)
SPOOLLINK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SPOOLLINK)

OLD_UID = "0C259367"
NEW_UID = "A1B2C3D4"
OLD_TAG = {
    "CARD_UID": [0x0C, 0x25, 0x93, 0x67],
    "VENDOR": "Bambu",
    "MAIN_TYPE": "PLA",
    "SUB_TYPE": "Basic",
    "OFFICIAL": True,
    "SKU": 123,
}
NEW_TAG = {
    "CARD_UID": [0xA1, 0xB2, 0xC3, 0xD4],
    "VENDOR": "Generic",
    "MAIN_TYPE": "PETG",
    "SUB_TYPE": "Basic",
    "OFFICIAL": True,
    "SKU": 456,
}
OLD_SPOOL = {
    "id": 27,
    "filament": {
        "material": "PLA",
        "vendor": {"name": "Bambu"},
        "color_hex": "009BD8",
        "extra": {"variant": '"Tough+"'},
    },
}


class FakeSpoolman:
    def __init__(self):
        self.calls = []

    def set_active_spool(self, spool_id):
        self.calls.append(spool_id)


class FakeKlippyApis:
    pass


class FakeServer:
    error = RuntimeError

    def __init__(self):
        self.spoolman = FakeSpoolman()

    def lookup_component(self, name, default=None):
        components = {
            "http_client": object(),
            "klippy_apis": FakeKlippyApis(),
            "spoolman": self.spoolman,
        }
        return components.get(name, default)

    def register_remote_method(self, *args):
        pass

    def register_event_handler(self, *args):
        pass


class FakeConfig:
    error = RuntimeError

    def __init__(self, external_channels=None):
        self.server = FakeServer()
        self.options = {
            "server": "http://spoolman.test",
            "external_channels": external_channels,
        }

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        value = self.options.get(key)
        return default if value is None else value

    def getintlist(self, key, default=None, separator="\n"):
        value = self.options.get(key)
        if value is None:
            return default
        try:
            return [
                int(item.strip()) for item in value.split(separator)
                if item.strip()
            ]
        except (AttributeError, ValueError) as e:
            raise self.error(
                f"invalid integer list for {key}: {value}") from e


class SpoolLinkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.configure_link()

    def configure_link(self, external_channels=None):
        self.config = FakeConfig(external_channels)
        self.link = SPOOLLINK.SpoolLink(self.config)
        self.link._spoollink_set = AsyncMock(return_value={})

    async def drain(self, cycles=6):
        for _ in range(cycles):
            await asyncio.sleep(0)

    def prime_spool_27(self, channel=2):
        self.link._channel_uids[channel] = OLD_UID
        self.link._channel_signatures[channel] = (
            self.link._filament_signature(OLD_TAG))
        self.link._resolved_uids[channel] = OLD_UID
        self.link._known_spools[channel] = (OLD_UID, OLD_SPOOL)
        self.link._fd_states = [0, 0, 0, 0]
        self.link._ptc_vendors = ["NONE"] * 4
        self.link._ptc_vendors[channel] = "Bambu"
        self.link._ptc_types = ["NONE"] * 4
        self.link._ptc_types[channel] = "PLA"
        self.link._ptc_spool_ids = [0] * 4
        self.link._ptc_spool_ids[channel] = 27
        self.link._toolhead_extruder = f"extruder{channel}"
        self.link._active_spool_id = 27

    def set_feeder(self, present=True, module_exist=True,
                   disable_auto=False, eventtime=0.0, channel=2,
                   channel_state="load_finish",
                   channel_action_state="load_finish"):
        side = "left" if channel < 2 else "right"
        self.link._handle_status_update({
            f"filament_feed {side}": {
                f"extruder{channel}": {
                    "module_exist": module_exist,
                    "filament_detected": present,
                    "disable_auto": disable_auto,
                    "channel_state": channel_state,
                    "channel_action_state": channel_action_state,
                },
            },
        }, eventtime)

    def set_toolhead_sensor(
            self, present, eventtime, channel=2, enabled=True):
        self.link._handle_status_update({
            f"filament_motion_sensor e{channel}_filament": {
                "filament_detected": present,
                "enabled": enabled,
            },
        }, eventtime)

    def qualify_automatic_refresh(self, eventtime=10.0, channel=2):
        self.set_feeder(eventtime=0.0, channel=channel)
        self.set_toolhead_sensor(True, 0.0, channel=channel)
        self.set_toolhead_sensor(False, eventtime, channel=channel)

    def clear_uid_and_spool(
            self, eventtime=10.5, channel=2, clear_metadata=False):
        info = [None] * 4
        info[channel] = {"CARD_UID": 0}
        ptc = {"filament_spool_id": [0, 0, 0, 0]}
        if clear_metadata:
            ptc.update({
                "filament_vendor": ["NONE"] * 4,
                "filament_type": ["NONE"] * 4,
            })
        self.link._handle_status_update({
            "filament_detect": {
                "info": info,
            },
            "print_task_config": ptc,
        }, eventtime)

    def start_reader_refresh(self, eventtime=10.0):
        self.link._handle_status_update({
            "filament_detect": {"state": [0, 0, 1, 0]},
        }, eventtime)

    def finish_reader_refresh(self, eventtime=11.0, info=None):
        fd = {"state": [0, 0, 0, 0]}
        if info is not None:
            fd["info"] = [None, None, info]
        self.link._handle_status_update({
            "filament_detect": fd,
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, eventtime)

    def set_resolver(self, result, gate=None):
        calls = []

        async def resolve(channel, spool_id=None, card_uid=None,
                          expected_uid=None,
                          expected_card_event_time=None):
            calls.append((channel, card_uid, expected_uid))
            if gate is not None:
                await gate.wait()
            return result

        self.link._resolve_spool = resolve
        return calls

    async def test_first_card_read_resolves_once(self):
        calls = self.set_resolver(27)
        self.link._handle_status_update({
            "filament_detect": {"info": [OLD_TAG]},
        }, 1.0)
        await self.drain()
        self.assertEqual(calls, [(0, OLD_UID, OLD_UID)])

    async def test_unchanged_valid_uid_does_not_resolve(self):
        self.prime_spool_27()
        calls = self.set_resolver(27)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, OLD_TAG]},
        }, 5.0)
        await self.drain()
        self.assertEqual(calls, [])

    async def test_same_uid_card_event_resolves_and_holds_active_spool(self):
        self.prime_spool_27()
        gate = asyncio.Event()
        calls = self.set_resolver(27, gate)
        info = dict(OLD_TAG, CARD_EVENT_TIME=100.25)

        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, info]},
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 10.0)
        await self.drain(2)

        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertEqual(self.link._recovery_holds.get(2), (OLD_UID, 27))
        self.assertNotIn(None, self.config.server.spoolman.calls)

        gate.set()
        await self.drain(8)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

    async def test_duplicate_and_older_card_events_are_ignored(self):
        self.prime_spool_27()
        self.link._channel_event_times[2] = 100.25
        calls = self.set_resolver(27)

        for card_event_time in (100.25, 100.0):
            info = dict(OLD_TAG, CARD_EVENT_TIME=card_event_time)
            self.link._handle_status_update({
                "filament_detect": {"info": [None, None, info]},
            }, 10.0)
        await self.drain()
        self.assertEqual(calls, [])

        info = dict(OLD_TAG, CARD_EVENT_TIME=100.5)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, info]},
        }, 10.1)
        await self.drain()
        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])

    async def test_newer_same_uid_event_supersedes_inflight_resolution(self):
        self.prime_spool_27()
        gate = asyncio.Event()
        calls = []

        async def resolve(channel, spool_id=None, card_uid=None,
                          expected_uid=None,
                          expected_card_event_time=None):
            calls.append(expected_card_event_time)
            if expected_card_event_time == 100.25:
                await gate.wait()
            return 27

        self.link._resolve_spool = resolve
        first = dict(OLD_TAG, CARD_EVENT_TIME=100.25)
        second = dict(OLD_TAG, CARD_EVENT_TIME=100.5)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, first]},
        }, 10.0)
        await self.drain(2)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, second]},
        }, 10.1)
        await self.drain(2)
        self.assertEqual(calls, [100.25])

        gate.set()
        await self.drain(10)
        self.assertEqual(calls, [100.25, 100.5])
        self.assertEqual(self.link._resolved_uids.get(2), OLD_UID)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

    async def test_manual_clear_cancels_inflight_card_event_resolution(self):
        self.prime_spool_27()
        gate = asyncio.Event()
        calls = self.set_resolver(27, gate)
        info = dict(OLD_TAG, CARD_EVENT_TIME=100.25)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, info]},
        }, 10.0)
        await self.drain(2)

        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 15.001)
        await self.drain(6)

        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._resolution_tasks, {})
        self.assertEqual(self.link._ptc_spool_ids[2], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])

        gate.set()
        await self.drain(4)
        self.assertEqual(self.link._ptc_spool_ids[2], 0)

    async def test_empty_card_event_cancels_external_resolution(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        gate = asyncio.Event()
        calls = self.set_resolver(27, gate)
        present = dict(OLD_TAG, CARD_EVENT_TIME=100.25)
        self.link._handle_status_update({
            "filament_detect": {
                "info": [None, None, None, present],
            },
        }, 10.0)
        await self.drain(2)

        self.link._handle_status_update({
            "filament_detect": {
                "info": [None, None, None, {
                    "CARD_UID": 0,
                    "CARD_EVENT_TIME": 100.5,
                }],
            },
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 10.1)
        await self.drain(6)

        self.assertEqual(calls, [(3, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._resolution_tasks, {})
        self.assertEqual(self.link._ptc_spool_ids[3], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])
        self.assertNotIn(3, self.link._known_spools)

        gate.set()
        await self.drain(4)
        self.assertEqual(self.link._ptc_spool_ids[3], 0)

    def test_external_channel_configuration_defaults_and_normalizes(self):
        self.assertEqual(self.link._external_channels, set())

        configured = SPOOLLINK.SpoolLink(FakeConfig("3, 1, 3"))

        self.assertEqual(configured._external_channels, {1, 3})

    def test_external_channel_configuration_rejects_invalid_values(self):
        for value in ("-1", "4", "1,4", "invalid"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                        RuntimeError, "external_channels"):
                    SPOOLLINK.SpoolLink(FakeConfig(value))

    async def test_startup_logs_configured_external_channels(self):
        self.configure_link("3,1")
        self.link._ensure_fields = AsyncMock()

        with self.assertLogs(level="INFO") as captured:
            await self.link.component_init()

        self.assertIn(
            "external channels: [1, 3]", "\n".join(captured.output))

    async def test_configured_external_feeder_loss_retains_current_spool(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        self.set_feeder(channel=3)

        with self.assertLogs(level="INFO") as captured:
            self.set_feeder(
                present=False, module_exist=False,
                eventtime=1.0, channel=3)

        self.assertEqual(
            self.link._known_spools.get(3), (OLD_UID, OLD_SPOOL))
        self.assertEqual(self.link._resolved_uids.get(3), OLD_UID)
        messages = "\n".join(captured.output)
        self.assertIn("external_channel=True", messages)
        self.assertIn("module_exist=False", messages)
        self.assertIn("filament_detected=False", messages)
        self.assertIn("disable_auto=False", messages)
        self.assertIn("retain_known_spool=True", messages)

    async def test_external_feeder_loss_requires_current_resolved_spool(self):
        cases = ("empty UID", "unresolved UID", "known UID mismatch")
        for case in cases:
            with self.subTest(case=case):
                self.configure_link("3")
                self.prime_spool_27(channel=3)
                self.set_feeder(channel=3)
                if case == "empty UID":
                    self.link._channel_uids[3] = ""
                elif case == "unresolved UID":
                    self.link._resolved_uids.pop(3)
                else:
                    self.link._known_spools[3] = (NEW_UID, OLD_SPOOL)

                self.set_feeder(
                    present=False, module_exist=False,
                    eventtime=1.0, channel=3)

                self.assertNotIn(3, self.link._known_spools)
                self.assertNotIn(3, self.link._resolved_uids)

    async def test_unconfigured_feeder_loss_forgets_current_spool(self):
        self.prime_spool_27(channel=3)
        self.set_feeder(channel=3)

        self.set_feeder(
            present=False, module_exist=False,
            eventtime=1.0, channel=3)

        self.assertNotIn(3, self.link._known_spools)
        self.assertNotIn(3, self.link._resolved_uids)

    async def test_continuously_ineligible_external_channel_recovers(self):
        self.configure_link("3")
        self.set_feeder(
            present=False, module_exist=False,
            eventtime=0.0, channel=3)
        self.link._toolhead_extruder = "extruder3"

        async def resolve(channel, spool_id=None, card_uid=None,
                          expected_uid=None,
                          expected_card_event_time=None):
            return await self.link._apply_spool(
                channel, OLD_SPOOL, card_uid or "")

        self.link._resolve_spool = resolve
        self.link._handle_status_update({
            "filament_detect": {
                "info": [None, None, None, OLD_TAG],
            },
        }, 10.0)
        await self.drain(8)
        self.assertEqual(self.link._resolved_uids.get(3), OLD_UID)
        self.assertEqual(
            self.link._known_spools.get(3), (OLD_UID, OLD_SPOOL))
        self.assertEqual(self.link._refresh_deadlines.get(3), 15.0)

        self.link._active_spool_id = 27
        self.config.server.spoolman.calls.clear()
        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 10.5)
        await self.drain(10)

        self.assertEqual(self.link._ptc_spool_ids[3], 27)
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

    async def test_external_channel_empty_uid_is_always_a_release(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        self.qualify_automatic_refresh(channel=3)

        self.clear_uid_and_spool(
            channel=3, clear_metadata=True)
        await self.drain(8)

        self.link._spoollink_set.assert_not_awaited()
        self.assertEqual(self.link._ptc_spool_ids[3], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertNotIn(3, self.link._known_spools)

    async def test_external_manual_clear_after_window_remains_cleared(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        self.link._refresh_deadlines[3] = 15.0

        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 15.001)
        await self.drain(8)

        self.link._spoollink_set.assert_not_awaited()
        self.assertEqual(self.link._ptc_spool_ids[3], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertNotIn(3, self.link._known_spools)

    async def test_external_failed_new_uid_resolution_never_restores_old(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        calls = self.set_resolver(None)

        self.link._handle_status_update({
            "filament_detect": {
                "info": [None, None, None, NEW_TAG],
            },
        }, 10.0)
        await self.drain(8)
        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 10.5)
        await self.drain(8)

        self.assertEqual(calls, [(3, NEW_UID, NEW_UID)])
        self.assertEqual(self.link._ptc_spool_ids[3], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertNotIn(3, self.link._known_spools)
        self.assertNotIn(3, self.link._resolved_uids)

    async def test_external_disconnect_clears_only_runtime_state(self):
        self.configure_link("3")
        self.prime_spool_27(channel=3)
        self.set_feeder(channel=3)
        self.set_feeder(
            present=False, module_exist=False,
            eventtime=1.0, channel=3)
        self.assertIn(3, self.link._known_spools)

        self.link._handle_klippy_disconnect()
        await self.drain(4)

        self.assertEqual(self.link._known_spools, {})
        self.assertEqual(self.link._resolved_uids, {})
        self.assertEqual(self.link._channel_event_times, {})
        self.assertEqual(self.link._refresh_deadlines, {})
        self.assertEqual(self.link._feeder_eligible, {})
        self.assertEqual(self.link._external_channels, {3})

    async def test_channel_spool_overrides_manual_active_spool(self):
        self.prime_spool_27(channel=3)

        self.link._handle_active_spool_set({"spool_id": 99})
        await self.drain(4)

        self.assertEqual(self.link._active_spool_id, 27)
        self.assertEqual(self.config.server.spoolman.calls, [27])

    async def test_reader_refresh_recovers_once_and_holds_active_spool(self):
        self.prime_spool_27()
        gate = asyncio.Event()
        calls = self.set_resolver(27, gate)

        self.start_reader_refresh()
        # A hardware read can remain active longer than the recovery window;
        # freshness is measured from completion, not from reader start.
        self.finish_reader_refresh(30.0)
        await self.drain(2)

        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

        # Duplicate status updates must not launch another lookup.
        self.finish_reader_refresh(30.5)
        await self.drain(2)
        self.assertEqual(len(calls), 1)

        gate.set()
        await self.drain()
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

    async def test_cleared_metadata_recovers_external_uid_only_reader(self):
        self.prime_spool_27()
        calls = self.set_resolver(27)
        self.link._handle_status_update({
            "print_task_config": {
                "filament_vendor": ["NONE", "NONE", "NONE", "NONE"],
                "filament_type": ["NONE", "NONE", "NONE", "NONE"],
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 20.0)
        await self.drain()
        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._ptc_spool_ids[2], 27)

    async def test_manual_spool_id_clear_remains_cleared(self):
        self.prime_spool_27()
        calls = self.set_resolver(27)
        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 20.0)
        await self.drain()
        self.assertEqual(calls, [])
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])
        self.assertNotIn(2, self.link._known_spools)

    async def test_different_tag_never_recovers_previous_uid(self):
        self.prime_spool_27()
        calls = self.set_resolver(31)
        self.start_reader_refresh()
        self.finish_reader_refresh(info=NEW_TAG)
        await self.drain()
        self.assertEqual(calls, [(2, NEW_UID, NEW_UID)])
        self.assertEqual(self.link._resolved_uids.get(2), NEW_UID)
        self.assertEqual(self.link._ptc_spool_ids[2], 31)

    async def test_stale_async_result_is_discarded_and_latest_uid_is_queued(self):
        self.prime_spool_27()
        old_gate = asyncio.Event()
        calls = []

        async def resolve(channel, spool_id=None, card_uid=None,
                          expected_uid=None,
                          expected_card_event_time=None):
            calls.append(card_uid)
            if card_uid == OLD_UID:
                await old_gate.wait()
                return 27
            return 31

        self.link._resolve_spool = resolve
        self.start_reader_refresh()
        self.finish_reader_refresh()
        await self.drain(2)

        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, NEW_TAG]},
        }, 12.0)
        await self.drain(2)
        self.assertEqual(calls, [OLD_UID])

        old_gate.set()
        await self.drain(10)
        self.assertEqual(calls, [OLD_UID, NEW_UID])
        self.assertEqual(self.link._resolved_uids.get(2), NEW_UID)
        self.assertEqual(self.link._ptc_spool_ids[2], 31)

    async def test_failed_recovery_releases_active_spool_hold(self):
        self.prime_spool_27()
        calls = self.set_resolver(None)
        self.start_reader_refresh()
        self.finish_reader_refresh()
        await self.drain()
        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])

    async def test_tag_removal_releases_hold_and_stale_result_cannot_restore(self):
        self.prime_spool_27()
        gate = asyncio.Event()
        calls = self.set_resolver(27, gate)
        self.start_reader_refresh()
        self.finish_reader_refresh()
        await self.drain(2)

        self.link._handle_status_update({
            "filament_detect": {
                "info": [None, None, {"CARD_UID": 0}],
            },
        }, 12.0)
        await self.drain(2)
        self.assertEqual(self.link._active_spool_id, 0)

        gate.set()
        await self.drain()
        self.assertNotEqual(self.link._ptc_spool_ids[2], 27)
        self.assertNotIn(2, self.link._resolved_uids)

    async def test_resolve_checks_expected_uid_before_apply(self):
        self.link._channel_uids[2] = OLD_UID
        spool = {"id": 27, "filament": {}}

        async def find_and_replace_tag(card_uid):
            self.link._channel_uids[2] = NEW_UID
            return [spool]

        self.link._spoolman_find_by_card = find_and_replace_tag
        self.link._apply_spool = AsyncMock(return_value=27)
        result = await self.link._resolve_spool(
            2, card_uid=OLD_UID, expected_uid=OLD_UID)
        self.assertIsNone(result)
        self.link._apply_spool.assert_not_awaited()

    async def test_resolve_checks_expected_card_event_before_apply(self):
        self.link._channel_uids[2] = OLD_UID
        self.link._channel_event_times[2] = 100.25
        spool = {"id": 27, "filament": {}}

        async def find_after_newer_event(card_uid):
            self.link._channel_event_times[2] = 100.5
            return [spool]

        self.link._spoolman_find_by_card = find_after_newer_event
        self.link._apply_spool = AsyncMock(return_value=27)
        result = await self.link._resolve_spool(
            2, card_uid=OLD_UID, expected_uid=OLD_UID,
            expected_card_event_time=100.25)
        self.assertIsNone(result)
        self.link._apply_spool.assert_not_awaited()

    async def test_sensor_qualified_uid_loss_restores_cached_spool_and_holds_active(self):
        self.prime_spool_27(channel=3)
        self.qualify_automatic_refresh(channel=3)

        self.clear_uid_and_spool(channel=3)
        await self.drain(10)

        self.assertEqual(self.link._ptc_spool_ids[3], 27)
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)
        self.assertIn(3, self.link._known_spools)

    async def test_expired_sensor_window_logs_recovery_decision_inputs(self):
        self.prime_spool_27()
        self.set_feeder(
            channel_state="loading",
            channel_action_state="feed_to_extruder")
        self.set_toolhead_sensor(True, 0.0)
        self.set_toolhead_sensor(False, 10.0)

        with self.assertLogs(level="INFO") as captured:
            self.clear_uid_and_spool(eventtime=15.001)
        await self.drain(6)

        messages = "\n".join(captured.output)
        self.assertIn(
            "UID clear decision: known_spool_id=27 feeder_eligible=True "
            "feeder_state=loading feeder_action=feed_to_extruder "
            "sensor_evidence=False sensor_window=-0.001s "
            "toolhead_sensor=False selected_toolhead=True "
            "retained_restore=False",
            messages)
        self.assertIn(
            "spool clear decision: old_spool_id=27 uid_present=False "
            "uid_resolved=False known_spool_id=0 feeder_eligible=True "
            "feeder_state=loading feeder_action=feed_to_extruder "
            "sensor_evidence=False sensor_window=none",
            messages)
        self.link._spoollink_set.assert_not_awaited()
        self.assertEqual(self.link._ptc_spool_ids[2], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertNotIn(2, self.link._known_spools)

    async def test_load_heating_combined_clear_recovers_after_sensor_window(self):
        self.prime_spool_27()
        self.set_feeder(
            channel_state="load_heating",
            channel_action_state="load_heating")
        self.set_toolhead_sensor(False, 0.0)
        self.set_toolhead_sensor(True, 10.0)

        with self.assertLogs(level="INFO") as captured:
            self.clear_uid_and_spool(
                eventtime=16.519, clear_metadata=True)
        await self.drain(10)

        messages = "\n".join(captured.output)
        self.assertIn(
            "sensor_evidence=False sensor_window=-1.519s "
            "toolhead_sensor=True selected_toolhead=True "
            "retained_restore=False load_clear_evidence=True",
            messages)
        self.assertIn(
            "automatic read lost UID; restoring spool 27",
            messages)
        self.assertEqual(self.link._spoollink_set.await_count, 1)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)
        self.assertIn(2, self.link._known_spools)

    async def test_combined_clear_requires_all_load_guards(self):
        cases = (
            ("finished load", {
                "state": "load_finish", "action": "load_finish"}),
            ("unknown load", {"state": None, "action": None}),
            ("mismatched action", {"action": "load_finish"}),
            ("sensor absent", {"sensor_present": False}),
            ("sensor disabled", {"sensor_enabled": False}),
            ("other toolhead", {"selected_toolhead": False}),
            ("metadata retained", {"clear_metadata": False}),
            ("automatic load disabled", {"disable_auto": True}),
            ("known spool mismatch", {"known_spool_id": 31}),
        )
        for name, overrides in cases:
            with self.subTest(name=name):
                self.setUp()
                self.prime_spool_27()
                options = {
                    "state": "load_heating",
                    "action": "load_heating",
                    "sensor_present": True,
                    "sensor_enabled": True,
                    "selected_toolhead": True,
                    "clear_metadata": True,
                    "disable_auto": False,
                    "known_spool_id": 27,
                }
                options.update(overrides)
                if options["known_spool_id"] != 27:
                    known_spool = dict(OLD_SPOOL)
                    known_spool["id"] = options["known_spool_id"]
                    self.link._known_spools[2] = (OLD_UID, known_spool)
                self.set_feeder(
                    channel_state=options["state"],
                    channel_action_state=options["action"],
                    disable_auto=options["disable_auto"])
                self.set_toolhead_sensor(
                    not options["sensor_present"], 0.0,
                    enabled=options["sensor_enabled"])
                self.set_toolhead_sensor(
                    options["sensor_present"], 10.0,
                    enabled=options["sensor_enabled"])
                if not options["selected_toolhead"]:
                    self.link._toolhead_extruder = "extruder"

                self.clear_uid_and_spool(
                    eventtime=16.519,
                    clear_metadata=options["clear_metadata"])
                await self.drain(6)

                self.link._spoollink_set.assert_not_awaited()
                self.assertEqual(self.link._ptc_spool_ids[2], 0)
                self.assertEqual(self.link._active_spool_id, 0)
                self.assertNotIn(2, self.link._known_spools)

    async def test_successful_uid_apply_caches_spool_for_continuity(self):
        result = await self.link._apply_spool(2, OLD_SPOOL, OLD_UID)

        self.assertEqual(result, 27)
        self.assertEqual(self.link._known_spools[2], (OLD_UID, OLD_SPOOL))

    async def test_failed_uid_apply_does_not_cache_spool(self):
        self.link._spoollink_set = AsyncMock(return_value=None)

        result = await self.link._apply_spool(2, OLD_SPOOL, OLD_UID)

        self.assertIsNone(result)
        self.assertNotIn(2, self.link._known_spools)

    async def test_retained_restore_does_not_invent_card_uid(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()

        async def echo_set(channel, message, info=None, status="ok"):
            self.link._handle_status_update({
                "filament_detect": {"info": [None, None, info]},
                "print_task_config": {
                    "filament_spool_id": [0, 0, 27, 0],
                },
            }, 10.6)
            return {}

        self.link._spoollink_set = AsyncMock(side_effect=echo_set)

        self.clear_uid_and_spool()
        await self.drain(10)

        calls = self.link._spoollink_set.await_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["info"]["CARD_UID"], [])
        self.assertEqual(calls[0].kwargs["info"]["SPOOL_ID"], 27)
        self.assertIn(2, self.link._known_spools)

    async def test_repeated_automatic_uid_loss_restores_once_per_transition(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        self.clear_uid_and_spool()
        await self.drain(10)

        self.set_toolhead_sensor(True, 20.0)
        self.link._handle_status_update({
            "print_task_config": {
                "filament_spool_id": [0, 0, 0, 0],
            },
        }, 20.5)
        await self.drain(10)

        self.assertEqual(self.link._spoollink_set.await_count, 2)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

    async def test_sensor_evidence_is_consumed_after_restore(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        self.clear_uid_and_spool()
        await self.drain(10)

        self.clear_uid_and_spool(eventtime=11.0)
        await self.drain(10)

        self.assertEqual(self.link._spoollink_set.await_count, 1)
        self.assertEqual(self.link._ptc_spool_ids[2], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])
        self.assertNotIn(2, self.link._known_spools)

    async def test_duplicate_empty_uid_update_does_not_cancel_or_repeat_restore(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        gate = asyncio.Event()

        async def delayed_set(*args, **kwargs):
            await gate.wait()
            return {}

        self.link._spoollink_set = AsyncMock(side_effect=delayed_set)
        self.clear_uid_and_spool()
        await self.drain(2)

        self.clear_uid_and_spool(eventtime=10.6)
        await self.drain(2)

        self.assertEqual(self.link._spoollink_set.await_count, 1)
        self.assertIn(2, self.link._known_spools)
        self.assertEqual(self.link._recovery_holds.get(2), (None, 27))

        gate.set()
        await self.drain(6)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertEqual(self.link._active_spool_id, 27)

    async def test_failed_retained_restore_releases_hold_and_forgets_spool(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        self.link._spoollink_set = AsyncMock(return_value=None)

        self.clear_uid_and_spool()
        await self.drain(10)

        self.assertEqual(self.link._ptc_spool_ids[2], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertEqual(self.config.server.spoolman.calls, [None])
        self.assertNotIn(2, self.link._known_spools)
        self.assertNotIn(2, self.link._recovery_holds)

    async def test_same_uid_return_keeps_hold_until_resolution_finishes(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        restore_gate = asyncio.Event()

        async def delayed_set(*args, **kwargs):
            await restore_gate.wait()
            return {}

        self.link._spoollink_set = AsyncMock(side_effect=delayed_set)
        self.clear_uid_and_spool()
        await self.drain(2)

        resolve_gate = asyncio.Event()
        calls = self.set_resolver(27, resolve_gate)
        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, OLD_TAG]},
        }, 10.7)
        await self.drain(2)

        self.assertEqual(calls, [(2, OLD_UID, OLD_UID)])
        self.assertEqual(self.link._active_spool_id, 27)
        self.assertNotIn(None, self.config.server.spoolman.calls)

        resolve_gate.set()
        await self.drain(6)
        self.assertEqual(self.link._ptc_spool_ids[2], 27)
        self.assertEqual(self.link._active_spool_id, 27)

    async def test_feeder_removal_cancels_pending_retained_restore(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        gate = asyncio.Event()

        async def delayed_set(*args, **kwargs):
            await gate.wait()
            return {}

        self.link._spoollink_set = AsyncMock(side_effect=delayed_set)
        self.clear_uid_and_spool()
        await self.drain(2)

        self.set_feeder(present=False, eventtime=11.0)
        await self.drain(4)
        gate.set()
        await self.drain(4)

        self.assertEqual(self.link._ptc_spool_ids[2], 0)
        self.assertEqual(self.link._active_spool_id, 0)
        self.assertNotIn(2, self.link._known_spools)
        self.assertNotIn(2, self.link._retention_tasks)

    async def test_different_uid_cancels_retained_spool(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        self.clear_uid_and_spool()
        await self.drain(10)
        calls = self.set_resolver(31)

        self.link._handle_status_update({
            "filament_detect": {"info": [None, None, NEW_TAG]},
        }, 12.0)
        await self.drain(10)

        self.assertEqual(calls, [(2, NEW_UID, NEW_UID)])
        self.assertNotIn(2, self.link._known_spools)

    async def test_unqualified_feeder_never_retains_spool(self):
        invalid_states = (
            (False, True, False),
            (True, False, False),
            (True, True, True),
        )
        for module_exist, present, disable_auto in invalid_states:
            with self.subTest(module_exist=module_exist, present=present,
                              disable_auto=disable_auto):
                self.setUp()
                self.prime_spool_27()
                self.set_feeder(
                    present=present,
                    module_exist=module_exist,
                    disable_auto=disable_auto,
                    eventtime=0.0)
                self.set_toolhead_sensor(True, 0.0)
                self.set_toolhead_sensor(False, 10.0)

                self.clear_uid_and_spool()
                await self.drain(6)

                self.assertEqual(self.link._spoollink_set.await_count, 0)
                self.assertEqual(self.link._ptc_spool_ids[2], 0)
                self.assertNotIn(2, self.link._known_spools)

    async def test_disabled_or_other_toolhead_sensor_never_qualifies_channel(self):
        updates = (
            {"filament_motion_sensor e2_filament": {
                "filament_detected": False, "enabled": False}},
            {"filament_motion_sensor e1_filament": {
                "filament_detected": False, "enabled": True}},
        )
        for update in updates:
            with self.subTest(update=update):
                self.setUp()
                self.prime_spool_27()
                self.set_feeder(eventtime=0.0)
                self.set_toolhead_sensor(True, 0.0)
                self.link._handle_status_update(update, 10.0)

                self.clear_uid_and_spool()
                await self.drain(6)

                self.assertEqual(self.link._spoollink_set.await_count, 0)
                self.assertEqual(self.link._ptc_spool_ids[2], 0)
                self.assertNotIn(2, self.link._known_spools)

    async def test_disconnect_clears_retained_state_and_tasks(self):
        self.prime_spool_27()
        self.qualify_automatic_refresh()
        gate = asyncio.Event()

        async def delayed_set(*args, **kwargs):
            await gate.wait()
            return {}

        self.link._spoollink_set = AsyncMock(side_effect=delayed_set)
        self.clear_uid_and_spool()
        await self.drain(2)

        self.link._handle_klippy_disconnect()
        await self.drain(4)

        self.assertEqual(self.link._known_spools, {})
        self.assertEqual(self.link._retention_tasks, {})
        self.assertEqual(self.link._channel_event_times, {})
        self.assertEqual(self.link._resolution_task_event_times, {})
        self.assertEqual(self.link._feeder_eligible, {})
        self.assertEqual(self.link._toolhead_sensors, {})


if __name__ == "__main__":
    unittest.main()
