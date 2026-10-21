#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @BICHENG

"""Host-side checks for the fan profile feature.

Run from the repository root:

    python3 overlays/firmware-extended/02-firmware-config/test/test_fan_curves.py

The checks cover the properties the feature has to hold, so the behaviour can
be verified without a printer:

  P1  a nozzle curve may not hold the fan off from 50C upwards
  P2  the power curve never exceeds the stock duty
  P3  the heavy-load rule matches the stock rule
  P4  the profiles are ordered: stock >= quiet >= very quiet
  P5  a running probe holds the fan at probe_speed, like stock
  P6  the cavity temperature guard can still raise the fan to full speed
  P7  a faulty toolhead bypass keeps the nozzle fan off
  P8  the limits in fan_curves.py and in the Klipper patch agree
  P9  a broken profile file falls back to stock behaviour

The Klipper side is exercised by applying the patch to the reference copy of
heater_fan.py in fixtures/ and driving the patched callback directly.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
FAN_CURVES = os.path.join(
    REPO, "overlays", "firmware-extended", "02-firmware-config", "root",
    "usr", "local", "bin", "fan_curves.py")
PATCH = os.path.join(
    REPO, "overlays", "firmware-extended", "11-patch-klipper", "patches",
    "home", "lava", "klipper", "09_heater_fan_fan_curves.patch")
FIXTURE = os.path.join(HERE, "fixtures", "heater_fan_upstream.py")
TWEAKS = os.path.join(
    REPO, "overlays", "firmware-extended", "32-feature-klipper-tweaks", "root",
    "usr", "local", "share", "firmware-config", "tweaks", "klipper")
PRESET_QUIET = os.path.join(TWEAKS, "fan_curves_quiet.json")
PRESET_VERY_QUIET = os.path.join(TWEAKS, "fan_curves_very_quiet.json")

# Stock nozzle fan curve, from the U1 printer.cfg:
#   [heater_fan e0_nozzle_fan] stepped_temp_table
STOCK_NOZZLE_TABLE = [
    (45, 0.3), (60, 0.35), (70, 0.4), (100, 0.45), (140, 0.5), (160, 0.6),
    (180, 0.65), (200, 0.7), (220, 0.75), (230, 0.8), (240, 0.85), (260, 0.9)]
# Stock power fan curve, from the U1 printer.cfg:
#   [heater_fan power_fan] temp_speed_table
#   9999, 260, 1, 3, 1  -> 3 active toolheads and one targeting above 260C
#   9999, 45,  1, 1, 0.6 -> any toolhead targeting above 45C
STOCK_POWER_DUTY = 0.6
STOCK_POWER_HEAVY = {"temperature": 260, "heaters": 3, "duty": 1.0}
# A nozzle fan may not be held off once the hotend is warm enough to matter.
MIN_COOL_TEMPERATURE = 50
MIN_NOZZLE_DUTY = 0.05

_passes = 0
_failures = 0


def check(description, actual, expected):
    global _passes, _failures
    if actual == expected:
        print("  PASS  %s" % description)
        _passes += 1
    else:
        print("  FAIL  %s  (expected %r, got %r)" % (description, expected, actual))
        _failures += 1


def section(title):
    print("\n=== %s ===" % title)


def load_fan_curves():
    spec = importlib.util.spec_from_file_location("fan_curves", FAN_CURVES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_preset(path):
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def stock_step_duty(table, temp):
    """The duty the stock stepped table gives at a temperature.

    The stock table is a step table: a threshold holds its duty until the
    next threshold is reached. It is not interpolated.
    """
    if temp < table[0][0]:
        return 0.0
    duty = table[0][1]
    for threshold, value in table:
        if temp >= threshold:
            duty = value
        else:
            break
    return duty


def profile_duty(points, temp):
    """The duty a profile curve gives at a temperature.

    The Klipper side evaluates a profile the same way it evaluates the stock
    stepped table: a node holds its duty until the next node is reached.
    """
    if temp < points[0][0]:
        return 0.0
    duty = points[0][1]
    for threshold, value in points:
        if temp >= threshold:
            duty = value
        else:
            break
    return duty


def nozzle_points(preset):
    return [(int(t), float(d)) for t, d in preset["curves"]["nozzle"]]


def power_points(preset):
    return [(int(t), float(d)) for t, d in preset["curves"]["power"]]


# ---------------------------------------------------------------- klipper stub


class FakeConfig:
    def __init__(self, name, options, printer):
        self.name = name
        self.options = options
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def getlist(self, key, default=None, seps=None, count=None):
        value = self.options.get(key)
        return default if value is None else value

    def getfloat(self, key, default=None, minval=None, maxval=None):
        value = self.options.get(key)
        return default if value is None else float(value)

    def get(self, key, default=None, seps=None, count=None):
        value = self.options.get(key)
        return default if value is None else value

    def getlists(self, key, default=None, seps=None, count=None, parser=None):
        value = self.options.get(key)
        return default if value is None else value

    def error(self, message):
        raise RuntimeError(message)


class FakeHeater:
    def __init__(self, current=0.0, target=0.0):
        self.current = current
        self.target = target

    def get_temp(self, eventtime):
        return self.current, self.target


class FakeSensor:
    def __init__(self, current=25.0):
        self.current = current

    def get_temp(self, eventtime):
        return self.current, 0.0


class FakeMcu:
    def estimated_print_time(self, curtime):
        return curtime


class FakeFan:
    def __init__(self, config, default_shutdown_speed=0.):
        self.speed = 0.

    def set_speed(self, print_time, value, control_enable=True):
        self.speed = value

    def get_mcu(self):
        return FakeMcu()

    def get_status(self, eventtime):
        return {"speed": self.speed}


class FakeGcode:
    def register_mux_command(self, *args, **kwargs):
        pass


class FakeHeaters:
    def __init__(self, heaters):
        self.heaters = heaters

    def lookup_heater(self, name):
        return self.heaters[name]


class FakeReactor:
    def monotonic(self):
        return 0.0

    def register_timer(self, callback, wakeup_time):
        return None

    def update_timer(self, handle, wakeup_time):
        pass


class FakePrinter:
    def __init__(self, heaters, sensor=None):
        self.heaters = heaters
        self.sensor = sensor
        self.reactor = FakeReactor()

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        if name == "heaters":
            return FakeHeaters(self.heaters)
        if name == "gcode":
            return FakeGcode()
        return default

    def load_object(self, config, name):
        return self.sensor

    def register_event_handler(self, name, callback):
        pass


def patched_heater_fan_module(profile_path):
    """Apply the patch to the reference file and import the result."""
    # The module reads the profile path into a module level constant when it
    # is imported, so the environment has to be set up first.
    os.environ["FAN_CURVES_PATH"] = profile_path
    workdir = tempfile.mkdtemp(prefix="fan-curves-patch-")
    package = os.path.join(workdir, "klippy", "extras")
    os.makedirs(package)
    with open(os.path.join(package, "__init__.py"), "w", encoding="utf-8"):
        pass
    with open(os.path.join(package, "fan.py"), "w", encoding="utf-8") as stream:
        stream.write(
            "class Fan:\n"
            "    def __init__(self, config, default_shutdown_speed=0.):\n"
            "        self.speed = 0.\n"
            "    def set_speed(self, print_time, value, control_enable=True):\n"
            "        self.speed = value\n"
            "    def get_mcu(self):\n"
            "        class Mcu:\n"
            "            def estimated_print_time(self, curtime):\n"
            "                return curtime\n"
            "        return Mcu()\n"
            "    def get_status(self, eventtime):\n"
            "        return {'speed': self.speed}\n")
    with open(FIXTURE, "rb") as stream:
        original = stream.read()
    with open(os.path.join(package, "heater_fan.py"), "wb") as stream:
        stream.write(original)
    result = subprocess.run(
        ["git", "apply", "-p1", PATCH], cwd=workdir, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("patch does not apply: %s"
                           % result.stderr.decode("utf-8", "replace"))
    sys.path.insert(0, workdir)
    try:
        spec = importlib.util.spec_from_file_location(
            "klippy.extras.heater_fan", os.path.join(package, "heater_fan.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["klippy.extras.heater_fan"] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def build_fan(module, name, options, heaters, sensor=None):
    printer = FakePrinter(heaters, sensor)
    config = FakeConfig(name, options, printer)
    fan_object = module.PrinterHeaterFan(config)
    fan_object.heaters = [heaters[key] for key in fan_object.heater_names]
    fan_object.fan_timer = None
    return fan_object


def nozzle_options(heater_temp=45.0, probe_speed=0.5):
    return {
        "heater": ["extruder"],
        "heater_temp": heater_temp,
        "fan_speed": 1,
        "probe_speed": probe_speed,
        "stepped_temp_table": [list(row) for row in STOCK_NOZZLE_TABLE],
        "external_temp_sensor": "cavity",
        "external_temp_guard_range": [[-15.0, 45.0]],
        "external_temp_guard_fan_speed": 1.0,
    }


def power_options():
    return {
        "heater": ["extruder", "extruder1", "extruder2", "extruder3"],
        "shutdown_speed": 0,
        "temp_speed_table": [[9999, 260, 1, 3, 1], [9999, 45, 1, 1, 0.6]],
    }


def write_profile(document, directory):
    path = os.path.join(directory, "fan-curves-active.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(document, stream)
    return path


# ------------------------------------------------------------------- the checks


def check_curve_properties(quiet, very_quiet):
    section("P4  the profiles are ordered: stock >= quiet >= very quiet")
    for temp in range(45, 301):
        stock = stock_step_duty(STOCK_NOZZLE_TABLE, temp)
        quiet_duty = profile_duty(nozzle_points(quiet), temp)
        very_quiet_duty = profile_duty(nozzle_points(very_quiet), temp)
        if quiet_duty > stock + 1e-9 or very_quiet_duty > quiet_duty + 1e-9:
            check("the nozzle duty order holds at %dC" % temp,
                  (stock, quiet_duty, very_quiet_duty), "descending")
            break
    else:
        check("the nozzle duty order holds at every temperature from 45C to 300C",
              True, True)
    check("both profiles run below the stock curve from 50C up",
          all(profile_duty(nozzle_points(quiet), temp)
              < stock_step_duty(STOCK_NOZZLE_TABLE, temp)
              for temp in range(50, 301)), True)
    check("quiet and very quiet are distinct at 210C",
          profile_duty(nozzle_points(quiet), 210)
          > profile_duty(nozzle_points(very_quiet), 210), True)
    check("quiet runs at 48% at 210C",
          round(profile_duty(nozzle_points(quiet), 210), 2), 0.48)
    check("very quiet runs at 25% at 210C",
          round(profile_duty(nozzle_points(very_quiet), 210), 2), 0.25)

    section("P1  a nozzle curve may not hold the fan off from 50C upwards")
    for name, preset in (("quiet", quiet), ("very quiet", very_quiet)):
        worst = min(duty for temp, duty in nozzle_points(preset)
                    if temp >= MIN_COOL_TEMPERATURE)
        check("the %s nozzle curve stays at or above %s from 50C up"
              % (name, MIN_NOZZLE_DUTY), worst >= MIN_NOZZLE_DUTY, True)

    section("P2  the power curve never exceeds the stock duty")
    for name, preset in (("quiet", quiet), ("very quiet", very_quiet)):
        check("the %s power duty stays at or below the stock 60%%" % name,
              max(duty for _, duty in power_points(preset))
              <= STOCK_POWER_DUTY + 1e-9, True)
    check("the quiet power fan starts at 20% at 70C",
          profile_duty(power_points(quiet), 70), 0.2)
    check("the very quiet power fan starts at 10% at 50C",
          profile_duty(power_points(very_quiet), 50), 0.1)

    section("P3  the heavy-load rule matches the stock rule")
    check("the quiet heavy-load rule matches", quiet["heavy_load"], STOCK_POWER_HEAVY)
    check("the very quiet heavy-load rule matches",
          very_quiet["heavy_load"], STOCK_POWER_HEAVY)


def check_validation(quiet, very_quiet, fan_curves):
    section("P1  the floor rejects a nozzle curve that holds the fan off")
    curves = {
        "power": [[45, 0.3], [260, 0.5]],
        "nozzle": [[50, 0.0], [260, 0.9]],
    }
    try:
        fan_curves.normalize_curves(curves)
        check("a zero duty nozzle node at 50C is rejected", "accepted", "rejected")
    except fan_curves.FanCurveError:
        check("a zero duty nozzle node at 50C is rejected", "rejected", "rejected")

    curves["nozzle"] = [[40, 0.0], [260, 0.9]]
    try:
        fan_curves.normalize_curves(curves)
        check("a zero duty nozzle node below 50C is accepted", "accepted", "accepted")
    except fan_curves.FanCurveError:
        check("a zero duty nozzle node below 50C is accepted", "rejected", "accepted")

    section("P1  the shipped profiles pass validation")
    for name, preset in (("quiet", quiet), ("very quiet", very_quiet)):
        try:
            normalized = fan_curves.normalize_curves(preset["curves"])
            check("the %s profile validates" % name, "ok", "ok")
            check("the %s profile keeps its nozzle nodes" % name,
                  len(normalized["nozzle"]), len(preset["curves"]["nozzle"]))
        except fan_curves.FanCurveError as error:
            check("the %s profile validates" % name, str(error), "ok")

    section("the other validation rules")
    for name, curves in (
        ("duty above the ceiling", {"power": [[45, 0.3], [260, 0.95]],
                                    "nozzle": [[50, 0.2], [260, 0.5]]}),
        ("descending temperatures", {"power": [[260, 0.3], [45, 0.5]],
                                    "nozzle": [[50, 0.2], [260, 0.5]]}),
        ("too few nodes", {"power": [[45, 0.3]],
                           "nozzle": [[50, 0.2], [260, 0.5]]}),
    ):
        try:
            fan_curves.normalize_curves(curves)
            check("%s is rejected" % name, "accepted", "rejected")
        except fan_curves.FanCurveError:
            check("%s is rejected" % name, "rejected", "rejected")


def check_patch_limits(fan_curves, module):
    section("P8  the limits in fan_curves.py and in the Klipper patch agree")
    pairs = (
        ("max points", fan_curves.MAX_POINTS, module.FAN_CURVE_MAX_POINTS),
        ("max temperature", fan_curves.MAX_TEMPERATURE, module.FAN_CURVE_MAX_TEMPERATURE),
        ("max duty", fan_curves.MAX_DUTY, module.FAN_CURVE_MAX_DUTY),
        ("min cool temperature", fan_curves.MIN_COOL_TEMPERATURE,
         module.FAN_CURVE_MIN_COOL_TEMPERATURE),
        ("min nozzle duty", fan_curves.MIN_NOZZLE_DUTY,
         module.FAN_CURVE_MIN_NOZZLE_DUTY),
    )
    for name, python_value, patch_value in pairs:
        check("%s agrees" % name, patch_value, python_value)


def check_runtime(quiet, very_quiet, module, profile_path):
    section("P5  the patched nozzle fan follows the profile")
    heater = FakeHeater(current=210.0, target=210.0)
    heaters = {"extruder": heater}
    fan_object = build_fan(module, "heater_fan e0_nozzle_fan",
                           nozzle_options(), heaters, FakeSensor(25.0))
    check("the patched fan loaded the quiet profile",
          fan_object.fan_curve is not None, True)
    fan_object.callback(0.0)
    check("the nozzle duty at 210C is the profile duty",
          round(fan_object.last_speed, 4), 0.48)
    fan_object.callback(0.0)
    check("the nozzle duty at 210C is stable across callbacks",
          round(fan_object.last_speed, 4), 0.48)

    section("the very quiet profile loads on the Klipper side too")
    very_quiet_path = os.path.join(os.path.dirname(profile_path),
                                   "fan-curves-very-quiet.json")
    write_profile(very_quiet, os.path.dirname(profile_path))
    with open(very_quiet_path, "w", encoding="utf-8") as stream:
        json.dump(very_quiet, stream)
    module.FAN_CURVES_PATH = very_quiet_path
    very_heater = FakeHeater(current=210.0, target=210.0)
    very_heaters = {"extruder": very_heater}
    very_object = build_fan(module, "heater_fan e0_nozzle_fan",
                            nozzle_options(), very_heaters, FakeSensor(25.0))
    check("the patched fan loaded the very quiet profile",
          very_object.fan_curve is not None, True)
    very_object.callback(0.0)
    check("the very quiet nozzle duty at 210C is the profile duty",
          round(very_object.last_speed, 4), 0.25)
    module.FAN_CURVES_PATH = profile_path

    section("P5  a running probe holds the fan at probe_speed")
    fan_object.set_probe_speed()
    fan_object.callback(0.0)
    check("the fan is held at probe_speed while probing",
          round(fan_object.last_speed, 4), 0.5)
    fan_object.restore_fan_speed()
    fan_object.callback(0.0)
    check("the fan returns to the profile duty after probing",
          round(fan_object.last_speed, 4), 0.48)

    section("P6  the cavity temperature guard can still raise the fan")
    cold_sensor = FakeSensor(60.0)
    cold_heater = FakeHeater(current=45.0, target=45.0)
    cold_heaters = {"extruder": cold_heater}
    fan_object = build_fan(module, "heater_fan e0_nozzle_fan",
                           nozzle_options(), cold_heaters, cold_sensor)
    fan_object.callback(0.0)
    check("a hot cavity raises the fan to the guard speed",
          round(fan_object.last_speed, 4), 1.0)

    section("P7  a faulty toolhead bypass keeps the nozzle fan off")
    # The bypass rewrites the last stepped_temp_table entry from 260, 0.9 to
    # 260, 0 and raises heater_temp to 999, so the fan stays off at the top of
    # the range. A profile must not take that fan back over.
    bypass_table = [list(row) for row in STOCK_NOZZLE_TABLE]
    bypass_table[-1] = [260, 0]
    bypass_options = nozzle_options(heater_temp=999)
    bypass_options["stepped_temp_table"] = bypass_table
    bypass_heater = FakeHeater(current=260.0, target=260.0)
    bypass_heaters = {"extruder": bypass_heater}
    bypassed = build_fan(module, "heater_fan e0_nozzle_fan",
                         bypass_options, bypass_heaters, FakeSensor(25.0))
    check("the bypassed fan does not load the profile",
          bypassed.fan_curve, None)
    bypassed.callback(0.0)
    check("the bypassed fan stays off at 260C", round(bypassed.last_speed, 4), 0.0)
    check("the profile would have driven the fan instead",
          round(profile_duty(nozzle_points(quiet), 260), 4), 0.6)

    section("P2  the patched power fan keeps the stock heavy-load rule")
    power_heaters = {
        "extruder": FakeHeater(current=210.0, target=210.0),
        "extruder1": FakeHeater(current=210.0, target=210.0),
        "extruder2": FakeHeater(current=210.0, target=210.0),
        "extruder3": FakeHeater(current=210.0, target=210.0),
    }
    power = build_fan(module, "heater_fan power_fan", power_options(),
                      power_heaters, FakeSensor(25.0))
    check("the power fan loaded the profile", power.fan_curve is not None, True)
    power.callback(0.0)
    check("the power fan stays below the stock duty with four heads at 210C",
          power.last_speed < STOCK_POWER_DUTY + 1e-9, True)

    hot_heaters = {
        "extruder": FakeHeater(current=265.0, target=265.0),
        "extruder1": FakeHeater(current=265.0, target=265.0),
        "extruder2": FakeHeater(current=265.0, target=265.0),
        "extruder3": FakeHeater(current=0.0, target=0.0),
    }
    power = build_fan(module, "heater_fan power_fan", power_options(),
                      hot_heaters, FakeSensor(25.0))
    power.callback(0.0)
    check("three heads with one above 260C drive the power fan to the stock 100%",
          round(power.last_speed, 4), 1.0)

    section("P9  a broken profile file falls back to stock")
    with open(profile_path, "w", encoding="utf-8") as stream:
        stream.write("{not json")
    fallback_heater = FakeHeater(current=210.0, target=210.0)
    fallback_heaters = {"extruder": fallback_heater}
    fallback = build_fan(module, "heater_fan e0_nozzle_fan",
                         nozzle_options(), fallback_heaters, FakeSensor(25.0))
    check("a broken profile is ignored", fallback.fan_curve, None)
    fallback.callback(0.0)
    check("a broken profile keeps the stock duty",
          round(fallback.last_speed, 4),
          round(stock_step_duty(STOCK_NOZZLE_TABLE, 210), 4))


def main():
    print("fan profile checks")
    fan_curves = load_fan_curves()
    quiet = load_preset(PRESET_QUIET)
    very_quiet = load_preset(PRESET_VERY_QUIET)

    check_curve_properties(quiet, very_quiet)
    check_validation(quiet, very_quiet, fan_curves)

    workdir = tempfile.mkdtemp(prefix="fan-curves-runtime-")
    profile_path = write_profile(quiet, workdir)
    try:
        module = patched_heater_fan_module(profile_path)
    except Exception as error:
        print("  the Klipper side was skipped: %s" % error)
        module = None
    if module is not None:
        check_patch_limits(fan_curves, module)
        check_runtime(quiet, very_quiet, module, profile_path)

    print("\n=== result ===")
    print("  %d passed, %d failed" % (_passes, _failures))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
