import functools
import json
import math
import os
import subprocess
import tempfile
import threading


CONFIG_DIR = os.environ.get(
    'FAN_CURVES_CONFIG_DIR', '/oem/printer_data/config/extended')
ACTIVE_PATH = os.path.join(CONFIG_DIR, 'fan-curves-active.json')
# The curve the old editor kept for the user. It is imported as a profile.
LEGACY_PATH = os.path.join(CONFIG_DIR, 'fan-curves.json')
# Every profile but the stock one is a file in this directory. It is the
# only place profiles are looked for, so a file the user adds, renames or
# removes is the one the editor and the driver see.
PROFILE_DIR = os.path.join(CONFIG_DIR, 'fan-curves-profiles')
# The directory the firmware ships its profile in. It is the source of that
# profile, not a second place to look for profiles.
SHIPPED_DIR = os.environ.get(
    'FAN_CURVES_SHIPPED_DIR', '/usr/local/share/firmware-config/tweaks/klipper')
PROFILE_PREFIX = 'fan_curves_'
PROFILE_SUFFIX = '.json'
STOCK_PROFILE = 'stock'
# The profile the firmware ships. It is a configuration like any other: it
# can be drawn, renamed and removed. It is the placeholder for a machine
# that has no configuration of its own.
CUSTOM_PROFILE = 'custom'
MAX_POINTS = 16
MIN_POINTS = 2
MAX_TEMPERATURE = 300
# The stock nozzle fan reaches 90%, so an ordinary curve has to be allowed to
# go that high. The heavy-load rule reaches 100% as well.
MAX_DUTY = 1.0
DEFAULT_HYSTERESIS = 5.0
# The heavy load rule of the stock power fan: 100% when at least three
# toolheads heat up and one of them targets more than 260C.
DEFAULT_HEAVY_LOAD = {'temperature': 260, 'heater_count': 3,
                      'hot_count': 1, 'duty': 1.0}
# The warning that goes with every profile but the stock one.
RISK_NOTICE = ('Stock keeps a lot of cooling in reserve, but a profile of your own '
               'is your own risk. Different materials need different cooling, so test '
               'it before a long print. Happy printing!')
# The stock nozzle fan and power fan, taken from the Snapmaker U1
# printer.cfg. The curve editor draws them next to a profile so the effect of
# a curve can be compared with the original behaviour.
STOCK_NOZZLE = [
    [45, 0.3], [60, 0.35], [70, 0.4], [100, 0.45], [140, 0.5], [160, 0.6],
    [180, 0.65], [200, 0.7], [220, 0.75], [230, 0.8], [240, 0.85], [260, 0.9],
]
STOCK_POWER = [[45, 0.6], [260, 0.6]]

# The profile files, the active file and the restart of Klipper belong to one
# change. The init script of Klipper refuses a restart that runs next to
# another one, so two changes that arrive at the same time would leave the
# running profile and the file behind it out of step: the second restart
# fails, and the rollback of the failed restart puts the running profile back.
# One change at a time keeps them in step.
STATE_LOCK = threading.RLock()


