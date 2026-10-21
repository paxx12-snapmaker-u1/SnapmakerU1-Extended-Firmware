"""Fan profiles for the Snapmaker U1 extended firmware.

Firmware Config writes the active profile to a JSON file; the Klipper patch
`09_heater_fan_fan_curves.patch` reads the same file. This module owns the
validation rules, so a profile that reaches the device has already been
checked. The Klipper side keeps a defensive copy of the limits and falls back
to stock behaviour when a file fails any of them.

The limits below and the ones in the Klipper patch must stay in agreement.
`test_fan_curves.py` checks that they do.
"""
import argparse
import json
import math
import os
import subprocess
import tempfile

CONFIG_DIR = os.environ.get(
    'FAN_CURVES_CONFIG_DIR', '/oem/printer_data/config/extended')
ACTIVE_PATH = os.path.join(CONFIG_DIR, 'fan-curves-active.json')
PRESET_DIR = os.environ.get(
    'FAN_CURVES_PRESET_DIR', '/usr/local/share/firmware-config/tweaks/klipper')

PRESETS = {'quiet': 'fan_curves_quiet.json',
           'very_quiet': 'fan_curves_very_quiet.json'}
PROFILES = ('stock', 'quiet', 'very_quiet')

# Limits. The Klipper patch keeps a copy of these numbers.
MAX_POINTS = 16
MIN_POINTS = 2
MAX_TEMPERATURE = 300
MAX_DUTY = 0.90
# A nozzle fan must not be held off once the hotend is warm enough to matter.
MIN_COOL_TEMPERATURE = 50
MIN_NOZZLE_DUTY = 0.05
DEFAULT_HYSTERESIS = 5.0


class FanCurveError(ValueError):
    pass


def _finite_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise FanCurveError('%s must be a number' % name)
    if not math.isfinite(number):
        raise FanCurveError('%s must be finite' % name)
    return number


def normalize_curves(value):
    """Validate a pair of curves and return them in normalized form."""
    if not isinstance(value, dict):
        raise FanCurveError('curves must be an object')
    normalized = {}
    for name in ('power', 'nozzle'):
        points = value.get(name)
        if not isinstance(points, list):
            raise FanCurveError('%s must be an array' % name)
        if len(points) < MIN_POINTS or len(points) > MAX_POINTS:
            raise FanCurveError(
                '%s must contain %d-%d nodes' % (name, MIN_POINTS, MAX_POINTS))
        output = []
        previous_temperature = None
        for index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise FanCurveError('%s node %d must be [temperature, duty]' %
                                    (name, index + 1))
            temperature = _finite_number(point[0], '%s temperature' % name)
            duty = _finite_number(point[1], '%s duty' % name)
            if temperature != int(temperature) or not 0 <= temperature <= MAX_TEMPERATURE:
                raise FanCurveError(
                    '%s temperature must be an integer from 0 to %d'
                    % (name, MAX_TEMPERATURE))
            if not 0 <= duty <= MAX_DUTY:
                raise FanCurveError(
                    '%s duty must be between 0 and %.2f' % (name, MAX_DUTY))
            temperature = int(temperature)
            if previous_temperature is not None and temperature <= previous_temperature:
                raise FanCurveError(
                    '%s temperatures must increase strictly' % name)
            if (name == 'nozzle' and temperature >= MIN_COOL_TEMPERATURE
                    and duty < MIN_NOZZLE_DUTY):
                raise FanCurveError(
                    '%s duty must be at least %.2f from %dC upwards'
                    % (name, MIN_NOZZLE_DUTY, MIN_COOL_TEMPERATURE))
            output.append([temperature, round(duty, 3)])
            previous_temperature = temperature
        normalized[name] = output
    return normalized


