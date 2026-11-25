#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real firmware GUI instructions and nozzle-setting backend methods."""
import ast
import copy
import importlib.util
import logging
import os
from pathlib import Path
import struct
import sys
import types
import unittest

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (
    UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_LR,
    UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_D0,
)

ROOTFS = Path(sys.argv.pop(1))
OVERLAY = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = load_module('patch_gui', OVERLAY / 'patch_gui.py')
GUI = (ROOTFS / 'usr/bin/gui').read_bytes()
PATCHED = patcher.patch_image(GUI)
ORIGINAL = bytearray(PATCHED)
for offset, before, after, description in patcher.PATCHES:
    ORIGINAL[offset:offset + 4] = bytes.fromhex(before)


class Gui:
    PANEL = 0x26E1230
    SELECTION = 0x6A04D0
    STOP = 0x2F00000
    CALLBACK = STOP + 0x100
    BUTTONS = {0.2: 128, 0.4: 136, 0.6: 144, 0.8: 152}

    def __init__(self, image=PATCHED):
        self.cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.cpu.mem_map(0, 0x3000000)
        phoff = struct.unpack_from('<Q', image, 32)[0]
        phsize, phnum = struct.unpack_from('<HH', image, 54)
        for i in range(phnum):
            kind, flags, offset, address, _, size, _, _ = struct.unpack_from(
                '<IIQQQQQQ', image, phoff + i * phsize)
            if kind == 1:
                self.cpu.mem_write(address, bytes(image[offset:offset + size]))
        self.objects = {}
        for offset in [88, 104, 112, 128, 136, 144, 152, 160, 168, 240, 280]:
            pointer = 0x2800000 + offset * 0x100
            self.write(self.PANEL + offset, '<Q', pointer)
            self.objects[pointer] = {'hidden': False, 'state': 0}
        for diameter, offset in self.BUTTONS.items():
            self.objects[self.pointer(offset)]['x'] = 8 + list(self.BUTTONS).index(diameter) * 118
        self.write(self.PANEL + 328, '<Q', self.CALLBACK)
        self.saved = []
        self.hooks = {
            0x8DB00: self.has_flag,
            0x8D240: lambda: self.flag(False),
            0x8E930: lambda: self.flag(True),
            0x8D850: lambda: self.state(True),
            0x8D9A0: lambda: self.state(False),
            0x881C0: self.position,
            0x7DB00: lambda: 7,  # LV_EVENT_CLICKED
            0x7DAF0: lambda: self.clicked,
            0x79ED0: lambda: 0,  # success-message timer
            self.CALLBACK: self.save,
        }
        self.cpu.hook_add(UC_HOOK_CODE, self.hook)

    def write(self, address, fmt, value):
        self.cpu.mem_write(address, struct.pack(fmt, value))

    def read(self, address, fmt):
        return struct.unpack(fmt, self.cpu.mem_read(address, struct.calcsize(fmt)))[0]

    def pointer(self, offset):
        return self.read(self.PANEL + offset, '<Q')

    def args(self):
        return self.cpu.reg_read(UC_ARM64_REG_X0), self.cpu.reg_read(UC_ARM64_REG_X1)

    def has_flag(self):
        pointer, flag = self.args()
        assert flag == 1
        return int(self.objects[pointer]['hidden'])

    def flag(self, hidden):
        pointer, flag = self.args()
        assert flag == 1
        self.objects[pointer]['hidden'] = hidden

    def state(self, enabled):
        pointer, flag = self.args()
        state = self.objects[pointer]['state']
        self.objects[pointer]['state'] = state | flag if enabled else state & ~flag

    def position(self):
        pointer, x = self.args()
        self.objects[pointer]['x'] = x

    def save(self):
        tool, flow = self.args()
        diameter = struct.unpack('<d', struct.pack('<Q', self.cpu.reg_read(UC_ARM64_REG_D0)))[0]
        self.saved.append((tool, diameter, flow))

    def hook(self, cpu, address, size, user_data):
        if address in self.hooks:
            result = self.hooks[address]()
            if result is not None:
                cpu.reg_write(UC_ARM64_REG_X0, result)
            cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))
        elif not (0x119160 <= address < 0x11B310):
            raise AssertionError(f'Unexpected GUI call: {address:#x}')

    def run(self, address):
        self.cpu.reg_write(UC_ARM64_REG_SP, 0x2E00000)
        self.cpu.reg_write(UC_ARM64_REG_LR, self.STOP)
        self.cpu.emu_start(address, self.STOP, count=10000)
        assert self.cpu.reg_read(UC_ARM64_REG_PC) == self.STOP, 'GUI did not return'

    def click(self, offset):
        self.clicked = self.pointer(offset)
        self.cpu.reg_write(UC_ARM64_REG_X0, 0)  # mocked LVGL event
        self.run(0x11AA00)

    def open(self, tool, diameter, flow):
        self.write(self.SELECTION, '<b', tool)
        self.write(self.PANEL + 32 + tool * 8, '<d', diameter)
        self.write(self.PANEL + 16 + tool * 4, '<I', flow)
        self.run(0x1195E0)

    def visible(self):
        return [d for d, offset in self.BUTTONS.items()
                if not self.objects[self.pointer(offset)]['hidden']]