def one_change_at_a_time(function):
    """Hold the fan curves while the function reads or changes them."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        with STATE_LOCK:
            return function(*args, **kwargs)
    return wrapper


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
    # The power fan reaches 100% when at least heater_count toolheads heat
    # up and at least hot_count of them target more than temperature.
    # A heater_count of 0 drops the first condition.
    temperature = _finite_number(
        value.get('temperature', DEFAULT_HEAVY_LOAD['temperature']),
        'heavy-load temperature')
    heater_count = _finite_number(
        value.get('heater_count', DEFAULT_HEAVY_LOAD['heater_count']),
        'heavy-load heater count')
    hot_count = _finite_number(
        value.get('hot_count', DEFAULT_HEAVY_LOAD['hot_count']),
        'heavy-load hot count')
    duty = _finite_number(value.get('duty', DEFAULT_HEAVY_LOAD['duty']),
                           'heavy-load duty')
    if temperature < 0 or temperature > MAX_TEMPERATURE:
        raise FanCurveError('heavy-load temperature must be from 0 to %d' % MAX_TEMPERATURE)
    if heater_count != int(heater_count) or heater_count < 0:
        raise FanCurveError('heavy-load heater count must be zero or a positive integer')
    if hot_count != int(hot_count) or hot_count < 1:
        raise FanCurveError('heavy-load hot count must be a positive integer')
    if duty < 0 or duty > 1:
        raise FanCurveError('heavy-load duty must be between 0 and 1')
    return {
        'temperature': temperature,
        'heater_count': int(heater_count),
        'hot_count': int(hot_count),
        'duty': duty,
    }


def _canonical_profile(profile):
    return str(profile or '').strip().lower().replace('-', '_')


def _normalize_document(value, profile=None, allow_new=False):
    if not isinstance(value, dict):
        raise FanCurveError('fan curve state must be an object')
    if value.get('version') != 1:
        raise FanCurveError('unsupported fan curve version')
    profile = _canonical_profile(profile or value.get('profile'))
    if profile == STOCK_PROFILE:
        return {'version': 1, 'profile': STOCK_PROFILE}
    if not allow_new and profile not in _profiles():
        raise FanCurveError('unknown fan curve profile: %s' % profile)
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


def _files_in(directory):
    """Return the profile files in a directory as {profile name: file path}."""
    found = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        if not name.startswith(PROFILE_PREFIX) or not name.endswith(PROFILE_SUFFIX):
            continue
        profile = name[len(PROFILE_PREFIX):-len(PROFILE_SUFFIX)]
        if not profile or profile == STOCK_PROFILE:
            continue
        found[profile] = os.path.join(directory, name)
    return found


def _profiles():
    """Return the profiles that exist as a file."""
    return _files_in(PROFILE_DIR)


def _profile_path(profile):
    return os.path.join(PROFILE_DIR,
                        '%s%s%s' % (PROFILE_PREFIX, profile, PROFILE_SUFFIX))


def _profile_name(value):
    """The name of a profile file: no path, no odd characters."""
    name = _canonical_profile(value)
    if not name or name == STOCK_PROFILE:
        raise FanCurveError('invalid profile name')
    if not all(character.isalnum() or character == '_' for character in name):
        raise FanCurveError(
            'a profile name may only hold letters, digits and _')
    return name


def profile_label(name):
    """The name of a profile as it is shown to the user."""
    known = {STOCK_PROFILE: 'Stock', CUSTOM_PROFILE: 'Custom'}
    if name in known:
        return known[name]
    return ' '.join(word.capitalize() for word in name.split('_') if word)


def available_profiles():
    """The profiles that exist as a file."""
    return sorted(_profiles())


def _load_profile(profile):
    """The document of a profile file."""
    files = _profiles()
    if profile not in files:
        raise FanCurveError('unknown fan curve profile: %s' % profile)
    try:
        return _normalize_document(_read_json(files[profile]), profile)
    except OSError as error:
        raise FanCurveError('cannot load %s profile: %s' % (profile, error))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid %s profile: %s' % (profile, error))


def _profile_settings(profile):
    """The settings of a profile that a change of the curve does not touch.

    The hysteresis and the heavy load rule live in the file, so writing a
    new curve keeps them.
    """
    try:
        document = _load_profile(profile)
    except FanCurveError:
        return {}
    return {
        'hysteresis': document['hysteresis'],
        'heavy_load': document['heavy_load'],
    }


def _profile_document(name, curves, settings=None):
    """The document of a profile file."""
    document = {
        'version': 1,
        'profile': name,
        'curves': curves,
        'hysteresis': DEFAULT_HYSTERESIS,
        'heavy_load': DEFAULT_HEAVY_LOAD,
    }
    if settings:
        document.update(settings)
    return _normalize_document(document, name, allow_new=True)


def _starter_curves():
    """A new profile starts with a short line close to the stock curve."""
    return {
        'nozzle': [list(STOCK_NOZZLE[0]), list(STOCK_NOZZLE[-1])],
        'power': [list(STOCK_POWER[0]), list(STOCK_POWER[-1])],
    }


@one_change_at_a_time
def create_profile(name):
    """Create a profile that starts close to the stock curve."""
    name = _profile_name(name)
    if name in _profiles():
        raise FanCurveError('a profile called %s exists already' % name)
    _atomic_json(_profile_path(name), _profile_document(name, _starter_curves()))
    return name


@one_change_at_a_time
def save_profile(name, curves):
    """Write the curves into the file of a profile."""
    name = _profile_name(name)
    if name not in _profiles():
        raise FanCurveError('unknown fan curve profile: %s' % name)
    _atomic_json(_profile_path(name),
                 _profile_document(name, curves, _profile_settings(name)))
    return name


@one_change_at_a_time
def rename_profile(name, new_name, curves=None):
    """Give a profile another name."""
    name = _profile_name(name)
    new_name = _profile_name(new_name)
    if new_name == name:
        return new_name
    if new_name in _profiles():
        raise FanCurveError('a profile called %s exists already' % new_name)
    if name not in _profiles():
        raise FanCurveError('unknown fan curve profile: %s' % name)
    content = curves if curves is not None else _load_profile(name)['curves']
    settings = _profile_settings(name)
    _atomic_json(_profile_path(new_name),
                 _profile_document(new_name, content, settings))
    os.unlink(_profile_path(name))
    _rename_active_profile(name, new_name)
    return new_name


@one_change_at_a_time
def delete_profile(name, restart=True):
    """Remove the file of a profile.

    A profile that is running is removed as well: the printer goes back to
    the stock curves, so the running curve is never one whose file is gone.
    """
    name = _profile_name(name)
    if name not in _profiles():
        raise FanCurveError('unknown fan curve profile: %s' % name)
    if current_profile() == name:
        # The stock curves are applied first, so the running curve always
        # has a file behind it.
        apply_profile(STOCK_PROFILE, restart=restart)
    os.unlink(_profile_path(name))
    return name


def _shipped_document():
    """The document of the profile the firmware ships."""
    source = os.path.join(
        SHIPPED_DIR,
        '%s%s%s' % (PROFILE_PREFIX, CUSTOM_PROFILE, PROFILE_SUFFIX))
    try:
        return _read_json(source)
    except OSError as error:
        raise FanCurveError('cannot read the shipped profile: %s' % error)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid shipped profile: %s' % error)


@one_change_at_a_time
def restore_shipped():
    """Write the profile the firmware ships into the profile directory."""
    document = _shipped_document()
    _atomic_json(_profile_path(CUSTOM_PROFILE),
                 _normalize_document(document, CUSTOM_PROFILE, allow_new=True))
    return CUSTOM_PROFILE


def seed_placeholder():
    """Put the profile the firmware ships in when there is no profile.

    The profile directory is the only place profiles live, so a machine
    that has no profile of its own gets the one the firmware ships. The
    directory being empty is the condition, so nothing has to remember
    whether the profile was installed before, and a profile the user
    keeps is never touched.
    """
    if _profiles():
        return None
    return restore_shipped()


def _rename_active_profile(name, new_name):
    """Keep the name of the running profile in step with the file."""
    if current_profile() != name:
        return
    # The file of the old name is gone already, so the active file is read
    # as it is on disk instead of through the checks a profile needs.
    document = _read_active()
    if not document:
        return
    document['profile'] = new_name
    _atomic_json(ACTIVE_PATH, document)


def _read_active():
    """The active file as it is on disk."""
    try:
        document = _read_json(ACTIVE_PATH)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _load_active():
    try:
        return _normalize_document(_read_json(ACTIVE_PATH))
    except FileNotFoundError:
        return {'version': 1, 'profile': STOCK_PROFILE}
    except OSError as error:
        raise FanCurveError('cannot load active fan curves: %s' % error)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FanCurveError('invalid active fan curves: %s' % error)


def current_profile():
    """The profile that is running.

    The name is read without the checks a file needs, so a profile whose
    file was removed by hand is still reported as the running one.
    """
    try:
        document = _read_json(ACTIVE_PATH)
    except (OSError, TypeError, ValueError):
        return STOCK_PROFILE
    if not isinstance(document, dict):
        return STOCK_PROFILE
    return _canonical_profile(document.get('profile')) or STOCK_PROFILE


@one_change_at_a_time
def get_state():
    """What the editor needs to show the profiles."""
    profiles = _profiles()
    files = {}
    for profile in sorted(profiles):
        try:
            document = _load_profile(profile)
        except FanCurveError as error:
            # A file that cannot be read stays in the list, so the user can
            # see that it is there and what is wrong with it.
            files[profile] = {'error': str(error)}
            continue
        files[profile] = {
            'curves': document['curves'],
            'hysteresis': document['hysteresis'],
            'heavy_load': document['heavy_load'],
        }
    names = [STOCK_PROFILE] + sorted(profiles)
    return {
        'version': 1,
        'current': current_profile(),
        'profiles': names,
        'files': files,
        'labels': {profile: profile_label(profile) for profile in names},
        'notice': RISK_NOTICE,
        'stock': {'nozzle': STOCK_NOZZLE, 'power': STOCK_POWER},
        'limits': {
            'max_points': MAX_POINTS,
            'temperature': [0, MAX_TEMPERATURE],
            'duty': [0, MAX_DUTY],
        },
    }


def _active_document(profile, curves=None):
    profile = _canonical_profile(profile)
    if profile == STOCK_PROFILE:
        return {'version': 1, 'profile': STOCK_PROFILE}
    if curves is not None:
        return _profile_document(profile, curves, _profile_settings(profile))
    return _load_profile(profile)


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


@one_change_at_a_time
def apply_profile(profile, curves=None, restart=True):
    profile = _canonical_profile(profile)
    if profile == STOCK_PROFILE:
        document = {'version': 1, 'profile': STOCK_PROFILE}
    else:
        if curves is not None:
            # The curves of the profile are written to its file first, so
            # the running curve is the one on the screen.
            save_profile(profile, curves)
        document = _active_document(profile)
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
    """Bring an older install to the current layout."""
    _migrate_legacy_curve()
    if os.path.exists(ACTIVE_PATH):
        return current_profile()
    profile = _canonical_profile(profile)
    if profile != STOCK_PROFILE and profile not in _profiles():
        # The profile the old install asked for is not available any more, so
        # fall back to the profile the firmware ships instead of losing the
        # setting.
        profile = CUSTOM_PROFILE if CUSTOM_PROFILE in _profiles() \
            else STOCK_PROFILE
    document = _active_document(profile)
    _atomic_json(ACTIVE_PATH, document)
    return document['profile']


def _migrate_legacy_curve():
    """The curve of the older editor becomes a profile file."""
    if not os.path.exists(LEGACY_PATH):
        return
    target = _profile_path(CUSTOM_PROFILE)
    if os.path.exists(target):
        return
    try:
        curves = normalize_curves(_read_json(LEGACY_PATH))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    _atomic_json(target, _profile_document(CUSTOM_PROFILE, curves))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=(
        'apply', 'migrate', 'profile', 'seed', 'restore'))
    parser.add_argument('profile', nargs='?')
    args = parser.parse_args()
    if args.command == 'profile':
        print(current_profile())
        return
    if args.command == 'seed':
        seed_placeholder()
        return
    if args.command == 'restore':
        print(restore_shipped())
        return
    if args.command == 'migrate':
        migrate_legacy(args.profile)
        return
    if not args.profile:
        parser.error('apply needs the name of a profile')
    apply_profile(args.profile)


if __name__ == '__main__':
    main()