def _normalize_heavy_load(value):
    if not isinstance(value, dict):
        value = {}
    temperature = _finite_number(value.get('temperature', 260), 'heavy-load temperature')
    heaters = _finite_number(value.get('heaters', 3), 'heavy-load heater count')
    duty = _finite_number(value.get('duty', 1.0), 'heavy-load duty')
    if not 0 <= temperature <= MAX_TEMPERATURE:
        raise FanCurveError(
            'heavy-load temperature must be from 0 to %d' % MAX_TEMPERATURE)
    if heaters != int(heaters) or heaters < 1:
        raise FanCurveError('heavy-load heater count must be a positive integer')
    if not 0 <= duty <= 1:
        raise FanCurveError('heavy-load duty must be between 0 and 1')
    return {
        'temperature': int(temperature),
        'heaters': int(heaters),
        'duty': duty,
    }


def normalize_document(value, profile=None):
    if not isinstance(value, dict):
        raise FanCurveError('fan profile state must be an object')
    if value.get('version') != 1:
        raise FanCurveError('unsupported fan profile version')
    profile = str(profile or value.get('profile') or '').strip().lower()
    if profile not in PROFILES:
        raise FanCurveError('unknown fan profile: %s' % profile)
    if profile == 'stock':
        return {'version': 1, 'profile': 'stock'}
    hysteresis = _finite_number(
        value.get('hysteresis', DEFAULT_HYSTERESIS), 'hysteresis')
    if not 0 <= hysteresis <= MAX_TEMPERATURE:
        raise FanCurveError('hysteresis must be from 0 to %d' % MAX_TEMPERATURE)
    return {
        'version': 1,
        'profile': profile,
        'curves': normalize_curves(value.get('curves')),
        'hysteresis': hysteresis,
        'heavy_load': _normalize_heavy_load(value.get('heavy_load')),
    }


def _read_json(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


def _atomic_bytes(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.fan-curves-', dir=directory)
    try:
        # The Klipper process reads this file as its own user.
        os.chmod(temporary, 0o644)
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_json(path, value):
    _atomic_bytes(path, (json.dumps(value, indent=2) + '\n').encode('utf-8'))


def load_preset(profile):
    path = os.path.join(PRESET_DIR, PRESETS[profile])
    try:
        return normalize_document(_read_json(path), profile)
    except OSError as error:
        raise FanCurveError('cannot load %s preset: %s' % (profile, error))
    except (TypeError, ValueError) as error:
        raise FanCurveError('invalid %s preset: %s' % (profile, error))


def current_profile():
    """The profile that is currently active, or 'stock' when it cannot be read."""
    try:
        with open(ACTIVE_PATH, 'r', encoding='utf-8') as stream:
            document = json.load(stream)
        return normalize_document(document)['profile']
    except (OSError, TypeError, ValueError):
        return 'stock'


def _restart_klipper():
    return subprocess.run(
        ['/etc/init.d/S60klipper', 'restart'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120).returncode


def apply_profile(profile):
    """Write the profile and restart Klipper, rolling back on failure."""
    profile = str(profile or '').strip().lower()
    if profile not in PROFILES:
        raise FanCurveError('unknown fan profile: %s' % profile)
    document = (load_preset(profile) if profile != 'stock'
                else {'version': 1, 'profile': 'stock'})
    try:
        with open(ACTIVE_PATH, 'rb') as stream:
            previous = stream.read()
    except FileNotFoundError:
        previous = None
    _atomic_json(ACTIVE_PATH, document)
    try:
        result = _restart_klipper()
    except Exception:
        _restore(ACTIVE_PATH, previous)
        raise
    if result != 0:
        _restore(ACTIVE_PATH, previous)
        raise FanCurveError('Klipper restart failed')
    return profile


def _restore(path, previous):
    if previous is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    else:
        _atomic_bytes(path, previous)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('apply', 'profile'))
    parser.add_argument('profile', nargs='?')
    args = parser.parse_args()
    if args.command == 'profile':
        print(current_profile())
        return
    if not args.profile:
        parser.error('apply needs a profile')
    apply_profile(args.profile)


if __name__ == '__main__':
    main()
