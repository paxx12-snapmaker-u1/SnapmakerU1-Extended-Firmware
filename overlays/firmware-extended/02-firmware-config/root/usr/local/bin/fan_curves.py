import json
import math
import os
import subprocess
import tempfile


CONFIG_DIR = os.environ.get(
    'FAN_CURVES_CONFIG_DIR', '/oem/printer_data/config/extended')
ACTIVE_PATH = os.path.join(CONFIG_DIR, 'fan-curves-active.json')
CUSTOM_PATH = os.path.join(CONFIG_DIR, 'fan-curves.json')
PRESET_DIR = os.environ.get(
    'FAN_CURVES_PRESET_DIR', '/usr/local/share/firmware-config/tweaks/klipper')
PRESETS = {
    'quiet': 'fan_curves_quiet.json',
    'very_quiet': 'fan_curves_very_quiet.json',
}
MAX_POINTS = 16
MIN_POINTS = 2
MAX_TEMPERATURE = 300
MAX_DUTY = 0.60
DEFAULT_HYSTERESIS = 5.0
PROFILES = ('stock', 'quiet', 'very_quiet', 'custom')
ALIASES = {
    'balanced': 'quiet',
    'lazy': 'very_quiet',
    'quiet_experimental': 'very_quiet',
}


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
    if not isinstance(value, dict):
        raise FanCurveError('curves must be an object')
    normalized = {}
    for name in ('power', 'nozzle'):
        points = value.get(name)
        if not isinstance(points, list):
            raise FanCurveError('%s must be an array' % name)
        if len(points) < MIN_POINTS or len(points) > MAX_POINTS:
            raise FanCurveError(
                '%s must contain %d-%d nodes' %
                (name, MIN_POINTS, MAX_POINTS))
        output = []
        previous_temperature = None
        for index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise FanCurveError('%s node %d must be [temperature, duty]' %
                                    (name, index + 1))
            temperature = _finite_number(point[0], '%s temperature' % name)
            duty = _finite_number(point[1], '%s duty' % name)
            if temperature != int(temperature) or not 0 <= temperature <= MAX_TEMPERATURE:
                raise FanCurveError('%s temperature must be an integer from 0 to %d' %
                                    (name, MAX_TEMPERATURE))
            if not 0 <= duty <= MAX_DUTY:
                raise FanCurveError('%s duty must be between 0 and %.2f' %
                                    (name, MAX_DUTY))
            temperature = int(temperature)
            if previous_temperature is not None and temperature <= previous_temperature:
                raise FanCurveError('%s temperatures must increase strictly' % name)
            output.append([temperature, round(duty, 3)])
            previous_temperature = temperature
        normalized[name] = output
    return normalized


def _normalize_heavy_load(value):
    if not isinstance(value, dict):
        value = {}
    temperature = _finite_number(value.get('temperature', 200), 'heavy-load temperature')
    heater_count = _finite_number(value.get('heater_count', 2), 'heavy-load heater count')
    duty = _finite_number(value.get('duty', 1), 'heavy-load duty')
    if temperature < 0 or temperature > MAX_TEMPERATURE:
        raise FanCurveError('heavy-load temperature must be from 0 to %d' % MAX_TEMPERATURE)
    if heater_count != int(heater_count) or heater_count < 1:
        raise FanCurveError('heavy-load heater count must be a positive integer')
    if duty < 0 or duty > 1:
        raise FanCurveError('heavy-load duty must be between 0 and 1')
    return {
        'temperature': temperature,
        'heater_count': int(heater_count),
        'duty': duty,
    }


def _canonical_profile(profile):
    profile = str(profile or '').strip().lower().replace('-', '_')
    return ALIASES.get(profile, profile)


def _normalize_document(value, profile=None):
    if not isinstance(value, dict):
        raise FanCurveError('fan curve state must be an object')
    if value.get('version') != 1:
        raise FanCurveError('unsupported fan curve version')
    profile = _canonical_profile(profile or value.get('profile'))
    if profile not in PROFILES:
        raise FanCurveError('unknown fan curve profile: %s' % profile)
    if profile == 'stock':
        return {'version': 1, 'profile': 'stock'}
    curves = normalize_curves(value.get('curves'))
    hysteresis = _finite_number(
        value.get('hysteresis', DEFAULT_HYSTERESIS), 'hysteresis')
    if not 0 <= hysteresis <= MAX_TEMPERATURE:
        raise FanCurveError('hysteresis must be from 0 to %d' % MAX_TEMPERATURE)
    return {
        'version': 1,
        'profile': profile,
        'curves': curves,
        'hysteresis': hysteresis,
        'heavy_load': _normalize_heavy_load(value.get('heavy_load')),
    }