class GuiTests(unittest.TestCase):
    def test_original_reproduces_restriction(self):
        gui = Gui(ORIGINAL)
        gui.open(0, 0.4, 1)
        self.assertEqual(gui.visible(), [0.4])

    def test_visibility_transitions_and_layout(self):
        gui = Gui()
        # Exercise both sides of every visibility check, including buttons
        # hidden by an earlier refresh. Repeated updates must be harmless.
        for flow in [0, 1]:
            for mask in range(16):
                for i, offset in enumerate(gui.BUTTONS.values()):
                    gui.objects[gui.pointer(offset)]['hidden'] = bool(mask & (1 << i))
                gui.open(0, 0.4, flow)
                self.assertEqual(gui.visible(), [0.2, 0.4, 0.6, 0.8])
        for flow in [1, 1, 0, 0, 1, 0]:
            gui.open(0, 0.4, flow)
            self.assertEqual(gui.visible(), [0.2, 0.4, 0.6, 0.8])
            self.assertEqual([gui.objects[gui.pointer(o)]['x'] for o in gui.BUTTONS.values()],
                             [8, 126, 244, 362])

    def test_all_tools_select_save_and_restore(self):
        gui = Gui()
        for tool in range(4):
            for flow in [0, 1]:
                for size in [0.2, 0.4, 0.6, 0.8]:
                    with self.subTest(tool=tool, flow=flow, diameter=size):
                        gui.open(tool, 0.4, 0)
                        gui.click(168 if flow else 160)
                        self.assertIn(size, gui.visible())
                        gui.click(gui.BUTTONS[size])
                        self.assertAlmostEqual(gui.read(gui.SELECTION + 8, '<d'), size)
                        self.assertEqual(gui.read(gui.PANEL + 96, '<I'), flow)
                        gui.click(280)  # confirm/save callback
                        self.assertEqual(gui.saved[-1], (tool, size, flow))
                        gui.open(tool, size, flow)
                        checked = [d for d, offset in gui.BUTTONS.items()
                                   if gui.objects[gui.pointer(offset)]['state'] & 1]
                        self.assertEqual(checked, [size])

    def test_idempotent_and_refuses_unknown_image(self):
        self.assertEqual(patcher.patch_image(PATCHED), PATCHED)
        for offset in [0, 0x119418, 0x119530]:
            damaged = bytearray(ORIGINAL)
            damaged[offset] ^= 0xFF
            with self.assertRaises(ValueError):
                patcher.patch_image(damaged)
        with self.assertRaises(ValueError):
            patcher.patch_image(b'')
        changed = {i for i, (a, b) in enumerate(zip(ORIGINAL, PATCHED)) if a != b}
        allowed = {offset + i for offset, *_ in patcher.PATCHES for i in range(4)}
        self.assertTrue(changed <= allowed)
        self.assertEqual(len(ORIGINAL), len(PATCHED))


# Execute only the real nozzle backend methods, without loading motor/heater
# dependencies. Keep their code and validation constants unchanged.
source = ROOTFS / 'home/lava/klipper/klippy/kinematics/extruder.py'
tree = ast.parse(source.read_text())
extruder_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PrinterExtruder')
method_names = {'_set_nozzle_diameter', '_set_nozzle_volume_type',
                '_handle_control_nozzle_properties', 'cmd_SET_NOZZLE_PROPERTIES'}
methods = [n for n in extruder_class.body if isinstance(n, ast.FunctionDef) and n.name in method_names]
# Run the actual startup configuration load/migration block, stopping before
# heater setup. This catches a saved high-flow choice resetting on restart.
initializer = copy.deepcopy(next(n for n in extruder_class.body
                                if isinstance(n, ast.FunctionDef) and n.name == '__init__'))
end = next(i for i, n in enumerate(initializer.body) if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Attribute) and t.attr == 'nozzle_volume_type' for t in n.targets))
initializer.body = initializer.body[:end + 1]
initializer.name = '_load_nozzle_config'
methods.append(initializer)
method_names.add(initializer.name)
constants = [n for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id.startswith(('VALID_NOZZLE_', 'NOZZLE_CONFIG_')) for t in n.targets)]
namespace = {'logging': logging, 'os': os}
exec(compile(ast.Module(body=constants + methods, type_ignores=[]), str(source), 'exec'), namespace)
Extruder = type('Extruder', (), {n: namespace[n] for n in method_names})


