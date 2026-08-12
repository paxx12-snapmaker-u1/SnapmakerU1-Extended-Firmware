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
    def __init__(self):
        self.server = FakeServer()

    def get_server(self):
        return self.server

    def get(self, key, default=None):
        return "http://spoolman.test" if key == "server" else default


class SpoolLinkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = FakeConfig()
        self.link = SPOOLLINK.SpoolLink(self.config)

    async def drain(self, cycles=6):
        for _ in range(cycles):
            await asyncio.sleep(0)

    def prime_spool_27(self):
        self.link._channel_uids[2] = OLD_UID
        self.link._channel_signatures[2] = (
            self.link._filament_signature(OLD_TAG))
        self.link._resolved_uids[2] = OLD_UID
        self.link._fd_states = [0, 0, 0, 0]
        self.link._ptc_vendors = ["NONE", "NONE", "Bambu", "NONE"]
        self.link._ptc_types = ["NONE", "NONE", "PLA", "NONE"]
        self.link._ptc_spool_ids = [0, 0, 27, 0]
        self.link._toolhead_extruder = "extruder2"
        self.link._active_spool_id = 27

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
                          expected_uid=None):
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
                          expected_uid=None):
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


if __name__ == "__main__":
    unittest.main()