def _read_json(path):
    with open(path, 'r', encoding='utf-8') as stream:
        return json.load(stream)


def _atomic_bytes(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.fan-curves-', dir=directory)
    try:
        os.chmod(temporary, 0o644)
        with os.fdopen(fd, 'wb') as stream:
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


def _preset_path(profile):
    return os.path.join(PRESET_DIR, PRESETS[profile])


def _load_preset(profile):
    try:
        return _normalize_document(_read_json(_preset_path(profile)), profile)
    except OSError as error:
        raise FanCurveError('cannot load %s preset: %s' % (profile, error))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid %s preset: %s' % (profile, error))


def load_custom():
    try:
        return normalize_curves(_read_json(CUSTOM_PATH))
    except FileNotFoundError:
        return _load_preset('quiet')['curves']
    except OSError as error:
        raise FanCurveError('cannot load Custom curves: %s' % error)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid Custom curves: %s' % error)


def save_custom(curves):
    normalized = normalize_curves(curves)
    _atomic_json(CUSTOM_PATH, normalized)
    return normalized


def _load_active():
    try:
        return _normalize_document(_read_json(ACTIVE_PATH))
    except FileNotFoundError:
        return {'version': 1, 'profile': 'stock'}
    except OSError as error:
        raise FanCurveError('cannot load active fan curves: %s' % error)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid active fan curves: %s' % error)


def current_profile():
    try:
        return _load_active()['profile']
    except FanCurveError:
        return 'stock'


def get_state():
    quiet = _load_preset('quiet')
    return {
        'version': 1,
        'current': current_profile(),
        'presets': {
            'stock': {'curves': {'power': [], 'nozzle': []}},
            'quiet': {'curves': quiet['curves']},
            'very_quiet': {'curves': _load_preset('very_quiet')['curves']},
        },
        'custom': load_custom(),
        'limits': {
            'max_points': MAX_POINTS,
            'temperature': [0, MAX_TEMPERATURE],
            'duty': [0, MAX_DUTY],
        },
        'hysteresis': quiet['hysteresis'],
        'heavy_load': quiet['heavy_load'],
    }


def _active_document(profile, curves=None):
    profile = _canonical_profile(profile)
    if profile == 'stock':
        return {'version': 1, 'profile': 'stock'}
    if profile in ('quiet', 'very_quiet'):
        return _load_preset(profile)
    if profile == 'custom':
        base = _load_preset('quiet')
        return _normalize_document({
            'version': 1,
            'profile': 'custom',
            'curves': curves if curves is not None else load_custom(),
            'hysteresis': base['hysteresis'],
            'heavy_load': base['heavy_load'],
        })
    raise FanCurveError('unknown fan curve profile: %s' % profile)


def _restart_klipper():
    return subprocess.run(
        ['/etc/init.d/S60klipper', 'restart'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=120).returncode


def _restore(path, previous):
    if previous is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    else:
        _atomic_bytes(path, previous)


def apply_profile(profile, curves=None, restart=True):
    profile = _canonical_profile(profile)
    if profile == 'custom' and curves is not None:
        curves = save_custom(curves)
    document = _active_document(profile, curves)
    try:
        with open(ACTIVE_PATH, 'rb') as stream:
            previous = stream.read()
    except FileNotFoundError:
        previous = None
    _atomic_json(ACTIVE_PATH, document)
    if restart:
        try:
            result = _restart_klipper()
        except Exception:
            _restore(ACTIVE_PATH, previous)
            raise
        if result != 0:
            _restore(ACTIVE_PATH, previous)
            raise FanCurveError('Klipper restart failed')
    return profile


def migrate_legacy(profile):
    if os.path.exists(ACTIVE_PATH):
        return current_profile()
    document = _active_document(profile)
    _atomic_json(ACTIVE_PATH, document)
    return document['profile']


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('apply', 'migrate', 'profile'))
    parser.add_argument('profile', nargs='?')
    args = parser.parse_args()
    if args.command == 'profile':
        print(current_profile())
        return
    if args.command == 'migrate':
        migrate_legacy(args.profile)
        return
    apply_profile(args.profile)


if __name__ == '__main__':
    main()
