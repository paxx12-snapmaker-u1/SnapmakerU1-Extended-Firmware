"""Host regressions against patched firmware, without importing hardware drivers.

Usage: python3 test_filament_clear.py /path/to/patched/klippy/extras
"""
import ast
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

EXTRAS = Path(sys.argv.pop(1))


def load_source(name, **dependencies):
    path = EXTRAS / (name + '.py')
    tree = ast.parse(path.read_text(), filename=str(path))
    # Keep the actual firmware implementation; inject only relative imports.
    tree.body = [node for node in tree.body
                 if not isinstance(node, ast.ImportFrom) or not node.level]
    namespace = dict(dependencies)
    exec(compile(tree, str(path), 'exec'), namespace)
    return SimpleNamespace(**namespace)


PROTOCOL = load_source('filament_protocol')
PTC = load_source('print_task_config', filament_feed=Mock())
DETECT = load_source('filament_detect', filament_protocol=PROTOCOL,
                     filament_protocol_ndef=Mock(), fm175xx_reader=Mock())


class FilamentClearTests(unittest.TestCase):
    def setUp(self):
        self.ptc = PTC.PrintTaskConfig.__new__(PTC.PrintTaskConfig)
        self.ptc.print_task_config = copy.deepcopy(PTC.DEFAULT_PRINT_TASK_CONFIG)
        self.ptc.printer = Mock()
        self.ptc.printer.lookup_object.return_value.has_remote_method.return_value = True
        self.ptc.filament_param_obj = None
        self.ptc.config_path = '/unused'
        self.ptc.gcode = Mock()
        self.ptc.backup_filament_info = Mock()
        state = self.ptc.print_task_config
        state['filament_vendor'][0] = 'Tagged spool'
        state['filament_type'][0] = 'PLA'
        state['filament_official'][0] = True
        state['filament_sku'][0] = 123
        state['filament_spool_id'][0] = 27
        state['filament_exist'][0] = True
        self.callbacks = []
        self.detector = DETECT.FilamentDetector.__new__(DETECT.FilamentDetector)
        self.detector.reactor = SimpleNamespace(
            monotonic=lambda: 10., register_async_callback=self.callbacks.append)
        self.detector._channel_nums = 4
        self.detector._state = [0] * 4
        self.detector._filament_info = [dict(PROTOCOL.FILAMENT_INFO_STRUCT) for _ in range(4)]
        self.detector._notify_data_update_cb = [self.ptc._rfid_filament_info_update_cb]
        self.detector._fm175xx_reader = Mock()

    def flush(self):
        while self.callbacks:
            self.callbacks.pop(0)(10.)

    def test_clear_then_read_without_reader_clear_callback_unlocks_replacement(self):
        # The real reader cancels its pending clear when a read is requested.
        # Deliberately deliver no reader clear callback here.
        other_slots = {key: copy.deepcopy(value[1:])
                       for key, value in self.ptc.print_task_config.items()
                       if key.startswith('filament_') and isinstance(value, list)}
        feeder = Mock()
        feeder.get_status.return_value = {'extruder0': {
            'module_exist': True, 'disable_auto': False, 'filament_detected': True}}
        self.detector.filament_feed_objects = [('filament_feed left', feeder)]
        self.detector._runout_evt_handle(0, False)
        self.detector._filament_info_update(0, dict(PROTOCOL.FILAMENT_INFO_STRUCT))
        self.flush()
        self.ptc.update_filament_edit_flag()
        state = self.ptc.print_task_config
        self.assertEqual(state['filament_spool_id'][0], 0)
        self.assertFalse(state['filament_official'][0])
        self.assertTrue(state['filament_edit'][0])
        for key, value in other_slots.items():
            if key != 'filament_edit':
                self.assertEqual(state[key][1:], value, key)
        # Exercise the actual manual-edit command without FORCE.
        params = {'CONFIG_EXTRUDER': 0, 'VENDOR': 'Generic',
                  'FILAMENT_TYPE': 'PETG', 'FILAMENT_SUBTYPE': 'Basic',
                  'FILAMENT_COLOR_RGBA': '123456FF'}
        gcmd = Mock()
        gcmd.get.side_effect = lambda key, default=None, **kw: params.get(key, default)
        gcmd.get_int.side_effect = gcmd.get.side_effect
        gcmd.error.side_effect = RuntimeError
        self.ptc.cmd_SET_PRINT_FILAMENT_CONFIG(gcmd)
        self.assertEqual(self.ptc.print_task_config['filament_type'][0], 'PETG')
        self.assertEqual(self.ptc.print_task_config['filament_color_rgba'][0], '123456FF')

    def test_cleared_empty_slot_is_editable_before_loading(self):
        self.ptc.print_task_config['filament_exist'][0] = False
        self.detector.request_clear_filament_info(0)
        self.flush()
        self.ptc.update_filament_edit_flag()
        self.assertTrue(self.ptc.print_task_config['filament_edit'][0])

    def test_assigned_or_official_slot_stays_locked(self):
        for official, spool_id in ((True, 0), (False, 27)):
            with self.subTest(official=official, spool_id=spool_id):
                self.ptc.print_task_config['filament_official'][0] = official
                self.ptc.print_task_config['filament_spool_id'][0] = spool_id
                self.ptc.update_filament_edit_flag()
                self.assertFalse(self.ptc.print_task_config['filament_edit'][0])

    def test_clear_with_disabled_reader_releases_spool(self):
        self.detector._fm175xx_reader = None
        self.detector.request_clear_filament_info(0)
        self.flush()
        self.assertEqual(self.ptc.print_task_config['filament_spool_id'][0], 0)
        self.assertEqual(self.detector._state[0], DETECT.FILAMENT_DT_STATE_IDLE)

    def test_tagless_refresh_preserves_manual_assignment(self):
        for vendor in ('NONE', 'Generic'):
            with self.subTest(vendor=vendor):
                self.ptc.print_task_config['filament_vendor'][0] = vendor
                self.ptc.print_task_config['filament_official'][0] = False
                self.ptc._rfid_filament_info_update_cb(0, dict(PROTOCOL.FILAMENT_INFO_STRUCT))
                self.assertEqual(self.ptc.print_task_config['filament_spool_id'][0], 27)

    def test_spoollink_explicit_assignment_takes_precedence(self):
        info = dict(PROTOCOL.FILAMENT_INFO_STRUCT, SPOOL_ID=42)
        self.ptc._rfid_filament_info_update_cb(0, info, is_clear=True)
        self.assertEqual(self.ptc.print_task_config['filament_spool_id'][0], 42)

    def test_new_tag_releases_previous_spool(self):
        info = dict(PROTOCOL.FILAMENT_INFO_STRUCT, OFFICIAL=True,
                    SKU=456, CARD_UID=[1, 2, 3, 4])
        self.ptc._rfid_filament_info_update_cb(0, info)
        self.assertEqual(self.ptc.print_task_config['filament_spool_id'][0], 0)


if __name__ == '__main__':
    unittest.main()