class Request(dict):
    def get_int(self, name, default=None):
        return self.get(name, default)

    get_float = get_int

    def get_raw_parameters(self):
        return dict(self)

    get_raw_command_parameters = get_raw_parameters

    def send(self, result):
        self.response = result

    error = ValueError


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}
        self.stats = types.SimpleNamespace(state='standby')
        self.tools = {}
        printer = types.SimpleNamespace(
            lookup_object=lambda name, default=None: self.tools.get(name, default),
            get_start_args=lambda: {'software_version': '2.0.0'},
            update_snapmaker_config_file=self.persist,
            get_reactor=lambda: None,
            get_snapmaker_config_dir=lambda kind: '/persistent',
            load_snapmaker_config_file=lambda path, default: copy.deepcopy(self.saved.get(path, default)),
        )
        self.tools['print_stats'] = self.stats
        for i in range(4):
            extruder = Extruder()
            extruder.printer = printer
            extruder.extruder_index = i
            extruder.nozzle_diameter = 0.4
            extruder.nozzle_volume_type = 'standard'
            extruder.nozzle_config_info = {'diameter': 0.4, 'diameter_v160': 0.4, 'volume_type': 'standard'}
            name = 'extruder' + (str(i) if i else '')
            extruder.nozzle_config_path = '/persistent/' + name + '_nozzle_config.json'
            self.tools['extruder' + (str(i) if i else '')] = extruder

    def persist(self, path, data):
        self.saved[path] = copy.deepcopy(data)
        return True

    def test_api_and_gcode_persist_both_fields_for_all_tools(self):
        for i in range(4):
            extruder = self.tools['extruder' + (str(i) if i else '')]
            for size in [0.2, 0.6, 0.8]:
                for interface in ['api', 'gcode']:
                    with self.subTest(tool=i, size=size, interface=interface):
                        if interface == 'api':
                            request = Request(extruder=i, diameter=size, volume_type='high_flow')
                            self.tools['extruder']._handle_control_nozzle_properties(request)
                            self.assertEqual(request.response, {'state': 'success'})
                        else:
                            extruder.cmd_SET_NOZZLE_PROPERTIES(Request(DIAMETER=size, VOLUME_TYPE='high_flow'))
                        self.assertEqual(extruder.nozzle_diameter, size)
                        self.assertEqual(extruder.nozzle_volume_type, 'high_flow')
                        self.assertEqual(self.saved[extruder.nozzle_config_path], {
                            'diameter': size, 'diameter_v160': size,
                            'volume_type': 'high_flow', 'version': '2.0.0',
                        })
                        config = types.SimpleNamespace(
                            get_printer=lambda: extruder.printer,
                            get_name=lambda: 'extruder' + (str(i) if i else ''),
                            getfloat=lambda *args, **kwargs: 0.4,
                        )
                        restored = Extruder()
                        restored._load_nozzle_config(config, i)
                        self.assertEqual(restored.nozzle_diameter, size)
                        self.assertEqual(restored.nozzle_volume_type, 'high_flow')

    def test_printing_and_paused_still_reject_changes(self):
        for state in ['printing', 'paused']:
            self.stats.state = state
            request = Request(extruder=0, diameter=0.8, volume_type='high_flow')
            extruder = self.tools['extruder']
            extruder._handle_control_nozzle_properties(request)
            self.assertEqual(request.response['state'], 'error')
            with self.assertRaises(ValueError):
                extruder.cmd_SET_NOZZLE_PROPERTIES(Request(DIAMETER=0.8, VOLUME_TYPE='high_flow'))
            self.assertFalse(self.saved)
            self.assertEqual(extruder.nozzle_diameter, 0.4)

    def test_non_04_nozzles_use_their_own_diameter_defaults(self):
        module = load_module('filament_parameters', ROOTFS / 'home/lava/klipper/klippy/extras/filament_parameters.py')
        params = module.FilamentParameters.__new__(module.FilamentParameters)
        for diameter in ['02', '04', '06', '08']:
            setattr(params, '_config_standard_' + diameter,
                    getattr(module, 'FILAMENT_PARA_CFG_STANDARD_' + diameter + '_DEFAULT'))
        params._config_high_flow_04 = module.FILAMENT_PARA_CFG_HIGH_FLOW_04_DEFAULT
        for size in [0.2, 0.6, 0.8]:
            for material in ['PLA', 'PETG', 'ABS']:
                hf = params.get_filament_parameters('generic', material, 'generic', size, 'high_flow')
                standard = params.get_filament_parameters('generic', material, 'generic', size, 'standard')
                self.assertEqual(hf, standard)
                self.assertGreater(hf['fast_v'], hf['slow_v'])
                self.assertGreater(hf['accel'], 0)


if __name__ == '__main__':
    unittest.main()
