# Adapted from decay71/multiACE (GPLv3), pinned by this mod to commit
# c9c22e391cee89bc7d7894ce4a25876a59565cbc. The firmware overlay adds no
# runtime updater/web service; Firmware Config controls module activation.
import logging
import logging.handlers
import json
import math
import queue
import threading
import traceback
import os
import time
import hashlib
import serial
from serial import SerialException

from .ace_protocol_v1 import AceProtocolV1
from .ace_protocol_v2 import AceProtocolV2

KNOWN_PROTOCOLS = (AceProtocolV1, AceProtocolV2)

MULTIACE_VERSION = "0.99.6.2b"
MULTIACE_CODENAME = "Persistent Pesterers"

ACE_API_VERSION = 1

# Local bundle identifiers for the pinned source revision used by this mod.
MULTIACE_BUILD_TAG = "c9c22e39"
MULTIACE_BUNDLE_SHA1 = "4cafb85"

def _load_i18n_catalog(i18n_dir, lang):
    """Read <i18n_dir>/<lang>.json overlaid on en.json. Returns a dict
    (possibly empty if the i18n dir is missing) - caller falls back to
    the literal key when a string is not found."""
    out = {}
    try:
        en_path = os.path.join(i18n_dir, 'en.json')
        if os.path.isfile(en_path):
            with open(en_path, 'r', encoding='utf-8') as f:
                out = json.load(f)
    except Exception:
        out = {}
    if lang and lang != 'en':
        try:
            lp = os.path.join(i18n_dir, lang + '.json')
            if os.path.isfile(lp):
                with open(lp, 'r', encoding='utf-8') as f:
                    overlay = json.load(f)

                def _merge(base, ov):
                    for k, v in ov.items():
                        if isinstance(v, dict) and isinstance(base.get(k), dict):
                            _merge(base[k], v)
                        else:
                            base[k] = v
                _merge(out, overlay)
        except Exception:
            pass
    return out

def _setup_file_logger(name, filepath, max_bytes=1048576, backup_count=3):

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            filepath, maxBytes=max_bytes, backupCount=backup_count)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s.%(msecs)03d %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(handler)
    return logger

class AceException(Exception):
    pass

GATE_UNKNOWN = -1
GATE_EMPTY = 0
GATE_AVAILABLE = 1

V2_FEED_LOG = False
V2_FEED_LOG_INTERVAL = 2.0

REACTOR_WATCHDOG_INTERVAL = 0.1
REACTOR_STALL_THRESHOLD = 0.030
AIRLOG_SAMPLE_S = 2.0
AIRLOG_EMIT_S = 10.0
STALL_SRC_THRESHOLD = 0.020

V2_FA_RUNNING_STATES = (
    'assisting', 'rollback_assisting', 'feeding', 'rollback', 'preloading')

V2_ACTIVE_MOTION_STATES = ('feeding', 'rollback', 'rollback_assisting', 'preloading')
WAIT_ACE_FEEDING_MAX = 4

FA_HOMING_SETTLE = 0.5

ACE_OPEN_TIMEOUT = 8.0

FA_ASSIST_VERIFY_MARGIN = 1.5

FA_EXTRUDE_IDLE_GRACE = 2.0

PICK_CHECK_FLOW_PUSH = 10.
PICK_CHECK_PUSH_FEEDRATE = 400
PICK_CHECK_COIL_SAMPLES = 5
PICK_CHECK_COIL_INTERVAL = 0.5
PICK_CHECK_COIL_THRESHOLD = 1000
PICK_CHECK_MIN_PUSH = 20.
PICK_GATE_REGRIP = 40.
PICK_GATE_REGRIP_FEEDRATE = 300
PICK_GATE_ACE_PUSH_V2 = 40.
PICK_GATE_ACE_PUSH_V1 = 30.
PICK_GATE_ACE_PUSH_SPEED = 20
PICK_GATE_ACE_PUSH_RETRIES = 3
PICK_GATE_ACE_PUSH_RETRY_DELAY = 1.0
PICK_TURBULENCE_UPSWING = 3000.
PICK_TURBULENCE_SETTLE = 2.0
BG_PICK_WIPE = True
RESUME_NOOP_WIPE_WINDOW = 180.

FA_REARM_MAX_FAILS = 5
FA_STICK_CONFIRM_TIME = 8.0

HEAL_MAX_FAILS = 3
FORCE_OFFICIAL_MAX = 3

V1_FA_CONFIRM_TICKS = 2
V1_FA_REARM_MIN_INTERVAL = 10.0

class MultiAce:
    VARS_ACE_REVISION = 'ace__revision'
    VARS_ACE_ACTIVE_DEVICE = 'ace__active_device'
    VARS_ACE_HEAD_SOURCE = 'ace__head_source'
    VARS_ACE_HEAD_MANUAL = 'ace__head_manual'
    VARS_ACE_HEAD = 'ace__ace_head'
    VARS_ACE_HEAD_FEEDER = 'ace__head_feeder'
    VARS_ACE_HEAD_ACE = 'ace__head_ace'

    def __init__(self, config):
        self._connected = False
        self._serial = None
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self._name = config.get_name()
        self.send_time = None
        self.ace_dev_fd = None
        self.heartbeat_timer = None

        self.gate_status = [GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN]
        if self._name.startswith('ace '):
            self._name = self._name[4:]

        self.save_variables = self.printer.lookup_object('save_variables', None)
        if self.save_variables:
            revision_var = self.save_variables.allVariables.get(self.VARS_ACE_REVISION, None)
            if revision_var is None:
                config.error("You have custom [save_variables]. "
                             "Copy the contents of ace_vars.cfg to your file and remove [save_variables] in ace.cfg")
        else:
            config.error("There is no [save_variables] in the config. Check installation guide")

        self.serial_id = config.get('serial', '')
        self._protocols = {}
        self._ace_path_protocol = {}
        self._ace_models = {}
        self.baud = config.getint('baud', 0, minval=0)
        self._ace_devices = []
        self._active_device_index = 0

        self._ace_canonical = None
        self._ace_startup_failed = False
        self._ace_present = set()

        self.ace_device_count = config.getint('ace_device_count', 1, minval=1, maxval=8)

        cfg_print_mode = config.get('print_mode', None)
        if cfg_print_mode is not None:
            logging.info(
                '[multiACE] print_mode=%s ignored (obsolete in v0.82+)'
                % cfg_print_mode)

        self.feed_speed = config.getint('feed_speed', 50)
        self.retract_speed = config.getint('retract_speed', 50)
        self.retract_length = config.getint('retract_length', 100)

        self.feed_length = config.getint('feed_length', 0)

        self.load_length = config.getint('load_length', 2000)
        self.load_retry = config.getint('load_retry', 3)
        self.load_retry_retract = config.getint('load_retry_retract', 50)
        self.max_dryer_temperature = config.getint('max_dryer_temperature', 55)
        self.extra_purge_length = config.getfloat('extra_purge_length', 0, minval=0, maxval=200)
        self.swap_purge_length = config.getint('swap_purge_length', 0, minval=0, maxval=200)

        self.seat_overshoot_length = config.getint('seat_overshoot_length', 0, minval=0, maxval=100)
        self.swap_default_temp = config.getint('swap_default_temp', 250, minval=180, maxval=300)

        self.swap_retract_length = config.getint('swap_retract_length', 0, minval=0, maxval=2000)

        self.swap_anti_ooze_retract = config.getint('swap_anti_ooze_retract', 10, minval=0, maxval=50)

        self.swap_post_retract_wipe = config.getboolean(
            'swap_post_retract_wipe', False)

        self.extrusion_retry = config.getint('extrusion_retry', 7, minval=0, maxval=10)
        self.extrusion_retry_retract = config.getint('extrusion_retry_retract', 30, minval=5, maxval=200)

        self.extrusion_retry_retract_a = config.getint('extrusion_retry_retract_a', 50, minval=5, maxval=200)

        self.wiggle_scheme = (config.get('wiggle_scheme', 'EAEAEAE') or 'EAEAEAE').upper()
        for c in self.wiggle_scheme:
            if c not in ('E', 'A'):
                raise config.error(
                    "wiggle_scheme: invalid char %r (only 'E' and 'A' allowed)" % c)

        config.getint('extrusion_stock_retry', 5, minval=1, maxval=50)
        self.unload_retry = config.getint('unload_retry', 3, minval=1, maxval=10)
        self.unload_gpio = config.getboolean('unload_gpio', True)

        self.swap_cool_probe = config.getboolean('swap_cool_probe', True)
        self.swap_probe_temp = config.getint('swap_probe_temp', 175, minval=170, maxval=250)
        self.swap_probe_push = config.getint('swap_probe_push', 5, minval=1, maxval=20)
        self.dryer_temp = config.getint('dryer_temp', 55, minval=30, maxval=70)
        self.dryer_duration = config.getint('dryer_duration', 240, minval=10, maxval=480)

        self.head_feed_length = {}
        self.head_load_length = {}
        self.head_load_retry = {}
        self.head_load_retry_retract = {}
        for i in range(4):
            self.head_feed_length[i] = config.getint('feed_length_%d' % i, self.feed_length)
            self.head_load_length[i] = config.getint('load_length_%d' % i, self.load_length)
            self.head_load_retry[i] = config.getint('load_retry_%d' % i, self.load_retry)
            self.head_load_retry_retract[i] = config.getint('load_retry_retract_%d' % i, self.load_retry_retract)

        self.head_manual = {}
        for i in range(4):
            self.head_manual[i] = config.getboolean('head_manual_%d' % i, False)

        self.HEAD_MODE_ACE = 0
        self._extruder_handler_registered = False
        cfg_ace_head = config.getint('ace_head', 3, minval=0, maxval=3)
        self._ace_head = cfg_ace_head
        if self.save_variables:
            sv = self.save_variables.allVariables.get(self.VARS_ACE_HEAD, None)
            if sv is not None:
                try:
                    sv_i = int(sv)
                    if 0 <= sv_i <= 3:
                        self._ace_head = sv_i
                except (TypeError, ValueError):
                    pass

        self.head_feeder = {}
        for i in range(4):
            self.head_feeder[i] = config.getboolean('head_feeder_%d' % i, False)

        self.head_ace = {}
        for i in range(4):
            self.head_ace[i] = config.getint(
                'head_ace_%d' % i, i, minval=0, maxval=3)

        self._ace_section_load_length = {}
        self._ace_section_load_length_slot = {}
        self._ace_section_retract_length = {}
        self._ace_section_retract_length_slot = {}
        self._ace_section_swap_retract_length = {}
        self._ace_section_swap_retract_length_slot = {}
        self._ace_section_feed_speed = {}
        self._ace_section_retract_speed = {}
        for ace_sec in config.get_prefix_sections('ace '):
            sec_name = ace_sec.get_name()
            try:
                ace_i = int(sec_name.split()[1])
            except (IndexError, ValueError):
                continue
            ll = ace_sec.getint('load_length', None, minval=1)
            if ll is not None:
                self._ace_section_load_length[ace_i] = ll
            rl = ace_sec.getint('retract_length', None, minval=1)
            if rl is not None:
                self._ace_section_retract_length[ace_i] = rl
            srl = ace_sec.getint('swap_retract_length', None, minval=0, maxval=2000)
            if srl is not None:
                self._ace_section_swap_retract_length[ace_i] = srl
            fs = ace_sec.getint('feed_speed', None, minval=1)
            if fs is not None:
                self._ace_section_feed_speed[ace_i] = fs
            rs = ace_sec.getint('retract_speed', None, minval=1)
            if rs is not None:
                self._ace_section_retract_speed[ace_i] = rs
            for slot_i in range(4):
                ll_s = ace_sec.getint('load_length_%d' % slot_i, None, minval=1)
                if ll_s is not None:
                    self._ace_section_load_length_slot[(ace_i, slot_i)] = ll_s
                rl_s = ace_sec.getint('retract_length_%d' % slot_i, None, minval=1)
                if rl_s is not None:
                    self._ace_section_retract_length_slot[(ace_i, slot_i)] = rl_s
                srl_s = ace_sec.getint('swap_retract_length_%d' % slot_i, None,
                                       minval=0, maxval=2000)
                if srl_s is not None:
                    self._ace_section_swap_retract_length_slot[(ace_i, slot_i)] = srl_s

        self.ace_dryer_temp = {}
        self.ace_dryer_duration = {}
        for i in range(4):
            self.ace_dryer_temp[i] = config.getint('dryer_temp_%d' % i, self.dryer_temp)
            self.ace_dryer_duration[i] = config.getint('dryer_duration_%d' % i, self.dryer_duration)

        def _parse_idx_list(key):
            raw = config.get(key, '').strip()
            out = set()
            if raw:
                for token in raw.split(','):
                    token = token.strip()
                    if token.isdigit():
                        out.add(int(token))
            return out
        self._fa_print_disable = _parse_idx_list('fa_print_disable')
        self._fa_load_disable = _parse_idx_list('fa_load_disable')
        self.fa_debug = config.getboolean('fa_debug', False)
        self.v1_fa_monitor = config.getboolean('v1_fa_monitor', False)
        _cfg_pickup_clean = config.getboolean('pickup_cleaning', False)
        self._pickup_cleaning = _cfg_pickup_clean
        if self.save_variables:
            _sv = self.save_variables.allVariables.get(
                'ace__pickup_cleaning', None)
            if _sv is not None:
                self._pickup_cleaning = bool(_sv)

        self._homing_flag_path = config.get(
            'homing_flag_path', '/tmp/multiace_homing_active')

        self._enable_ace_v2 = config.getboolean('enable_ace_v2', False)

        self._v2_order = config.getchoice('v2_order',
                                          {'usb': 'usb', 'first': 'first',
                                           'last': 'last'},
                                          'usb')

        self._v2_extra_usb_ids = self._parse_v2_extra_usb_ids(
            config.get('v2_extra_usb_ids', ''))
        AceProtocolV2.EXTRA_USB_IDS = self._v2_extra_usb_ids
        if self._v2_extra_usb_ids:
            logging.info('[multiACE] V2 extra USB IDs (opt-in): %s' % (
                ', '.join('%s:%s' % p for p in self._v2_extra_usb_ids)))

        self._v2_print_assist_mode = config.getchoice(
            'v2_print_assist_mode',
            {'constant': 'constant', 'tracked': 'tracked'},
            'constant')
        self._v2_constant_assist_speed = config.getint(
            'v2_constant_assist_speed', 0, minval=0, maxval=50)
        self._v2_assist_confirm_time = config.getfloat(
            'v2_assist_confirm_time', 0.5, minval=0.0, maxval=5.0)

        self._update_repo = config.get('update_repo', 'decay71/multiACE').strip()
        self._update_prerelease = config.getboolean('update_prerelease', False)

        self._update_url_base = config.get('update_url_base', '').strip()

        self._feed_assist_index = -1
        self._request_id = 0

        self._serials = {}
        self._connected_per_ace = {}
        self._serial_failed_per_ace = {}
        self._reconnecting_per_ace = {}
        self._info_per_ace = {}

        self._slot_overrides = {}
        self._slot_overrides_file = (
            "/home/lava/printer_data/config/extended/multiace/slot_overrides.json")
        self._slot_overrides_mtime = 0.0

        self._orig_set_ptc = None
        self._raw_set_ptc = None
        self._expected_ptc_pushes = []

        self._in_internal_load_head = False
        self._feed_assist_per_ace = {}
        self._v1_fa_notassist_streak = {}
        self._v1_fa_last_rearm = {}
        self._callback_maps = {}
        self._request_ids = {}
        self._read_buffers = {}
        self._ace_dev_fds = {}
        self._heartbeat_timers = {}
        self._connect_timers_per_ace = {}

        self._writer_threads = {}
        self._reader_threads = {}
        self._writer_queues = {}
        self._thread_stop_flags = {}
        self._cb_locks = {}
        self._seq_lock = threading.Lock()
        self._gate_status_per_ace = {}

        self._v2_filament_info_per_ace = {}
        self._v2_filament_info_pending = {}
        self._v2_filament_info_empty = {}

        self._v2_velocity_timers = {}
        self._v2_velocity_state = {}
        self._v2_fa_rearm_pending = set()
        self._fa_rearm_fails = {}
        self._fa_rearm_suspended = set()
        self._fa_intent_ts = {}

        self._v2_feed_check_check_length = config.getint(
            'v2_feed_check_check_length', 200, minval=3, maxval=254)
        self._v2_feed_check_error_length = config.getint(
            'v2_feed_check_error_length', 185, minval=3, maxval=254)
        if self._v2_feed_check_error_length > self._v2_feed_check_check_length:
            raise config.error(
                'v2_feed_check_error_length (%d) must be ≤ '
                'v2_feed_check_check_length (%d)' % (
                    self._v2_feed_check_error_length,
                    self._v2_feed_check_check_length))

        self._enable_web = config.getboolean('enable_web', True)
        self._web_port = config.getint(
            'web_port', 7126, minval=1024, maxval=65535)
        self._web_dir = config.get(
            'web_dir', '/home/lava/multiace_web')

        self._identity_priority = config.get('identity_priority', 'multiace')
        if self._identity_priority not in ('multiace', 'spoollink'):
            self._identity_priority = 'multiace'
        config_lang = config.get('language', 'en')
        lang = None
        if self.save_variables:
            lang = self.save_variables.allVariables.get('ace__language', None)
        self._language = (lang or config_lang)
        self._display_index_base = config.getint(
            'display_index_base', 0, minval=0, maxval=1)

        self._i18n_primary = '/home/lava/printer_data/config/extended/multiace/i18n'
        self._i18n_fallback = os.path.join(self._web_dir, 'i18n')
        self._reload_i18n_catalog()

        self._head_source = {0: None, 1: None, 2: None, 3: None}
        self._heal_official_skip = {}
        self._heal_fail_count = {}
        self._ptc_push_block = {}
        self._force_official_count = {}

        self._swap_in_progress = False
        self._swap_saved_pos = None
        self._swap_orig_ext_name = None
        self._swap_switched_head = False
        self._swap_probe_ref_temp = 0

        self._swap_phase = 'idle'
        self._last_swap_result = None
        self._event_seq = 0

        self._v2_active_rev_assist = False
        self._test_cancel = False
        self._auto_feed_enabled = False
        self._fa_context = 'idle'

        self._homing_active = False
        self._last_homing_end = 0.0

        self._retract_length_override = None
        self._purge_length_override = None

        self._last_unload_ok = True
        self._last_load_ok = True

        self._runout_suppress_heads = set()
        self._print_has_gcode_loads = False

        self._ghost_heads = set()
        self._bg_left_empty = set()
        self._bg_staged = {}
        self._bg_load_unverified = set()
        self._resume_wipe_deadline = 0.
        self._bg_prime_deficit = {}
        self._hotplug_gone = {}

        self._serial_failed = False
        self._serial_failed_at = 0.0
        self._serial_failed_pause_sent = False
        self._fa_failed_pause_sent = False

        log_dir = config.get('log_dir', '/home/lava/printer_data/logs')
        self._usb_log = _setup_file_logger(
            'multiace_usb', os.path.join(log_dir, 'multiace_usb.log'))
        self._state_log = _setup_file_logger(
            'multiace_state', os.path.join(log_dir, 'multiace_state.log'))
        self._telemetry_log = _setup_file_logger(
            'multiace_telemetry', os.path.join(log_dir, 'multiace_telemetry.log'))
        self._wiggle_log = _setup_file_logger(
            'multiace_wiggle', os.path.join(log_dir, 'multiace_wiggle.log'))
        self._fa_log = _setup_file_logger(
            'multiace_fa', os.path.join(log_dir, 'multiace_fa.log'))
        self._feedlog = _setup_file_logger(
            'multiace_feedlog', os.path.join(log_dir, 'multiace_feedlog.log'))
        self._feedlog_last = {}
        self._feedlog_timer = None
        self._state_debug_enabled = config.getboolean('state_debug', False)
        self._usb_debug_enabled = config.getboolean('usb_debug', True)
        self.airlog_enable = config.getboolean('airlog', False)
        self.stall_watchdog = config.getboolean('stall_watchdog', False)

        self._apply_log_levels()
        self._last_switch_auto_ts = None
        self._fa_any_active_since = None
        self._fa_last_active_ts = time.monotonic()
        self._fa_gap_threshold_ms = config.getint(
            'fa_gap_threshold_ms', 3000, minval=100)

        self._fa_settle_after_stop = config.getfloat(
            'fa_settle_after_stop', 2.0, minval=0.0, maxval=10.0)
        self._fa_start_retries = config.getint(
            'fa_start_retries', 15, minval=0, maxval=30)
        self._fa_start_retry_delay = config.getfloat(
            'fa_start_retry_delay', 1.0, minval=0.05, maxval=5.0)

        self._usb_stats = {
            'scans': 0,
            'retries': 0,
            'connects': 0,
            'connect_failures': 0,
            'disconnects': 0,
            'errno5_total': 0,
            'errno5_recovered': 0,
            'errno5_unrecovered': 0,
            'cascades': 0,
            'start_time': time.monotonic(),
        }
        self._errno5_recent = []

        self._info = {
            'status': 'ready',
            'dryer_status': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [
                {
                    'index': 0,
                    'status': 'empty1',
                    'sku': '',
                    'type': '',
                    'rfid': 0,
                    'brand':'',
                    'color': [0, 0, 0]
                },
                {
                    'index': 1,
                    'status': 'empty1',
                    'sku': '',
                    'type': '',
                    'rfid': 0,
                    'brand': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 2,
                    'status': 'empty1',
                    'sku': '',
                    'type': '',
                    'rfid': 0,
                    'brand': '',
                    'color': [0, 0, 0]
                },
                {
                    'index': 3,
                    'status': 'empty1',
                    'sku': '',
                    'type': '',
                    'rfid': 0,
                    'brand': '',
                    'color': [0, 0, 0]
                }
            ]
        }
        self.extruder_sensor = None

        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect', self._handle_disconnect)

        self.printer.register_event_handler('print_stats:start', self._on_print_start)
        self.printer.register_event_handler('print_stats:stop', self._on_print_end)

        self.printer.register_event_handler(
            'homing:homing_move_begin', self._on_homing_move_begin)
        self.printer.register_event_handler(
            'homing:homing_move_end', self._on_homing_move_end)

        self.gcode.register_command(
            'ACE_START_DRYING', self.cmd_ACE_START_DRYING,
            desc=self.cmd_ACE_START_DRYING_help)
        self.gcode.register_command(
            'ACE_STOP_DRYING', self.cmd_ACE_STOP_DRYING,
            desc=self.cmd_ACE_STOP_DRYING_help)
        self.gcode.register_command(
            'ACE_ENABLE_FEED_ASSIST', self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc=self.cmd_ACE_ENABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_SET_HEAD_MANUAL', self.cmd_ACE_SET_HEAD_MANUAL,
            desc=self.cmd_ACE_SET_HEAD_MANUAL_help)
        self.gcode.register_command(
            'ACE_SET_HEAD_FEEDER', self.cmd_ACE_SET_HEAD_FEEDER,
            desc=self.cmd_ACE_SET_HEAD_FEEDER_help)
        self.gcode.register_command(
            'ACE_SET_HEAD_ACE', self.cmd_ACE_SET_HEAD_ACE,
            desc=self.cmd_ACE_SET_HEAD_ACE_help)
        self.gcode.register_command(
            'ACE_SET_PURGE', self.cmd_ACE_SET_PURGE,
            desc=self.cmd_ACE_SET_PURGE_help)
        self.gcode.register_command(
            'ACE_DISABLE_FEED_ASSIST', self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc=self.cmd_ACE_DISABLE_FEED_ASSIST_help)
        self.gcode.register_command(
            'ACE_FEED', self.cmd_ACE_FEED,
            desc=self.cmd_ACE_FEED_help)
        self.gcode.register_command(
            'ACE_RETRACT', self.cmd_ACE_RETRACT,
            desc=self.cmd_ACE_RETRACT_help)

        self.gcode.register_command(
            'ACE_SWITCH', self.cmd_ACE_SWITCH,
            desc=self.cmd_ACE_SWITCH_help)
        self.gcode.register_command(
            'ACE_LIST', self.cmd_ACE_LIST,
            desc=self.cmd_ACE_LIST_help)

        self.gcode.register_command(
            'ACE_RUN_MODE_SWITCH', self.cmd_ACE_RUN_MODE_SWITCH,
            desc=self.cmd_ACE_RUN_MODE_SWITCH_help)

        self.gcode.register_command(
            'ACE_UPDATE_CHECK', self.cmd_ACE_UPDATE_CHECK,
            desc='[multiACE] Check GitHub for a newer release (no install)')
        self.gcode.register_command(
            'ACE_UPDATE_APPLY', self.cmd_ACE_UPDATE_APPLY,
            desc='[multiACE] Download + install the latest release. '
                 'Optional FORCE=1 reinstalls even if already on latest.')

        self.gcode.register_command(
            'ACE_LOAD_HEAD', self.cmd_ACE_LOAD_HEAD,
            desc=self.cmd_ACE_LOAD_HEAD_help)
        self.gcode.register_command(
            'ACE_UNLOAD_HEAD', self.cmd_ACE_UNLOAD_HEAD,
            desc=self.cmd_ACE_UNLOAD_HEAD_help)
        self.gcode.register_command(
            'ACE_SWAP_HEAD', self.cmd_ACE_SWAP_HEAD,
            desc=self.cmd_ACE_SWAP_HEAD_help)
        self.gcode.register_command(
            'ACE_HEAD_STATUS', self.cmd_ACE_HEAD_STATUS,
            desc=self.cmd_ACE_HEAD_STATUS_help)
        self.gcode.register_command(
            'ACE_CLEAR_HEADS', self.cmd_ACE_CLEAR_HEADS,
            desc=self.cmd_ACE_CLEAR_HEADS_help)
        self.gcode.register_command(
            'ACE_UNLOAD_ALL_HEADS', self.cmd_ACE_UNLOAD_ALL_HEADS,
            desc=self.cmd_ACE_UNLOAD_ALL_HEADS_help)
        self.gcode.register_command(
            'ACE_TEST', self.cmd_ACE_TEST,
            desc=self.cmd_ACE_TEST_help)
        self.gcode.register_command(
            'ACE_DWELL_TEST', self.cmd_ACE_DWELL_TEST,
            desc=self.cmd_ACE_DWELL_TEST_help)
        self.gcode.register_command(
            'ACE_MULTI_SLOT_TEST', self.cmd_ACE_MULTI_SLOT_TEST,
            desc=self.cmd_ACE_MULTI_SLOT_TEST_help)
        self.gcode.register_command(
            'ACE_TEST_CANCEL', self.cmd_ACE_TEST_CANCEL,
            desc='[multiACE] Cancel a running ACE_TEST after current step')
        self.gcode.register_command(
            'ACE_DRY', self.cmd_ACE_DRY,
            desc=self.cmd_ACE_DRY_help)
        self.gcode.register_command(
            'ACE_USB_STATS', self.cmd_ACE_USB_STATS,
            desc=self.cmd_ACE_USB_STATS_help)
        self.gcode.register_command(
            'ACE_DEBUG', self.cmd_ACE_DEBUG,
            desc=self.cmd_ACE_DEBUG_help)
        self.gcode.register_command(
            'ACE_USB_DEBUG', self.cmd_ACE_USB_DEBUG,
            desc=self.cmd_ACE_USB_DEBUG_help)
        self.gcode.register_command(
            'ACE_SEQ', self.cmd_ACE_SEQ,
            desc=self.cmd_ACE_SEQ_help)
        self.gcode.register_command(
            'ACE_PRELOAD', self.cmd_ACE_PRELOAD,
            desc=self.cmd_ACE_PRELOAD_help)
        self.gcode.register_command(
            'MACE_LOG', self.cmd_MACE_LOG,
            desc=self.cmd_MACE_LOG_help)
        self.gcode.register_command(
            'ACE_FA_TEST', self.cmd_ACE_FA_TEST,
            desc=self.cmd_ACE_FA_TEST_help)
        self.gcode.register_command(
            'MULTIACE_REFRESH_OVERRIDES',
            self.cmd_MULTIACE_REFRESH_OVERRIDES,
            desc='[multiACE] Re-read slot_overrides.json and push to display')
        self.gcode.register_command(
            'MULTIACE_SET_LANGUAGE',
            self.cmd_MULTIACE_SET_LANGUAGE,
            desc='[multiACE] Set message language (LANG=<code>), live reload + persist')
        self.gcode.register_command(
            'ACE_SET_PICKUP_CLEANING',
            self.cmd_ACE_SET_PICKUP_CLEANING,
            desc='[multiACE] Toggle Pickup-Cleaning (ENABLE=0|1), live + persist')
        self.gcode.register_command(
            'ACE_PICKUP_CLEAN',
            self.cmd_ACE_PICKUP_CLEAN,
            desc=self.cmd_ACE_PICKUP_CLEAN_help)

        for _name in (
                'DISCOVER', 'INFO', 'STATUS', 'TEMP', 'FEEDINFO',
                'KEYSTATE', 'FILAMENT', 'FILAMENT_IDENTIFY', 'RFID_TEST',
                'RFID', 'FEED', 'ROLLBACK',
                'STOP', 'SPEED', 'DRY', 'DRYSTOP', 'DRYTEMP',
                'FAN', 'VALVE', 'FEEDCHECK', 'RAW'):
            self.gcode.register_command(
                'A_' + _name,
                getattr(self, 'cmd_A_' + _name),
                desc=getattr(self, 'cmd_A_' + _name + '_help', ''))

    def _refresh_ace_devices(self, context):

        scan = self._scan_ace_devices(context)
        self._ace_present = set(scan)
        if self._ace_canonical is not None:
            for path in scan:
                if path in self._ace_canonical:
                    continue
                new_idx = len(self._ace_canonical)
                self._ace_canonical.append(path)
                self._ace_devices = list(self._ace_canonical)
                self.log_always(
                    '[multiACE] Late ACE device %s -> index %d (missed the '
                    'startup scan) - connecting' % (path, self._disp(new_idx)))
                ok = False
                try:
                    ok = self._open_ace(new_idx)
                except Exception as e:
                    logging.info(
                        '[multiACE] late-join connect error for ACE %d (%s): '
                        '%s' % (new_idx, path, e))
                if not ok:
                    self._ace_canonical.pop()
                    logging.info(
                        '[multiACE] late-join connect failed for %s - will '
                        'retry on the next scan' % path)
            self._ace_devices = list(self._ace_canonical)
        else:
            self._ace_devices = scan
        return scan

    def _is_ace_present(self, ace_index):

        if ace_index < 0 or ace_index >= len(self._ace_devices):
            return False
        if self._ace_canonical is None:
            return True
        return self._ace_devices[ace_index] in self._ace_present

    def _ace_path_sort_key(self, path):

        try:
            base = os.path.basename(path)
            segs = base.split(':')
            port_str = segs[1] if len(segs) >= 2 else ''
            port_tuple = tuple(int(x) for x in port_str.split('.') if x != '')
        except (ValueError, IndexError):
            port_tuple = ()

        proto = self._ace_path_protocol.get(path)
        proto_name = getattr(proto, 'NAME', '') if proto else ''
        if self._v2_order == 'first':
            proto_bucket = 0 if proto_name == 'v2' else 1
        elif self._v2_order == 'last':
            proto_bucket = 1 if proto_name == 'v2' else 0
        else:
            proto_bucket = 0
        return (proto_bucket, len(port_tuple), port_tuple, path)

    def _parse_v2_extra_usb_ids(self, raw):
        pairs = []
        hexset = set('0123456789abcdef')
        for tok in (raw or '').replace(',', ' ').split():
            t = tok.strip().lower()
            if not t:
                continue
            if ':' in t:
                vid, pid = t.split(':', 1)
            else:
                vid, pid = '1a86', t
            vid = vid[2:] if vid.startswith('0x') else vid
            pid = pid[2:] if pid.startswith('0x') else pid
            if (len(vid) == 4 and len(pid) == 4
                    and set(vid) <= hexset and set(pid) <= hexset):
                if (vid, pid) not in pairs:
                    pairs.append((vid, pid))
            else:
                logging.info(
                    '[multiACE] ignoring invalid v2_extra_usb_ids token: %r'
                    % tok)
        return tuple(pairs)

    def _scan_ace_devices(self, context='unknown'):
        scan_start = time.monotonic()
        self._usb_stats['scans'] += 1

        ace_devices = []

        active_protocols = KNOWN_PROTOCOLS if self._enable_ace_v2 \
            else tuple(p for p in KNOWN_PROTOCOLS if p is not AceProtocolV2)
        for protocol_cls in active_protocols:
            for path in protocol_cls.discover():
                if path in ace_devices:
                    continue
                self._ace_path_protocol[path] = protocol_cls
                ace_devices.append(path)
                real_dev = os.path.basename(os.path.realpath(path))
                logging.info('[multiACE] Found device %s (%s) protocol=%s' % (
                    path, real_dev, protocol_cls.NAME))

        ace_devices.sort(key=self._ace_path_sort_key)

        scan_ms = (time.monotonic() - scan_start) * 1000
        self._usb_log.info('SCAN [%s] found=%d time=%.1fms devices=[%s]',
                           context, len(ace_devices), scan_ms,
                           ', '.join('%s(%s)->%s' % (
                               d, self._ace_path_protocol.get(d, type('_', (), {'NAME': '?'})).NAME,
                               os.path.basename(os.path.realpath(d))) for d in ace_devices))
        return ace_devices

    def _apply_log_levels(self):
        """Apply current debug flags to file-logger levels. Setting a
        logger above CRITICAL turns every .info/.warning/.error/.debug
        call on it into a no-op without touching call sites."""
        off = logging.CRITICAL + 1
        self._usb_log.setLevel(logging.DEBUG if self._usb_debug_enabled else off)
        gated = logging.DEBUG if self._state_debug_enabled else off
        self._telemetry_log.setLevel(gated)
        self._wiggle_log.setLevel(gated)

        self._fa_log.setLevel(logging.DEBUG if self.fa_debug else logging.WARNING)

    def _t(self, key, **params):
        """
        Translate a dotted key against the loaded catalog. Returns the
        formatted string, or the key itself when not found (so log lines
        always carry SOMETHING readable). Index-style params are NOT
        auto-shifted here - caller passes display-ready values via
        self._disp(idx) when appropriate.
        """
        v = getattr(self, '_i18n', None) or {}
        for p in key.split('.'):
            if not isinstance(v, dict):
                return key
            v = v.get(p)
            if v is None:
                return key
        if not isinstance(v, str):
            return key
        if not params:
            return v
        try:
            return v.format(**params)
        except Exception:
            return v

    def _reload_i18n_catalog(self):
        """(Re)load self._i18n for the current self._language. Used at startup
        and live by MULTIACE_SET_LANGUAGE."""
        i18n_dir = self._i18n_primary if os.path.isdir(self._i18n_primary) \
            else self._i18n_fallback
        try:
            self._i18n = _load_i18n_catalog(i18n_dir, self._language)
        except Exception as e:
            logging.info('[multiACE] i18n catalog load failed: %s' % e)
            self._i18n = {}

    cmd_MULTIACE_SET_LANGUAGE_help = (
        '[multiACE] Set message language (LANG=<code>), live reload + persist')
    def cmd_MULTIACE_SET_LANGUAGE(self, gcmd):
        lang = gcmd.get('LANG').strip()
        if not lang:
            raise gcmd.error('[multiACE] LANG is required')
        self._language = lang
        self._reload_i18n_catalog()
        try:
            if self.save_variables:
                self.save_variable('ace__language', lang, write=True)
        except Exception as e:
            logging.info('[multiACE] persist ace__language failed: %s' % e)
        self.log_always('[multiACE] language set to %s' % lang)

    def _disp(self, idx):
        """Apply display_index_base offset for log messages."""
        if idx is None:
            return '–'
        try:
            return int(idx) + getattr(self, '_display_index_base', 0)
        except (TypeError, ValueError):
            return idx

    _WEB_PIDFILE = '/tmp/multiace_web_klipper.pid'

    def _stop_old_web(self):
        """Kill a stale multiACE web instance (from a previous Klipper run
        or from the init.d script). Called on every klippy:ready so that
        backend code updates pick up after a Klipper restart."""
        import signal
        try:
            with open(self._WEB_PIDFILE, 'r') as f:
                old_pid = int((f.read() or '0').strip())
        except (FileNotFoundError, ValueError, OSError):
            old_pid = 0
        if old_pid > 0:
            try:
                os.kill(old_pid, signal.SIGTERM)
            except ProcessLookupError:
                old_pid = 0
            except OSError:
                old_pid = 0
        if old_pid > 0:

            for _ in range(40):
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    logging.info('[multiACE] web stopped old pid %d', old_pid)
                    break
                time.sleep(0.05)
            else:
                try:
                    os.kill(old_pid, signal.SIGKILL)
                    logging.info('[multiACE] web SIGKILLd old pid %d', old_pid)
                except OSError:
                    pass

        self._free_web_port()

    def _free_web_port(self):
        """Ensure port self._web_port is free. Tries fuser first, then
        falls back to pkill matching the uvicorn cmdline - fuser is absent
        on some firmware builds (e.g. 1.4), which previously left a stale
        uvicorn holding the port so every respawn failed to bind. Silent if
        the port is already free."""
        import socket, subprocess
        port_spec = '%d/tcp' % self._web_port

        def _port_busy():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            try:
                return s.connect_ex(('127.0.0.1', self._web_port)) == 0
            finally:
                s.close()

        def _evict(sig):
            for cmd in (['fuser', '-k', '-%s' % sig, port_spec],
                        ['pkill', '-%s' % sig, '-f', 'uvicorn.*main:app']):
                try:
                    subprocess.run(
                        cmd, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=3, check=False)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
                except Exception:
                    continue

        for _ in range(2):
            if not _port_busy():
                return
            _evict('TERM')
            logging.info('[multiACE] web port %d held by other process, '
                         'evicted (TERM)', self._web_port)
            time.sleep(0.5)

        if _port_busy():
            _evict('KILL')
            logging.info('[multiACE] web port %d still held, sent KILL',
                         self._web_port)
            time.sleep(0.3)

    _WEB_INITD = '/etc/init.d/S98multiace-web'

    def _web_port_busy(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            return s.connect_ex(('127.0.0.1', self._web_port)) == 0
        except OSError:
            return False
        finally:
            s.close()

    def _kill_own_klippy_web(self):
        """Kill ONLY a web head this Klipper instance spawned itself in a
        previous (older) version - tracked by _WEB_PIDFILE. That process
        is owned by lava and therefore killable by us. We must never
        touch a web head started by the S98 init daemon at boot (runs as
        root, different/no pidfile) - that one is the correct standalone
        instance and lava can't kill it anyway. Returns True if we killed
        our own old child."""
        import signal
        try:
            with open(self._WEB_PIDFILE, 'r') as f:
                pid = int((f.read() or '0').strip())
        except (FileNotFoundError, ValueError, OSError):
            return False
        if pid <= 0:
            return False
        killed = False
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
        except OSError:
            return False
        if killed:
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            logging.info('[multiACE] web: stopped own old klippy-child pid %d', pid)
        try:
            os.unlink(self._WEB_PIDFILE)
        except OSError:
            pass
        return killed

    def _spawn_multiace_web(self):
        """
        Start the multiACE Web FastAPI service via the standalone init
        daemon (/etc/init.d/S98multiace-web).

        The web head must NOT run as a Klipper child: a child uvicorn
        shares the reactor's scheduling context and intermittently steals
        the ~50ms multi-MCU homing-probe margin from toolhead e3, tripping
        0003 "Communication timeout during homing". So ace.py only ever
        (re)starts the standalone S98 daemon, never spawns uvicorn itself.

        Ownership rules (U1 has NO sudo; klippy/ace.py run as lava):
          - baked image: S98 already started the web at boot as root
            (ppid 1) -> port 7126 busy by a root process we can't (and
            must not) touch. We leave it: the online updater needs that
            root instance, and it's already correct. Do nothing.
          - SSH install: S98 is skipped at boot (overlay mounts after the
            rcS S?? scan), so nothing is listening -> we start S98 as lava.
            The web runs as lava; that's fine - the installer chowns the
            klipper dirs to lava so the lava web head can still apply
            online updates (see multiace_update.sh writability check).
          - upgrade from an older ace.py that spawned a Klipper-child
            uvicorn: that child (ours, lava-owned, tracked by _WEB_PIDFILE)
            may still hold 7126. We kill only that one, then start S98.
        """
        if not self._enable_web:
            return
        backend = os.path.join(self._web_dir, 'backend')
        if not os.path.isdir(backend) or not os.path.isfile(os.path.join(backend, 'main.py')):
            logging.info('[multiACE] web not installed at %s - skip', self._web_dir)
            return
        if not os.path.isfile(self._WEB_INITD):
            logging.info('[multiACE] web init script %s missing - web not '
                         'started. Run install_multiace.sh --install-web.',
                         self._WEB_INITD)
            return
        if self._web_port_busy():
            if self._kill_own_klippy_web():
                for _ in range(20):
                    if not self._web_port_busy():
                        break
                    time.sleep(0.1)
            if self._web_port_busy():
                logging.info('[multiACE] web already running on :%d '
                             '(standalone daemon) - leaving it untouched',
                             self._web_port)
                self.log_always(self._t('msg.web_running'))
                return
        import subprocess
        try:
            subprocess.run(['sh', self._WEB_INITD, 'start'],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=45, check=False)
            logging.info('[multiACE] web started via init daemon %s '
                         '(standalone, not a Klipper child)', self._WEB_INITD)
            self.log_always(self._t('msg.web_running'))
        except Exception as e:
            logging.info('[multiACE] init-daemon web start failed: %s', e)

    def _disable_stock_entangle_detect(self):
        for head in range(4):
            ed = self.printer.lookup_object(
                'filament_entangle_detect e%d_filament' % head, None)
            if ed is None or not hasattr(ed, 'skip_entangle_check'):
                continue
            try:
                ed.skip_entangle_check(True)
                logging.info(
                    '[multiACE] disabled stock filament_entangle_detect '
                    'on head %d (incompatible with ACE topology)' % head)
            except Exception as e:
                logging.info(
                    '[multiACE] failed to disable filament_entangle_detect '
                    'on head %d: %s' % (head, e))

    def _touch_homing_flag(self):
        """Refresh the tmpfs homing-gate flag's mtime. The web daemon
        pauses its Moonraker polling while this flag is fresh, keeping
        I/O pressure off the homing-probe window (0003 mitigation).
        Cheap RAM write on the reactor thread; never fatal."""
        try:
            with open(self._homing_flag_path, 'w') as f:
                f.write('1')
        except Exception:
            pass

    def _clear_homing_flag(self):
        try:
            os.unlink(self._homing_flag_path)
        except OSError:
            pass

    def _feedlog_tick(self, eventtime):
        try:
            ps = self.printer.lookup_object('print_stats', None)
            if ps is None or getattr(ps, 'state', '') != 'printing':
                return eventtime + V2_FEED_LOG_INTERVAL
            idx = self._active_device_index
            proto = self._protocols.get(idx)
            if proto is None or getattr(proto, 'NAME', None) != 'v2':
                return eventtime + V2_FEED_LOG_INTERVAL
            slot = self._feed_assist_per_ace.get(idx, -1)
            if slot is None or slot < 0:
                return eventtime + V2_FEED_LOG_INTERVAL
            try:
                en = self.toolhead.get_extruder().get_name()
                head = 0 if en == 'extruder' else int(en.replace('extruder', ''))
            except Exception:
                head = -1
            def _cb(self, response, _i=idx, _s=slot, _h=head):
                self._feedlog_record(_i, _s, _h, response)
            self.send_request_to(idx, {'method': 'get_feed_info'}, _cb)
        except Exception as e:
            try:
                self._feedlog.info('tick error: %s' % e)
            except Exception:
                pass
        return eventtime + V2_FEED_LOG_INTERVAL

    def _feedlog_record(self, idx, slot, head, response):
        try:
            fi = ((response or {}).get('result') or {}).get('feed_info') or []
            rec = None
            for s in fi:
                if s.get('index') == slot:
                    rec = s
                    break
            if rec is None:
                return
            decoder = int(rec.get('decoder', 0))
            steps = int(rec.get('steps', 0))
            length = int(rec.get('length', 0))
            ext = 0.0
            try:
                pt = self.printer.lookup_object('mcu').estimated_print_time(
                    self.reactor.monotonic())
                ext = self.toolhead.get_extruder().find_past_position(pt)
            except Exception:
                ext = 0.0
            key = (idx, slot)
            last = self._feedlog_last.get(key)
            if last is None:
                dext = ddec = 0.0
                ratio = 0.0
            else:
                dext = ext - last[0]
                ddec = decoder - last[1]
                ratio = (ddec / dext) if abs(dext) > 1e-6 else 0.0
            self._feedlog_last[key] = (ext, decoder)
            self._feedlog.info(
                'head=%s ace=%d slot=%d ext=%.2f dec=%d steps=%d len=%d '
                'dext=%+.2f ddec=%+d ratio=%.2f'
                % (head, idx, slot, ext, decoder, steps, length,
                   dext, ddec, ratio))
        except Exception as e:
            try:
                self._feedlog.info('record error: %s' % e)
            except Exception:
                pass

    def _airlog_tick(self, eventtime):
        if getattr(self, '_fa_context', 'idle') != 'print':
            self._airlog_state = None
            return eventtime + AIRLOG_EMIT_S
        try:
            ext = self.toolhead.get_extruder()
            name = ext.get_name()
            head = 0 if name == 'extruder' else int(
                name.replace('extruder', '') or 0)
            vel = 0.0
            try:
                mr = self.printer.lookup_object('motion_report', None)
                if mr is not None:
                    vel = abs(float(mr.get_status(eventtime).get(
                        'live_extruder_velocity', 0.0) or 0.0))
            except Exception:
                pass
            freq = None
            if vel >= 0.3:
                try:
                    freq = float(ext.binding_probe.sensor.get_coil_freq())
                except Exception:
                    freq = None
            st = self._airlog_state
            if st is None or st['head'] != head:
                st = self._airlog_state = {
                    'head': head, 'start': eventtime, 'n': 0,
                    'cmin': None, 'cmax': None,
                    'vsum': 0.0, 'vmax': 0.0, 'nmove': 0}
            st['n'] += 1
            if freq is not None:
                st['cmin'] = (freq if st['cmin'] is None
                              else min(st['cmin'], freq))
                st['cmax'] = (freq if st['cmax'] is None
                              else max(st['cmax'], freq))
            st['vsum'] += vel
            st['vmax'] = max(st['vmax'], vel)
            if vel >= 0.3:
                st['nmove'] += 1
            if eventtime - st['start'] >= AIRLOG_EMIT_S and st['n'] > 0:
                delta = (st['cmax'] - st['cmin']
                         if st['cmin'] is not None else None)
                self._wiggle_log.info(
                    'airlog head=%d win=%.0fs n=%d moving=%d vel_mean=%.1f '
                    'vel_max=%.1f coil_min=%s coil_max=%s coil_delta=%s',
                    st['head'], eventtime - st['start'], st['n'],
                    st['nmove'], st['vsum'] / st['n'], st['vmax'],
                    ('%.0f' % st['cmin']) if st['cmin'] is not None else '-',
                    ('%.0f' % st['cmax']) if st['cmax'] is not None else '-',
                    ('%.0f' % delta) if delta is not None else '-')
                self._airlog_state = None
        except Exception:
            logging.exception('[multiACE] airlog tick failed')
            self._airlog_state = None
        return eventtime + AIRLOG_SAMPLE_S

    def _reactor_stall_watchdog(self, eventtime):
        import gc
        sched = getattr(self, '_watchdog_next', None)
        try:
            gen2 = gc.get_stats()[2]['collections']
        except Exception:
            gen2 = None
        if sched is not None:
            late = eventtime - sched
            if late > REACTOR_STALL_THRESHOLD:
                prev_gen2 = getattr(self, '_watchdog_gen2', None)
                gc_hit = (prev_gen2 is not None and gen2 is not None
                          and gen2 > prev_gen2)
                logging.warning(
                    '[multiACE] reactor stall %.0fms%s (gc_counts=%s) - '
                    'correlate with any 0003-0522 Timer-too-close near this ts'
                    % (late * 1000.,
                       ' [gen2-GC in window]' if gc_hit else '',
                       gc.get_count()))
        self._watchdog_gen2 = gen2
        self._watchdog_next = eventtime + REACTOR_WATCHDOG_INTERVAL
        return self._watchdog_next

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')

        if self.stall_watchdog and getattr(self, '_watchdog_timer', None) is None:
            self._watchdog_next = None
            self._watchdog_gen2 = None
            self._watchdog_timer = self.reactor.register_timer(
                self._reactor_stall_watchdog, self.reactor.NOW)

        if self.airlog_enable and getattr(self, '_airlog_timer', None) is None:
            self._airlog_state = None
            self._airlog_timer = self.reactor.register_timer(
                self._airlog_tick, self.reactor.NOW)

        if V2_FEED_LOG and self._feedlog_timer is None:
            self._feedlog.info(
                '=== feed-vs-extruder log START interval=%.1fs '
                '(cols: ext=extruder mm, dec=ACE decoder, dext/ddec=deltas, '
                'ratio=ddec/dext) ===' % V2_FEED_LOG_INTERVAL)
            self._feedlog_timer = self.reactor.register_timer(
                self._feedlog_tick, self.reactor.NOW)

        self._clear_homing_flag()
        self._spawn_multiace_web()

        self._refresh_slot_overrides()

        try:
            fd = self.printer.lookup_object('filament_detect', None)
            ptc = self.printer.lookup_object('print_task_config', None)
            if fd is not None and ptc is not None:
                orig_cb = ptc._rfid_filament_info_update_cb
                def _multiace_rfid_cb(channel, info, is_clear=False, _orig=orig_cb):

                    has_content = bool(
                        (info.get('VENDOR') or '').strip()
                        or (info.get('MAIN_TYPE') or '').strip()
                        or info.get('OFFICIAL'))
                    if is_clear and not has_content and self._ace_mode != 'normal':
                        logging.info(
                            '[multiACE] suppressing RFID clear on channel %d '
                            '(mode=%s, multiACE manages)' % (channel, self._ace_mode))
                        return
                    mt = (info.get('MAIN_TYPE') or '').strip()
                    if not is_clear and mt and mt != 'NONE':
                        nv = self._norm_vendor_push(info.get('VENDOR'))
                        ns = self._norm_subtype_push(info.get('SUB_TYPE'))
                        if (nv != info.get('VENDOR')
                                or ns != info.get('SUB_TYPE')):
                            info = dict(info)
                            info['VENDOR'] = nv
                            info['SUB_TYPE'] = ns
                    return _orig(channel, info, is_clear)
                cbs = getattr(fd, '_notify_data_update_cb', None)
                if isinstance(cbs, list):
                    replaced = False
                    for i, cb in enumerate(cbs):
                        if cb is orig_cb:
                            cbs[i] = _multiace_rfid_cb
                            replaced = True
                            break
                    if not replaced:
                        cbs.append(_multiace_rfid_cb)
                    logging.info('[multiACE] filament_detect callback hook installed (clear-suppress + capture)')
        except Exception as e:
            logging.info('[multiACE] failed to install filament_detect hook: %s' % e)

        try:
            self._orig_set_ptc = self.gcode.register_command(
                'SET_PRINT_FILAMENT_CONFIG', None)
            if self._orig_set_ptc is not None:
                _ptc = self.printer.lookup_object('print_task_config', None)
                self._raw_set_ptc = getattr(
                    _ptc, 'cmd_SET_PRINT_FILAMENT_CONFIG', None)
                self.gcode.register_command(
                    'SET_PRINT_FILAMENT_CONFIG',
                    self._wrap_set_print_filament_config,
                    desc='[multiACE] wrap SET_PRINT_FILAMENT_CONFIG to '
                         'capture display edits as picker overrides')
        except Exception as e:
            logging.info(
                '[multiACE] failed to wrap SET_PRINT_FILAMENT_CONFIG: %s' % e)

        for log in (self._state_log, self._usb_log, self._telemetry_log, self._wiggle_log):
            for handler in log.handlers:
                if hasattr(handler, 'doRollover'):
                    try:
                        handler.doRollover()
                    except Exception:
                        pass

        try:
            ace_mtime = os.path.getmtime(os.path.abspath(__file__))
            from datetime import datetime
            ace_timestamp = datetime.fromtimestamp(ace_mtime).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ace_timestamp = 'unknown'
        self.log_always(self._t('msg.version_line',
            version=MULTIACE_VERSION, codename=MULTIACE_CODENAME,
            build=MULTIACE_BUILD_TAG, ts=ace_timestamp))
        logging.info('[multiACE] Version %s (%s) build=%s file=%s' % (
            MULTIACE_VERSION, MULTIACE_CODENAME, MULTIACE_BUILD_TAG, ace_timestamp))

        try:
            _bg = self.printer.lookup_object('ace_bg_swap', None)
            if _bg is not None:
                _bg_heads = sorted(getattr(_bg, 'enabled_heads', []) or [])
                self.log_always('[multiACE] bg-swap %s: enabled heads %s'
                                % (getattr(_bg, 'version', '?'),
                                   _bg_heads if _bg_heads else 'NONE'))
            else:
                logging.info('[multiACE] bg-swap: not loaded '
                             '(no [ace_bg_swap] section)')
        except Exception as e:
            logging.info('[multiACE] bg-swap banner failed: %s' % e)

        self._ace_mode = 'normal'
        if self.save_variables:
            self._ace_mode = self.save_variables.allVariables.get('ace__mode', 'normal')
            self._restore_head_manual()
            self._restore_head_feeder()
            self._restore_head_ace()
        if self._ace_mode == 'normal':
            logging.info('[multiACE] Normal mode - skipping ACE detection')
            return

        if self._ace_mode in ('multi', 'head'):
            self._restore_head_source()
            self._ensure_extruder_change_handler()
        else:
            logging.info('[multiACE] %s mode - no head_source tracking'
                         % self._ace_mode)

        self._disable_stock_entangle_detect()

        self._wrap_resume_command()

        self._refresh_ace_devices('startup')

        if self.ace_device_count is not None:
            expected = self.ace_device_count
            if len(self._ace_devices) < expected:
                self.log_always(self._t('msg.waiting_for_devices',
                    expected=expected, count=len(self._ace_devices)))
                deadline = time.monotonic() + 20.0
                attempt = 0
                while time.monotonic() < deadline and len(self._ace_devices) < expected:
                    self.reactor.pause(self.reactor.monotonic() + 1.0)
                    attempt += 1
                    self._refresh_ace_devices('startup_wait_%d' % attempt)
            if len(self._ace_devices) < expected:

                self._ace_startup_failed = True
                self.log_error(self._t('msg.usb_unstable',
                    expected=expected, count=len(self._ace_devices)))
                logging.info(
                    '[multiACE] Startup soft-fail (%d/%d ACEs) - skipping connect timer' % (
                        len(self._ace_devices), expected))
                return

            self._ace_canonical = list(self._ace_devices)
            self._ace_present = set(self._ace_canonical)
            self.log_always(self._t('msg.all_expected_found', expected=expected))

        if self._ace_devices:
            logging.info('[multiACE] Found %d device(s): %s' % (len(self._ace_devices), str(self._ace_devices)))
            self.log_always(self._t('msg.found_devices', count=len(self._ace_devices)))

            saved_device = self.save_variables.allVariables.get(self.VARS_ACE_ACTIVE_DEVICE, None)
            if saved_device and saved_device in self._ace_devices:
                self._active_device_index = self._ace_devices.index(saved_device)
                logging.info('[multiACE] Restored active device %d: %s' % (self._active_device_index, saved_device))
            else:
                self._active_device_index = 0

            self.serial_id = self._ace_devices[self._active_device_index]
        elif self.serial_id:
            logging.info('[multiACE] No devices auto-detected, using configured serial: %s' % self.serial_id)
        else:
            self._ace_startup_failed = True
            self.log_error(self._t('msg.no_ace_serial_configured'))
            return

        self._queue = queue.Queue()

        all_ok = True
        CONNECT_ATTEMPTS = 3
        for idx in range(len(self._ace_devices)):
            ok = False
            for attempt in range(CONNECT_ATTEMPTS):
                ok = self._open_ace(idx)
                if ok:
                    break
                if attempt < CONNECT_ATTEMPTS - 1:
                    self._usb_log.info(
                        'RETRY [startup_connect] idx=%d attempt=%d/%d failed, retrying in 1s',
                        idx, attempt + 1, CONNECT_ATTEMPTS)
                    time.sleep(1.0)
            if not ok:
                self.log_error(self._t('msg.open_ace_failed_attempts',
                    ace=self._disp(idx), attempts=CONNECT_ATTEMPTS))
                all_ok = False
        if not all_ok:
            self.log_error(self._t('msg.not_all_aces_opened'))

        self._set_active_idx(self._active_device_index)

    def _hotplug_monitor(self, eventtime):

        if self._auto_feed_enabled or self._swap_in_progress:
            return eventtime + 2.0

        try:
            current = set(self._scan_ace_devices('hotplug'))
            known = set(self._ace_devices)
            now = self.reactor.monotonic()

            for dev in known - current:
                if dev not in self._hotplug_gone:
                    self._hotplug_gone[dev] = now

            for dev in list(self._hotplug_gone.keys()):
                if dev in current:
                    gone_time = now - self._hotplug_gone[dev]
                    del self._hotplug_gone[dev]
                    if gone_time >= 5.0:

                        fresh_devices = sorted(current)
                        if dev in fresh_devices:
                            new_index = fresh_devices.index(dev)
                            self.log_always(self._t('msg.ace_returned_switching',
                                ace=self._disp(new_index), seconds=gone_time))
                            self.reactor.register_async_callback(
                                lambda et, idx=new_index: self.gcode.run_script_from_command(
                                    'ACE_SWITCH TARGET=%d' % idx))
                            return eventtime + 10.0

            for dev, gone_since in list(self._hotplug_gone.items()):
                gone_time = now - gone_since
                if gone_time >= 5.0 and gone_time < 7.0:
                    self.log_always(self._t('msg.ace_removed_reenable'))

        except Exception as e:
            logging.info('[multiACE] Hotplug monitor error: %s' % str(e))

        return eventtime + 2.0

    def _handle_disconnect(self):
        logging.info('[multiACE] Closing all ACE connections')
        for idx in list(self._serials.keys()):
            try:
                self._disconnect_from(idx)
            except Exception:
                pass
        self._queue = None

    def get_load_length(self, ace_idx, slot):
        """Lookup load_length with per-ACE/per-slot override priority."""
        v = self._ace_section_load_length_slot.get((ace_idx, slot))
        if v is not None:
            return v
        v = self._ace_section_load_length.get(ace_idx)
        if v is not None:
            return v
        return self.head_load_length.get(slot, self.load_length)

    def get_retract_length(self, ace_idx, slot):
        """Lookup retract_length with per-ACE/per-slot override priority."""
        v = self._ace_section_retract_length_slot.get((ace_idx, slot))
        if v is not None:
            return v
        v = self._ace_section_retract_length.get(ace_idx)
        if v is not None:
            return v
        return self.retract_length

    def get_swap_retract_length(self, ace_idx, slot):
        """Swap unload retract length with per-ACE/per-slot override
        priority, falling back to the global swap_retract_length. A value
        of 0 (empty or explicit) means 'use the normal default retract'."""
        v = self._ace_section_swap_retract_length_slot.get((ace_idx, slot))
        if v is not None:
            return v
        v = self._ace_section_swap_retract_length.get(ace_idx)
        if v is not None:
            return v
        return self.swap_retract_length

    def get_purge_length(self):
        """Flush LENGTH for the stock INNER_FLUSH_FILAMENT. A per-swap
        override (multiACE Pro) wins, else the swap_purge_length config
        value. 0 means 'use the stock default' (caller omits LENGTH=)."""
        if self._purge_length_override is not None:
            return self._purge_length_override
        return self.swap_purge_length

    def get_feed_speed(self, ace_idx):
        """Lookup feed_speed with [ace N] override, falling back to [ace]."""
        v = self._ace_section_feed_speed.get(ace_idx)
        if v is not None:
            return v
        return self.feed_speed

    def get_retract_speed(self, ace_idx):
        """Lookup retract_speed with [ace N] override, falling back to [ace]."""
        v = self._ace_section_retract_speed.get(ace_idx)
        if v is not None:
            return v
        return self.retract_speed

    def _sync_ptc_to_active_ace(self):
        """Push SET_PRINT_FILAMENT_CONFIG for slots 0..3 of the currently
        active ACE so the Snapmaker display reflects the live slot
        belegung after an unload.

        No-op when any toolhead still carries filament - Snapmaker's
        print_task_config holds the per-extruder filament profile during
        a print, and clobbering it mid-print would lie to the firmware
        about what's loaded. Safe to call from every head_source[h] = None
        site: the guard makes it a no-op except when the last head went
        empty.

        Data source priority per slot:
          1. self._slot_overrides[ace_slot]   (user labels via Web UI)
          2. self._info_per_ace[ace].slots[]  (RFID from ACE hardware)
          3. Empty marker (NONE / 000000FF) for unconfigured slots.

        Failures are logged but never raised - display drift is cosmetic,
        not a print-blocking concern.
        """
        if any(self._head_source.get(h) is not None for h in range(4)):
            return
        head_mode = getattr(self, '_ace_mode', 'multi') == 'head'
        active_idx = self._active_device_index
        lines = []
        for head in range(4):
            if not self.head_uses_ace(head):
                continue
            ace_idx = active_idx
            slot_idx = head
            if head_mode:
                ace_idx = self.head_ace_for(head)
                _s = self._first_loaded_slot_for_ace(ace_idx)
                if _s is not None:
                    slot_idx = _s
            info = self._info_per_ace.get(ace_idx) or {}
            slots_by_index = {s.get('index', n): s
                              for n, s in enumerate(info.get('slots') or [])
                              if isinstance(s, dict)}
            ov = self._slot_overrides.get('%d_%d' % (ace_idx, slot_idx)) or {}
            s = slots_by_index.get(slot_idx) or {}
            mat = (ov.get('material') or s.get('material')
                   or s.get('type') or '').strip()
            sub = (ov.get('subtype') or s.get('subtype') or '').strip()
            brand = (ov.get('brand') or s.get('brand') or '').strip()
            color = (ov.get('color') or '').strip().lstrip('#').upper()
            if not color:
                rgb = s.get('color')
                if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                    try:
                        color = '%02X%02X%02X' % tuple(int(v) for v in rgb[:3])
                    except (TypeError, ValueError):
                        color = ''
            push_type = mat if mat else 'NONE'
            push_vendor = brand if brand else 'NONE'
            push_color = (color + 'FF') if color else '000000FF'
            push_subtype = sub
            self._expect_ptc_push(head, push_type, push_color,
                                  push_vendor, push_subtype)
            lines.append(
                'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=%d '
                'FILAMENT_TYPE="%s" FILAMENT_COLOR_RGBA=%s '
                'VENDOR="%s" FILAMENT_SUBTYPE="%s"' % (
                    head, push_type, push_color, push_vendor, push_subtype))
        if not lines:
            return
        try:
            self.gcode.run_script_from_command('\n'.join(lines))
            logging.info(
                '[multiACE] PTC sync -> per-head (all heads empty, head_mode=%s)'
                % head_mode)
        except Exception as e:
            logging.info('[multiACE] PTC sync failed: %s' % e)

    def _fa_trace(self, msg):
        """Log FA/load transitions to multiace_fa.log. Helps diagnose
        flakey first-load failures or unexpected FA suppression by showing
        every gate/context transition and call site. _fa_log level is
        gated by fa_debug (DEBUG when on, WARNING when off) so trace
        info is silent in production but failures persist."""
        self._fa_log.info(msg)

    def _is_v2_idx(self, idx):
        proto = self._protocols.get(idx)
        return proto is not None and getattr(proto, 'NAME', None) == 'v2'

    def _v2_get_slot_status(self, idx, slot):
        """Return the device's real slot_status for (idx, slot), or None if
        unknown. Source of truth for whether FA is actually running on the
        device (vs the host-side _feed_assist_per_ace cache, which can go
        stale across swaps/reconnects/disarms)."""
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return None
        info = self._info_per_ace.get(idx) or {}
        for s in info.get('slots') or []:
            if s.get('index') == slot:
                return s.get('slot_status')
        vstate = self._v2_velocity_state.get(idx) or {}
        return (vstate.get('last_slot_statuses') or {}).get(slot)

    def _v2_any_slot_active(self, idx):
        """True if any slot on this V2 is in a real motor motion (feeding a
        spool to the gate on insert, unloading, rolling back) - i.e. the
        device is LEGITIMATELY busy, not hung/stale. Device-truth via
        _v2_get_slot_status (the same source get_status reports 'busy' from)."""
        if not self._is_v2_idx(idx):
            return False
        for s in range(4):
            if self._v2_get_slot_status(idx, s) in V2_ACTIVE_MOTION_STATES:
                return True
        return False

    def _is_actively_printing(self):
        """True only while a print is actively running (print_stats 'printing').
        A paused/idle print is NOT actively printing - the same distinction the
        pre-load suppression uses (defer feeding a freshly-inserted spool until
        the ACE motor is not needed for the live print)."""
        ps = self.printer.lookup_object('print_stats', None)
        return ps is not None and getattr(ps, 'state', '') == 'printing'

    def _read_decoder(self, idx, slot):
        """[diag] Synchronous V2 decoder read (get_feed_info -> per-slot
        'decoder' = REAL filament movement, HW-proven). Returns int or None
        (V2-only; a V1 has no feed_info). Safe from a gcode/greenlet context:
        reactor.pause yields to the reader for the one response, like the
        whole-print logger's async read but blocking. Used only for the
        passive unload-retract decoder logging - no control depends on it."""
        try:
            if not self._is_v2_idx(idx):
                return None
        except Exception:
            return None
        box = {'d': None, 'done': False}
        def _cb(self, response, _b=box, _slot=slot):
            try:
                fi = ((response or {}).get('result') or {}).get(
                    'feed_info') or []
                for s in fi:
                    if s.get('index') == _slot:
                        _dv = int(s.get('decoder', 0))
                        if _dv >= (1 << 63):
                            _dv -= (1 << 64)
                        _b['d'] = _dv
                        break
            except Exception:
                pass
            _b['done'] = True
        try:
            self.send_request_to(idx, {'method': 'get_feed_info'}, _cb)
        except Exception:
            return None
        deadline = self.reactor.monotonic() + 1.0
        while not box['done'] and self.reactor.monotonic() < deadline:
            self.reactor.pause(self.reactor.monotonic() + 0.02)
        return box['d']

    def _retract_with_decoder_span(self, idx, slot, retract_fn):
        """[diag] Run retract_fn() while sampling the V2 decoder every ~100ms
        (async get_feed_info in a reactor timer, like _feedlog_tick - ace.dwell
        yields so the timer fires DURING the retract). Returns (span, n, dmin,
        dmax) signed. SPAN = max-min is base-agnostic: it catches a real stall
        regardless of the decoder's per-command reset/hold, which a before/after
        delta cannot (HW 2026-07-09: delta=d1-d0 read a full-movement retry as
        '0' because d0 held the prior command's value). V2-only; on a V1 it just
        runs retract_fn."""
        try:
            is_v2 = self._is_v2_idx(idx)
        except Exception:
            is_v2 = False
        if not is_v2:
            retract_fn()
            return (None, 0, None, None)
        box = {'min': None, 'max': None, 'n': 0}
        def _cb(self, response, _b=box, _slot=slot):
            try:
                fi = ((response or {}).get('result') or {}).get(
                    'feed_info') or []
                for s in fi:
                    if s.get('index') == _slot:
                        dv = int(s.get('decoder', 0))
                        if dv >= (1 << 63):
                            dv -= (1 << 64)
                        _b['min'] = dv if _b['min'] is None else min(_b['min'], dv)
                        _b['max'] = dv if _b['max'] is None else max(_b['max'], dv)
                        _b['n'] += 1
                        break
            except Exception:
                pass
        def _tick(eventtime, _idx=idx):
            try:
                self.send_request_to(_idx, {'method': 'get_feed_info'}, _cb)
            except Exception:
                pass
            return eventtime + 0.25
        timer = self.reactor.register_timer(_tick, self.reactor.NOW)
        try:
            retract_fn()
        finally:
            try:
                self.reactor.unregister_timer(timer)
            except Exception:
                pass
        span = None if box['min'] is None else (box['max'] - box['min'])
        return (span, box['n'], box['min'], box['max'])

    def _clear_fa_cache_for(self, idx, slot):
        if self._feed_assist_per_ace.get(idx, -1) == slot:
            self._feed_assist_per_ace[idx] = -1
        if idx == self._active_device_index and self._feed_assist_index == slot:
            self._feed_assist_index = -1

    def _fa_rearm_backoff_ok(self, idx, slot):
        """Back-off gate for the V2 FA re-arm churn (FA_REARM_MAX_FAILS, see the
        constant's note). Called as the LAST guard of each re-arm branch in the
        velocity tick, so it only counts an attempt that would otherwise re-arm.
        Returns True to proceed (log + schedule), False when the slot is
        suspended (caller skips both). A reconnect's transient dropped arms are
        allowed but NOT counted - the deliberate post-handshake re-arm sticks."""
        key = (idx, slot)
        if key in self._fa_rearm_suspended:
            return False
        if (self._reconnecting_per_ace.get(idx, False)
                or self._serial_failed_per_ace.get(idx, False)):
            return True
        n = self._fa_rearm_fails.get(key, 0) + 1
        self._fa_rearm_fails[key] = n
        if n >= FA_REARM_MAX_FAILS:
            self._fa_rearm_suspended.add(key)
            self._fa_log.warning(
                '[v2-recover] FA re-arm not sticking on ACE %d slot %d after '
                '%d tries (device keeps disarming - cut/absent filament? a '
                'mid-bowden cut is invisible to the gate + toolhead sensors) - '
                'SUSPENDING re-arm until tool change / extrusion resume / '
                'reconnect' % (idx, slot, n))
            return False
        return True

    def _fa_rearm_reset(self, idx, slot=None):
        """Clear the FA re-arm back-off counter + suspend flag. slot=None clears
        every slot of idx (reconnect / print start); a slot clears just that one
        (a stick on that slot / a tool change onto it)."""
        if slot is None:
            for k in [k for k in self._fa_rearm_fails if k[0] == idx]:
                self._fa_rearm_fails.pop(k, None)
            for k in [k for k in self._fa_rearm_suspended if k[0] == idx]:
                self._fa_rearm_suspended.discard(k)
        else:
            self._fa_rearm_fails.pop((idx, slot), None)
            self._fa_rearm_suspended.discard((idx, slot))

    def _v1_check_fa_health(self, idx, result):
        """LOG-ONLY V1 FA diagnosis (predicate HW-DISPROVED 2026-07-06).
        The original idea: V1 reports a GLOBAL cont_assist_time and assists one
        slot at a time, so "armed but cont_assist_time==0" = dropped FA ->
        re-arm. The USB-pull test print disproved it: cont_assist_time stayed
        0.0 and feed_assist_count static over 31 forced re-arms while FA
        demonstrably ran - the fields are NOT runtime truth on this firmware.
        Now gated off by default (v1_fa_monitor False) and, when enabled,
        LOGS the would-be trigger without re-arming. Do not re-introduce a
        re-arm here without a NEW hardware signal."""
        if not self.v1_fa_monitor:
            return
        if self._fa_context != 'print' or not self._auto_feed_enabled:
            self._v1_fa_notassist_streak[idx] = 0
            return
        if self._swap_in_progress or getattr(self, '_v2_active_rev_assist', False):
            return
        if self._reconnecting_per_ace.get(idx, False):
            return
        if idx in self._fa_print_disable:
            return
        slot = self._feed_assist_per_ace.get(idx, -1)
        if slot is None or slot < 0:
            self._v1_fa_notassist_streak[idx] = 0
            return
        _fa_head = (self._head_for_ace(idx)
                    if getattr(self, '_ace_mode', 'multi') == 'head' else slot)
        if _fa_head is not None and self.head_is_manual(_fa_head):
            self._v1_fa_notassist_streak[idx] = 0
            return
        cont = result.get('cont_assist_time')
        fac = result.get('feed_assist_count')
        assisting = isinstance(cont, (int, float)) and cont > 0
        if assisting:
            self._v1_fa_notassist_streak[idx] = 0
            return
        streak = self._v1_fa_notassist_streak.get(idx, 0) + 1
        self._v1_fa_notassist_streak[idx] = streak
        if streak < V1_FA_CONFIRM_TICKS:
            return
        now = time.monotonic()
        if now - self._v1_fa_last_rearm.get(idx, 0.0) < V1_FA_REARM_MIN_INTERVAL:
            return
        self._v1_fa_last_rearm[idx] = now
        self._v1_fa_notassist_streak[idx] = 0
        self._fa_log.info(
            '[v1-recover] ACE %d slot %d armed but not assisting '
            '(cont_assist_time=%s feed_assist_count=%s) - LOG-ONLY, '
            'predicate HW-disproved, no re-arm'
            % (idx, slot, cont, fac))

    def _v2_schedule_fa_rearm(self, idx, slot, reason, delay=0.05):
        """Deferred, guarded FA re-arm for a V2 head whose host cache may be
        stale. Coalesced per (idx, slot). Re-checks at fire time that we are
        still printing/loading this exact source, not in a rollback-assist
        window, and that the device is NOT already in a running state - then
        clears the stale cache and re-arms so start_feed_assist actually lands."""
        if not self._is_v2_idx(idx):
            return False
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            return False
        key = (idx, slot)
        if key in self._v2_fa_rearm_pending:
            return False
        self._v2_fa_rearm_pending.add(key)

        def _rearm(eventtime):
            self._v2_fa_rearm_pending.discard(key)
            if not self._auto_feed_enabled:
                return self.reactor.NEVER
            if self._fa_context not in ('print', 'load'):
                return self.reactor.NEVER
            if getattr(self, '_v2_active_rev_assist', False):
                return self.reactor.NEVER
            try:
                cur_ext = self.toolhead.get_extruder()
                cur_head = getattr(cur_ext, 'extruder_index',
                                   getattr(cur_ext, 'extruder_num', None))
                src = self._head_source.get(cur_head) if cur_head is not None else None
            except Exception:
                src = None
            if src is None or src.get('ace_index') != idx or src.get('slot') != slot:
                return self.reactor.NEVER
            status = self._v2_get_slot_status(idx, slot)
            if status in V2_FA_RUNNING_STATES:
                return self.reactor.NEVER
            self._fa_log.info(
                '[v2-recover] stale FA cache, rearming ACE %d slot %d '
                'reason=%s status=%s' % (
                    idx, slot, reason, status if status is not None else 'unknown'))
            self._clear_fa_cache_for(idx, slot)
            try:
                self._arm_fa_for(idx, slot, from_recovery=True)
            except Exception as e:
                logging.info('[multiACE] V2 FA rearm failed: %s' % e)
            return self.reactor.NEVER

        self.reactor.register_timer(_rearm, self.reactor.monotonic() + delay)
        return True

    def _sniff_print_gcode_loads(self):
        try:
            vsd = self.printer.lookup_object('virtual_sdcard', None)
            f = getattr(vsd, 'current_file', None)
            path = getattr(f, 'name', None) if f is not None else None
            if not path:
                fp = getattr(vsd, 'file_path', None)
                path = fp() if callable(fp) else None
            if not path:
                return False
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                head = fh.read(512 * 1024)
            if '; multiACE auto-load:' in head:
                return True
            if head.startswith('ACE_SWAP_HEAD ') or '\nACE_SWAP_HEAD ' in head:
                return True
            return False
        except Exception as e:
            logging.info('[multiACE] gcode load-sniff failed: %s' % e)
            return False

    def _on_print_start(self, *args):
        self._print_has_gcode_loads = self._sniff_print_gcode_loads()
        logging.info('[multiACE] print gcode carries multiACE loads: %s'
                     % self._print_has_gcode_loads)
        if self._ace_mode in ('multi', 'head'):

            self._ghost_heads = set()
            stale_heads = []
            ghost_heads = []
            manual_loaded_heads = []
            for head in range(4):
                sensor = self.printer.lookup_object(
                    'filament_motion_sensor e%d_filament' % head, None)
                if sensor is None:
                    continue
                detected = sensor.get_status(0)['filament_detected']
                src = self._head_source.get(head)
                if detected and src is None:
                    if not self.head_uses_ace(head):
                        manual_loaded_heads.append(head)
                    else:
                        ghost_heads.append(head)
                elif (not detected) and src is not None:
                    stale_heads.append(head)
                    self._head_source[head] = None
            if stale_heads:
                try:
                    self._save_head_source()
                except Exception:
                    pass
                logging.info(
                    '[multiACE] Print start: cleared stale head_source for '
                    'head(s) %s (sensor reports no filament)'
                    % ', '.join('T%d' % h for h in stale_heads))
            if ghost_heads:
                self._ghost_heads = set(ghost_heads)
                head_list = ', '.join('T%d' % h for h in ghost_heads)
                self.log_error(self._t('msg.ghost_heads', heads=head_list))
            if manual_loaded_heads:
                head_list = ', '.join('T%d' % h for h in manual_loaded_heads)
                self.log_always(
                    '[multiACE] Manual/feeder head(s): %s have filament at the '
                    'toolhead sensor (hand-loaded or stock-fed, no ACE source - '
                    'expected). ACE_SWAP_HEAD will be refused for these heads.'
                    % head_list)

            seen_slots = {}
            dup = []
            mismatched = []
            for head in range(4):
                src = self._head_source.get(head)
                if src is None or not self.head_uses_ace(head):
                    continue
                key = (src.get('ace_index'), src.get('slot'))
                if key in seen_slots:
                    dup.append((seen_slots[key], head, src.get('slot')))
                else:
                    seen_slots[key] = head
                if self._ace_mode == 'multi' and src.get('slot') != head:
                    mismatched.append((head, src.get('slot')))
            dup_ace = []
            if self._ace_mode == 'head':
                seen_aces = {}
                for head in range(4):
                    src = self._head_source.get(head)
                    if src is None or not self.head_uses_ace(head):
                        continue
                    a = src.get('ace_index')
                    if a in seen_aces:
                        dup_ace.append((seen_aces[a], head, a))
                    else:
                        seen_aces[a] = head
            if dup or mismatched or dup_ace:
                parts = ['T%s+T%s->slot %s' % (self._disp(a), self._disp(b),
                         self._disp(s)) for a, b, s in dup]
                parts += ['T%s->slot %s' % (self._disp(h), self._disp(s))
                          for h, s in mismatched]
                parts += ['T%s+T%s->ACE %s' % (self._disp(a), self._disp(b),
                          self._disp(x)) for a, b, x in dup_ace]
                self.log_error(self._t('msg.head_source_inconsistent',
                    detail=', '.join(parts)))
                self.reactor.register_async_callback(
                    lambda et: self.gcode.run_script_from_command('CANCEL_PRINT'))
                return

            for head in range(4):
                source = self._head_source.get(head)
                if source is None:
                    continue
                ace_idx = source['ace_index']
                if ace_idx >= len(self._ace_devices):
                    self.log_error(self._t('msg.print_start_head_needs_unavailable',
                        head=self._disp(head), ace=self._disp(ace_idx),
                        count=len(self._ace_devices)))
                    continue
                if not self._connected_per_ace.get(ace_idx, False):
                    self.log_error(self._t('msg.print_start_head_needs_disconnected',
                        head=self._disp(head), ace=self._disp(ace_idx)))
        self._auto_feed_enabled = True
        self._fa_context = 'print'
        self._serial_failed_pause_sent = False
        self._fa_failed_pause_sent = False
        self._fa_rearm_fails.clear()
        self._fa_rearm_suspended.clear()
        self._reopen_failed_aces_on_resume()
        self._runout_suppress_heads = set()
        self._resume_wipe_deadline = (self.reactor.monotonic()
                                      + RESUME_NOOP_WIPE_WINDOW)
        logging.info('[multiACE] Print started - auto-feed enabled')
        self._fa_trace('gate OPEN (context=print) via _on_print_start')

        try:
            extruder = self.toolhead.get_extruder()
            head_index = getattr(extruder, 'extruder_index',
                        getattr(extruder, 'extruder_num', None))
        except Exception:
            head_index = None
        if head_index is None:
            return
        source = self._head_source.get(head_index)
        if source is None:
            return
        if not self.head_uses_ace(head_index):
            return
        target_ace = source['ace_index']
        target_slot = source['slot']
        if target_ace >= len(self._ace_devices):
            return
        if not self._connected_per_ace.get(target_ace, False):
            self._audit_state('PRINT_START', {
                'head': head_index,
                'target_ace': target_ace,
                'action': 'ace_not_connected',
            })
            return

        if self._active_device_index != target_ace:
            self._set_active_idx(target_ace)
        try:
            self._arm_fa_for(target_ace, target_slot)
            self.log_always(self._t('msg.print_start_fa_enabled',
                ace=self._disp(target_ace), slot=self._disp(target_slot),
                head=self._disp(head_index)))
            self._audit_state('PRINT_START', {
                'head': head_index,
                'target_ace': target_ace,
                'target_slot': target_slot,
                'action': 'feed_assist_enabled',
            })
        except Exception as e:
            logging.info('[multiACE] print-start feed_assist enable failed: %s' % e)
            self._audit_state('PRINT_START', {
                'head': head_index,
                'action': 'feed_assist_enable_failed',
                'error': str(e)[:200],
            })

    def _on_print_end(self, *args):
        self._auto_feed_enabled = False
        self._fa_context = 'idle'
        self._runout_suppress_heads = set()
        logging.info('[multiACE] Print ended - auto-feed disabled')
        self._fa_trace('gate CLOSE (context=idle) via _on_print_end')
        stopped_any = False
        for idx in range(len(self._ace_devices)):
            if self._feed_assist_per_ace.get(idx, -1) != -1:
                try:
                    self._disarm_fa_for(idx)
                    stopped_any = True
                except Exception as e:
                    logging.info('[multiACE] print-end stop_feed_assist[%d] failed: %s' % (idx, e))
        if stopped_any:
            self._audit_state('PRINT_END', {
                'action': 'feed_assist_disabled',
            })

    def _color_message(self, msg):
        try:
            html_msg = msg.format(
                '</span>',
                '<span style="color:#FFFF00">',
                '<span style="color:#90EE90">',
                '<span style="color:#458EFF">',
                '<b>',
                '</b>'
            )
        except (IndexError, KeyError, ValueError) as e:
            html_msg = msg
        return html_msg

    def log_warning(self, msg):
        c_msg = self._color_message(f'{{1}}{msg}{{0}}')
        self.gcode.respond_raw(c_msg)

    def log_always(self, msg: str, color=False):
        c_msg = self._color_message(msg) if color else msg
        self.gcode.respond_raw(c_msg)

    def log_error(self, msg):
        self.error_msg = msg
        logging.error(msg)
        self.gcode.respond_raw(f"!! {msg}")

    def _restore_pos_for_pause(self, saved_pos):

        if not saved_pos:
            return
        try:
            self.gcode.run_script_from_command('G90')
            self.gcode.run_script_from_command(
                'G0 Z%.3f F600' % (saved_pos[2] + 3.0))
            self.gcode.run_script_from_command(
                'G0 Y%.3f F12000' % saved_pos[1])
            self.gcode.run_script_from_command(
                'G0 X%.3f F12000' % saved_pos[0])
            self.gcode.run_script_from_command(
                'G0 Z%.3f F600' % (saved_pos[2] + 2.0))
            self.toolhead.wait_moves()
            logging.info(
                '[multiACE] Swap PAUSE: restored pos X=%.2f Y=%.2f Z=%.2f '
                '(pre-PAUSE, prevents RESUME-traverse ram)' % (
                    saved_pos[0], saved_pos[1], saved_pos[2]))
        except Exception as e:
            logging.info(
                '[multiACE] Swap PAUSE: pos restore failed: %s' % e)

    def _swap_back_to_orig_for_pause(self, switched_head, orig_ext_name):

        if not switched_head:
            return
        try:
            orig_head_idx = (0 if orig_ext_name == 'extruder'
                             else int(orig_ext_name.replace('extruder', '')))
            logging.info(
                '[multiACE] Swap PAUSE: switching active extruder back '
                'to %s before pause (was on swap head)' % orig_ext_name)
            self.gcode.run_script_from_command('T%d A0' % orig_head_idx)
            self.toolhead.wait_moves()
        except Exception as e:
            logging.info(
                '[multiACE] Swap PAUSE: T-switch back to %s failed: %s'
                % (orig_ext_name, e))

    def _restore_machine_state_for_resume(self):
        try:
            ps = self.printer.lookup_object('print_stats', None)
            if ps is not None:
                state = ps.get_status(self.reactor.monotonic()).get('state')
                if state not in ('printing', 'paused'):
                    logging.info(
                        '[multiACE] recovery: print_stats=%s - skip machine_state '
                        'restore' % state)
                    return
            msm = self.printer.lookup_object('machine_state_manager', None)
            if msm is None:
                return
            cur = str(msm.get_status().get('main_state'))
            if cur == 'PRINTING':
                return
            if cur != 'IDLE':
                self.gcode.run_script_from_command(
                    'SET_MAIN_STATE MAIN_STATE=IDLE ACTION=IDLE')
            self.gcode.run_script_from_command('SET_MAIN_STATE MAIN_STATE=PRINTING')
            logging.info(
                '[multiACE] recovery: machine_state main_state %s -> PRINTING '
                '(satisfies stock RESUME guard so paused print is resumable)'
                % cur)
        except Exception as e:
            logging.info(
                '[multiACE] recovery: machine_state restore failed: %s' % e)

    def _wrap_resume_command(self):
        for name in ('RESUME', '_RESUME_BASE'):
            try:
                prev = self.gcode.register_command(name, None)
            except Exception as e:
                logging.info('[multiACE] %s unregister failed: %s' % (name, e))
                continue
            if prev is None:
                logging.info('[multiACE] %s not registered - skip wrap' % name)
                continue
            try:
                self.gcode.register_command(
                    name, self._make_resume_wrap(prev, name),
                    desc='multiACE: normalise machine_state, then resume')
                logging.info('[multiACE] wrapped %s for machine_state restore'
                             % name)
            except Exception as e:
                logging.info('[multiACE] %s re-register failed (%s) - restoring '
                             'stock handler' % (name, e))
                try:
                    self.gcode.register_command(name, prev)
                except Exception:
                    pass

    def _make_resume_wrap(self, prev, name):
        def _wrap(gcmd):
            try:
                self._restore_machine_state_for_resume()
            except Exception as e:
                logging.info('[multiACE] %s wrap: machine_state restore '
                             'failed: %s' % (name, e))
            prev(gcmd)
        return _wrap

    def _load_slip_details(self, head, ace_index, slot):
        """Classify a failed ACE load into its two REAL modes and return
        (detail_msg, recovery_steps) for _pause_for_recovery. Used by the
        swap path and by the direct feed path in filament_feed_ace, so both
        word the same failure identically.

        The old single 'Load slip ... unload, reload, RESUME' message hid two
        unrelated failures: sensor clear = the filament NEVER REACHED the
        toolhead (no transport - spool/bowden/ACE problem; unload+reload is
        the right recovery) vs sensor present = the filament arrived and was
        gripped but the nozzle does not extrude (no flow - a clog; unload+
        reload cannot fix it and points the user at the wrong end). The
        toolhead sensor alone discriminates cleanly; the raw feed error stays
        in klippy.log. Fail-open to the transport wording if the sensor
        cannot be read."""
        arrived = False
        try:
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            if sensor is not None:
                arrived = bool(sensor.get_status(0)['filament_detected'])
        except Exception:
            arrived = False
        if arrived:
            detail = self._t('msg.pause_swap_load_no_flow',
                head=self._disp(head), ace=self._disp(ace_index),
                slot=self._disp(slot))
            steps = [
                'Check the nozzle of Head %d for a clog (heat + manual push / cold pull)'
                    % self._disp(head),
                'ACE_UNLOAD_HEAD HEAD=%d           (only if the filament must come out)'
                    % head,
                'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d   (reload after clearing)'
                    % (head, ace_index, slot),
                'RESUME                           (continue the print)',
            ]
        else:
            detail = self._t('msg.pause_swap_load_no_transport',
                head=self._disp(head), ace=self._disp(ace_index),
                slot=self._disp(slot))
            steps = [
                'Check spool / bowden / ACE path of ACE %d Slot %d (knot, tip, gate)'
                    % (self._disp(ace_index), self._disp(slot)),
                'ACE_UNLOAD_HEAD HEAD=%d           (clear partial filament)'
                    % head,
                'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d   (reload)'
                    % (head, ace_index, slot),
                'RESUME                           (continue the print)',
            ]
        return detail, steps

    def _pause_for_recovery(self, gcmd, detail_msg, recovery_steps, code=0):
        for i, step in enumerate(recovery_steps, 1):
            try:
                self.gcode.run_script_from_command(
                    'RESPOND TYPE=echo MSG="  %d. %s"' % (
                        i, step.replace('"', "'")))
            except Exception:
                pass
        self.error_msg = detail_msg
        self._audit_state('PAUSE_RECOVERY', {
            'detail': detail_msg,
            'steps': recovery_steps,
        })

        active = self.toolhead.get_extruder().get_name() if self.toolhead else 'extruder'
        idx = 0 if active == 'extruder' else int(active.replace('extruder', '') or 0)
        if not self._head_is_loaded(idx):
            self._runout_suppress_heads.add(idx)
            logging.info(
                '[multiACE] recovery: runout suppressed on empty active head %d '
                'until it is (re)loaded' % idx)
        if getattr(self, '_ace_mode', 'multi') == 'head':
            for h in range(4):
                if self.head_uses_ace(h) and not self._head_is_loaded(h):
                    if h not in self._runout_suppress_heads:
                        self._runout_suppress_heads.add(h)
                        logging.info(
                            '[multiACE] recovery (head mode): runout suppressed '
                            'on unloaded ACE head %d until it is loaded' % h)
        self._restore_machine_state_for_resume()
        raise gcmd.error(
            message=detail_msg.replace('"', "'")[:200], action='pause',
            id=525, index=idx, code=code, oneshot=1, level=2)

    def _machine_state_after_feed_op(self):
        try:
            ps = self.printer.lookup_object('print_stats', None)
            if ps is not None and ps.get_status(
                    self.reactor.monotonic()).get('state') == 'paused':
                self._restore_machine_state_for_resume()
                return
        except Exception as e:
            logging.info('[multiACE] _machine_state_after_feed_op check failed: %s' % e)
        self.gcode.run_script_from_command(
            "SET_MAIN_STATE MAIN_STATE=IDLE ACTION=IDLE")

    def _ace_event(self, name, **fields):
        """Emit a machine-readable engine event over Moonraker's
        gcode_response stream so an external (arm's-length) host can react
        without polling. Bumps event_seq and echoes a single line of the
        form: multiace_event <name> key=val ... seq=<n>. Best-effort: a
        failure here never disturbs the swap. See docs/ENGINE_API.md
        section 5.

        Also mirrored to klippy.log: the RESPOND reaches the response pipe
        only, and Moonraker keeps the console live-only (~1000 lines, nothing
        on disk), so without the mirror an event cannot be read after the
        fact from a submitted log. Logged BEFORE the RESPOND so the line
        survives a failing response pipe.
        """
        self._event_seq += 1
        fields['seq'] = self._event_seq
        parts = ' '.join('%s=%s' % (k, v) for k, v in fields.items())
        line = 'multiace_event %s %s' % (name, parts)
        logging.info('[multiACE] %s' % line)
        try:
            self.gcode.run_script_from_command(
                'RESPOND TYPE=command MSG="%s"' % line)
        except Exception:
            pass

    def save_variable(self, variable, value, write=False):
        self.save_variables.allVariables[variable] = value
        if write:
            self.write_variables()

    def rgb2hex(self, r, g, b):
        return "%02X%02X%02X" % (r, g, b)

    def delete_variable(self, variable, write=False):
        _ = self.save_variables.allVariables.pop(variable, None)
        if write:
            self.write_variables()

    def write_variables(self):
        mmu_vars_revision = self.save_variables.allVariables.get(self.VARS_ACE_REVISION, 0) + 1
        self.gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE={self.VARS_ACE_REVISION} VALUE={mmu_vars_revision}")

    def _serial_disconnect(self):
        idx = self._active_device_index
        self._disconnect_from(idx)
        self._serial = None
        self._connected = False
        self.heartbeat_timer = None
        self.ace_dev_fd = None

    def _connect(self, eventtime):
        idx = self._active_device_index
        ok = self._open_ace(idx)
        if ok:
            self._set_active_idx(idx)
            return self.reactor.NEVER
        return eventtime + 1.0

    def _make_default_info(self, idx=None):
        if idx is None:
            idx = self._active_device_index
        protocol = self._protocols.get(idx)
        if protocol is None:
            return AceProtocolV1().make_default_info()
        return protocol.make_default_info()

    def _next_request_id_for(self, idx):

        with self._seq_lock:
            rid = self._request_ids.get(idx, 0) + 1
            if rid >= 300000:
                rid = 1
            self._request_ids[idx] = rid
            return rid

    def _set_active_idx(self, idx):
        if idx < 0 or idx >= len(self._ace_devices):
            return False
        self._active_device_index = idx
        self.serial_id = self._ace_devices[idx]
        self._serial = self._serials.get(idx)
        self._connected = self._connected_per_ace.get(idx, False)
        self._serial_failed = self._serial_failed_per_ace.get(idx, False)
        self._feed_assist_index = self._feed_assist_per_ace.get(idx, -1)
        info = self._info_per_ace.get(idx)
        if info is not None:
            self._info = info
        if idx in self._request_ids:
            self._request_id = self._request_ids[idx]
        gate_list = self._gate_status_per_ace.get(idx)
        if gate_list is not None:
            self.gate_status = gate_list
        self.ace_dev_fd = self._ace_dev_fds.get(idx)
        self.heartbeat_timer = self._heartbeat_timers.get(idx)
        try:
            self.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=%s VALUE=\"'%s'\"" % (
                    self.VARS_ACE_ACTIVE_DEVICE, self.serial_id))
        except Exception:
            pass
        return True

    def _open_ace(self, idx, on_ready=None):
        if idx >= len(self._ace_devices):
            return False
        serial_path = self._ace_devices[idx]
        logging.info('[multiACE] Try connecting ACE %d (%s)' % (idx, serial_path))
        try:
            logging.info('[multiACE][DBG] _open_ace ACE %d caller chain:\n%s' % (
                idx, ''.join(traceback.format_stack(limit=8)[:-1])))
        except Exception:
            pass
        self._usb_log.info('CONNECT attempt idx=%d serial=%s', idx, serial_path)
        connect_start = time.monotonic()

        old_ht = self._heartbeat_timers.pop(idx, None)
        if old_ht is not None:
            try:
                self.reactor.unregister_timer(old_ht)
            except Exception:
                pass
        old_vt = self._v2_velocity_timers.pop(idx, None)
        if old_vt is not None:
            try:
                self.reactor.unregister_timer(old_vt)
            except Exception:
                pass
        self._v2_velocity_state.pop(idx, None)
        old_stop = self._thread_stop_flags.pop(idx, None)
        if old_stop is not None:
            old_stop.set()
        old_fd = self._ace_dev_fds.pop(idx, None)
        if old_fd is not None:
            try:
                self.reactor.unregister_fd(old_fd)
            except Exception:
                pass
        old_ser = self._serials.pop(idx, None)
        if old_ser is not None:
            try:
                if old_ser.is_open:
                    old_ser.close()
            except Exception:
                pass
        for thread_dict in (self._reader_threads, self._writer_threads):
            old_t = thread_dict.pop(idx, None)
            if old_t is not None:
                try:
                    old_t.join(timeout=0.5)
                except Exception:
                    pass
        self._writer_queues.pop(idx, None)
        self._cb_locks.pop(idx, None)
        self._v2_filament_info_per_ace.pop(idx, None)
        self._v2_filament_info_pending.pop(idx, None)
        self._v2_filament_info_empty.pop(idx, None)

        def info_callback(self, response):
            if response.get('msg') != 'success':
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
            result = response.get('result', {})
            model = result.get('model', 'Unknown')
            firmware = result.get('firmware', 'Unknown')
            self._ace_models[idx] = (model, firmware)
            self._usb_log.info('CONNECT info idx=%d model=%s firmware=%s', idx, model, firmware)
            self.log_always(self._t('msg.ace_connected',
                ace=self._disp(idx), model=model, firmware=firmware), True)

        try:
            protocol_cls = self._ace_path_protocol.get(serial_path, AceProtocolV1)
            protocol = protocol_cls()
            self._protocols[idx] = protocol
            _open_res = {'ser': None, 'err': None}
            _open_done = threading.Event()
            _open_gaveup = threading.Event()
            def _open_worker(_p=protocol, _path=serial_path,
                             _baud=self.baud or protocol.DEFAULT_BAUD):
                try:
                    s = _p.open_transport(_path, _baud)
                except Exception as _e:
                    _open_res['err'] = _e
                    _open_done.set()
                    return
                if _open_gaveup.is_set():
                    try:
                        s.close()
                    except Exception:
                        pass
                else:
                    _open_res['ser'] = s
                _open_done.set()
            threading.Thread(target=_open_worker, daemon=True,
                             name='ace%d-open' % idx).start()
            _open_deadline = self.reactor.monotonic() + ACE_OPEN_TIMEOUT
            while not _open_done.is_set():
                if self.reactor.monotonic() > _open_deadline:
                    _open_gaveup.set()
                    self._usb_stats['connect_failures'] += 1
                    self._usb_log.warning(
                        'CONNECT open timeout idx=%d (>%.0fs, still off-thread)',
                        idx, ACE_OPEN_TIMEOUT)
                    logging.info('[multiACE] open ACE %d timed out '
                                 '(serial still opening off-thread)' % idx)
                    return False
                self.reactor.pause(self.reactor.monotonic() + 0.05)
            if _open_res['err'] is not None:
                raise _open_res['err']
            ser = _open_res['ser']
            if ser is None or not ser.is_open:
                return False
            self._serials[idx] = ser
            self._connected_per_ace[idx] = True
            self._serial_failed_per_ace[idx] = False
            self._request_ids[idx] = 0
            self._callback_maps[idx] = {}
            self._read_buffers[idx] = bytearray()
            self._info_per_ace[idx] = protocol.make_default_info()
            self._feed_assist_per_ace.setdefault(idx, -1)
            _gl = self._gate_status_per_ace.setdefault(
                idx, [GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN])
            _gl[:] = [GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN, GATE_UNKNOWN]
            connect_ms = (time.monotonic() - connect_start) * 1000
            self._usb_stats['connects'] += 1
            self._usb_log.info('CONNECT success idx=%d serial=%s time=%.1fms', idx, serial_path, connect_ms)
            logging.info('[multiACE] Connected to ACE %d (%s)' % (idx, serial_path))
            use_threads = (protocol.NAME == 'v2')
            if use_threads:

                self._cb_locks[idx] = threading.Lock()
                self._writer_queues[idx] = queue.Queue()
                self._thread_stop_flags[idx] = threading.Event()
                rt = threading.Thread(
                    target=self._make_v2_reader_thread_for(idx, ser, protocol),
                    daemon=True, name='ace%d-reader' % idx)
                wt = threading.Thread(
                    target=self._make_v2_writer_thread_for(idx, ser, protocol),
                    daemon=True, name='ace%d-writer' % idx)
                rt.start()
                wt.start()
                self._reader_threads[idx] = rt
                self._writer_threads[idx] = wt
                self._usb_log.info(
                    'CONNECT idx=%d V2 reader+writer threads started', idx)
            else:
                fd = self.reactor.register_fd(
                    ser.fileno(), self._make_reader_cb_for(idx))
                self._ace_dev_fds[idx] = fd
            ht = self.reactor.register_timer(
                self._make_heartbeat_tick_for(idx), self.reactor.NOW)
            self._heartbeat_timers[idx] = ht
            if protocol.NAME == 'v2':
                vt = self.reactor.register_timer(
                    self._make_v2_velocity_tick_for(idx), self.reactor.NOW)
                self._v2_velocity_timers[idx] = vt

                _fc_check = self._v2_feed_check_check_length
                _fc_error = self._v2_feed_check_error_length
                def _fc_cb(self, response, _ch=_fc_check, _er=_fc_error):
                    code = response.get('code', -1) if response else -1
                    msg = response.get('msg', '?') if response else 'no-response'
                    self._fa_log.info(
                        '[v2-init] ace=%d SET_FEED_CHECK %d/%d -> code=%d msg=%s'
                        % (idx, _ch, _er, code, msg))
                try:
                    self.send_request_to(idx, {
                        'method': 'set_feed_check',
                        'params': {'check_length': _fc_check,
                                   'error_length': _fc_error},
                    }, _fc_cb)
                except Exception as e:
                    self._fa_log.info(
                        '[v2-init] ace=%d SET_FEED_CHECK enqueue failed: %s'
                        % (idx, e))
            handshake_requests = protocol.initial_handshake_requests() or []

            ready_state = {'fired': False}
            def _fire_ready():
                if ready_state['fired'] or on_ready is None:
                    return
                ready_state['fired'] = True
                try:
                    on_ready()
                except Exception as e:
                    logging.info('[multiACE] _open_ace on_ready failed: %s' % e)
            last_i = len(handshake_requests) - 1
            for i, req in enumerate(handshake_requests):
                method = req.get('method', '')
                def cb(self, response, _m=method, _last=(i == last_i)):
                    if _m == 'get_info':
                        info_callback(self, response)
                    if _last:
                        _fire_ready()
                self.send_request_to(idx, request=dict(req), callback=cb)
            if on_ready is not None:
                def _ready_timeout(eventtime):
                    _fire_ready()
                    return self.reactor.NEVER
                try:
                    self.reactor.register_timer(
                        _ready_timeout, self.reactor.monotonic() + 2.5)
                except Exception:
                    _fire_ready()
            return True
        except serial.serialutil.SerialException:
            self._usb_stats['connect_failures'] += 1
            self._usb_log.warning('CONNECT failed idx=%d SerialException', idx)
            logging.info('[multiACE] Conn error idx=%d' % idx)
            return False
        except Exception as e:
            self._usb_stats['connect_failures'] += 1
            self._usb_log.warning('CONNECT failed idx=%d error=%s', idx, str(e))
            logging.info("ACE Error idx=%d: %s" % (idx, str(e)))
            return False

    def _disconnect_from(self, idx):
        self._usb_stats['disconnects'] += 1

        stop = self._thread_stop_flags.pop(idx, None)
        if stop is not None:
            stop.set()
        ser = self._serials.get(idx)
        if ser is not None:
            self._usb_log.info('DISCONNECT idx=%d serial=%s', idx,
                               self._ace_devices[idx] if idx < len(self._ace_devices) else '?')
            try:
                if ser.is_open:
                    ser.close()
            except Exception:
                pass
        for thread_dict in (self._reader_threads, self._writer_threads):
            t = thread_dict.pop(idx, None)
            if t is not None:
                try:
                    t.join(timeout=0.5)
                except Exception:
                    pass
        self._writer_queues.pop(idx, None)
        self._cb_locks.pop(idx, None)
        self._v2_filament_info_per_ace.pop(idx, None)
        self._v2_filament_info_pending.pop(idx, None)
        self._v2_filament_info_empty.pop(idx, None)
        self._connected_per_ace[idx] = False
        ht = self._heartbeat_timers.pop(idx, None)
        if ht is not None:
            try:
                self.reactor.unregister_timer(ht)
            except Exception:
                pass
        vt = self._v2_velocity_timers.pop(idx, None)
        if vt is not None:
            try:
                self.reactor.unregister_timer(vt)
            except Exception:
                pass
        self._v2_velocity_state.pop(idx, None)
        fd = self._ace_dev_fds.pop(idx, None)
        if fd is not None:
            try:
                self.reactor.unregister_fd(fd)
            except Exception:
                pass
        self._serials.pop(idx, None)

    def _make_reader_cb_for(self, idx):
        def _reader(eventtime):
            if self._serial_failed_per_ace.get(idx, False):
                return
            ser = self._serials.get(idx)
            if ser is None or not ser.is_open:
                return
            try:
                if ser.in_waiting:
                    raw_bytes = ser.read(size=ser.in_waiting)
                    self._process_data_for(idx, raw_bytes)
            except Exception:
                logging.info('ACE[%d] error reading/processing: %s' % (
                    idx, traceback.format_exc()))
                logging.info("Unable to communicate with ACE %d" % idx)
                fd = self._ace_dev_fds.pop(idx, None)
                if fd is not None:
                    try:
                        self.reactor.unregister_fd(fd)
                    except Exception:
                        pass
                if not self._serial_failed_per_ace.get(idx, False):
                    self._serial_failed_per_ace[idx] = True
                    try:
                        self.reactor.register_async_callback(
                            lambda et, i=idx: self._reconnect_or_pause(
                                i, 'v1 reader comms lost'))
                    except Exception as re:
                        logging.info(
                            '[multiACE] V1 reader reconnect schedule failed '
                            'ACE %d: %s' % (idx, str(re)))
                        self._handle_per_ace_failure(idx, 'v1 reader comms lost')
        return _reader

    def _make_v2_writer_thread_for(self, idx, ser, protocol):

        q = self._writer_queues[idx]
        stop = self._thread_stop_flags[idx]
        def _loop():
            while not stop.is_set():
                try:
                    request = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if stop.is_set():
                    break
                try:
                    if 'id' not in request:
                        request['id'] = self._next_request_id_for(idx)
                    data = protocol.encode_request(
                        request,
                        next_id=lambda: self._next_request_id_for(idx))
                    ser.write(data)
                except Exception as e:
                    if stop.is_set():
                        break
                    logging.info('[multiACE] V2 writer ACE %d error: %s' % (
                        idx, e))
                    if not self._serial_failed_per_ace.get(idx, False):
                        self._serial_failed_per_ace[idx] = True
                        try:
                            self.reactor.register_async_callback(
                                lambda et, i=idx, er=str(e):
                                    self._reconnect_or_pause(i, er))
                        except Exception as re:
                            logging.info(
                                '[multiACE] V2 writer reconnect schedule failed '
                                'ACE %d: %s' % (idx, str(re)))
                    break
        return _loop

    def _make_v2_reader_thread_for(self, idx, ser, protocol):

        stop = self._thread_stop_flags[idx]
        buf = bytearray()
        def _loop():
            while not stop.is_set():
                try:
                    chunk = ser.read(256)
                except Exception as e:
                    if stop.is_set():
                        break
                    logging.info('[multiACE] V2 reader ACE %d error: %s' % (idx, e))
                    if not self._serial_failed_per_ace.get(idx, False):
                        self._serial_failed_per_ace[idx] = True
                        try:
                            self.reactor.register_async_callback(
                                lambda et, i=idx, er=str(e):
                                    self._reconnect_or_pause(i, er))
                        except Exception as re:
                            logging.info(
                                '[multiACE] V2 reader reconnect schedule failed '
                                'ACE %d: %s' % (idx, str(re)))
                    break
                if stop.is_set():
                    break
                if not chunk:
                    continue
                buf.extend(chunk)
                try:
                    frames = protocol.decode_frames(buf)
                except Exception as e:
                    logging.info('[multiACE] V2 decode error ACE %d: %s' % (
                        idx, e))
                    continue
                for ret in frames:
                    msg_id = ret.get('id')
                    cb = None
                    lock = self._cb_locks.get(idx)
                    if lock is not None:
                        with lock:
                            cb_map = self._callback_maps.get(idx, {})
                            cb = cb_map.pop(msg_id, None)
                    if cb is not None:
                        try:
                            self.reactor.register_async_callback(
                                lambda et, c=cb, r=ret: c(self=self, response=r))
                        except Exception as e:
                            logging.info(
                                '[multiACE] V2 async dispatch failed ACE %d: %s'
                                % (idx, e))
        return _loop

    def _process_data_for(self, idx, raw_bytes):
        buf = self._read_buffers.get(idx)
        if buf is None:
            buf = bytearray()
            self._read_buffers[idx] = buf
        buf += raw_bytes
        protocol = self._protocols.get(idx)
        if protocol is None:
            return
        for ret in protocol.decode_frames(buf):
            msg_id = ret.get('id')
            cb_map = self._callback_maps.get(idx, {})
            if msg_id in cb_map:
                callback = cb_map.pop(msg_id)
                callback(self=self, response=ret)

    def send_request_to(self, idx, request, callback):
        info = self._info_per_ace.get(idx)
        if info is None:
            info = self._make_default_info(idx)
            self._info_per_ace[idx] = info
        if request.get('method') not in (
                'get_status', 'get_feed_info', 'get_filament_info'):
            info['status'] = 'busy'
        msg_id = self._next_request_id_for(idx)
        cb_map = self._callback_maps.setdefault(idx, {})

        method = request.get('method', '?')
        params = request.get('params', {}) or {}
        slot_repr = params.get('index', params.get('slot', '?'))
        len_repr = params.get('length', '?')
        speed_repr = params.get('speed', '?')

        trace_request = method not in ('get_status', 'get_filament_info',
                                       'get_feed_info')
        if trace_request:
            self._fa_log.info(
                'SEND ACE %d id=%d method=%s slot=%s len=%s speed=%s'
                % (idx, msg_id, method, slot_repr, len_repr, speed_repr))
        if method == 'start_feed_assist':
            try:
                self._fa_intent_ts[(idx, int(slot_repr))] = self.reactor.monotonic()
            except (TypeError, ValueError):
                pass
        original_cb = callback
        def _traced_cb(self, response):
            if trace_request:
                try:
                    self._fa_log.info(
                        'RESP ACE %d id=%s method=%s slot=%s code=%s msg=%s' % (
                            idx, response.get('id', '?'), method, slot_repr,
                            response.get('code', '?'), response.get('msg', '')))
                except Exception:
                    pass
            original_cb(self=self, response=response)

        request['id'] = msg_id
        protocol = self._protocols.get(idx)
        if protocol is not None and protocol.NAME == 'v2':

            lock = self._cb_locks.get(idx)
            if lock is not None:
                with lock:
                    cb_map[msg_id] = _traced_cb
            else:
                cb_map[msg_id] = _traced_cb
            wq = self._writer_queues.get(idx)
            if wq is not None:
                try:
                    wq.put_nowait(request)
                except Exception as e:
                    logging.error('[multiACE] V2 writer queue put failed for ACE %d: %s' % (idx, e))
            else:
                logging.error('[multiACE] V2 writer queue missing for ACE %d' % idx)
            return
        cb_map[msg_id] = _traced_cb
        self._send_request_to(idx, request)

    def _send_request_to(self, idx, request):
        if 'id' not in request:
            request['id'] = self._next_request_id_for(idx)
        protocol = self._protocols.get(idx)
        if protocol is None:
            raise Exception('[multiACE] no protocol bound for ACE %d' % idx)
        try:
            data = protocol.encode_request(
                request, next_id=lambda: self._next_request_id_for(idx))
        except ValueError as e:
            logging.error("ACE[%d]: %s" % (idx, str(e)))
            return
        ser = self._serials.get(idx)
        if ser is None or self._serial_failed_per_ace.get(idx, False):
            raise Exception('[multiACE] serial[%d] unavailable' % idx)
        try:
            _sw_t0 = time.monotonic() if self.stall_watchdog else None
            ser.write(data)
            if _sw_t0 is not None:
                _sw_dt = time.monotonic() - _sw_t0
                if _sw_dt > STALL_SRC_THRESHOLD:
                    logging.warning(
                        '[multiACE][stall-src] sync ser.write ACE %d method=%s '
                        'blocked %.0fms (reactor-thread write backpressure)'
                        % (idx, request.get('method', '?'), _sw_dt * 1000.))
            return
        except Exception as e:
            err_first = str(e)
            logging.info(
                "ACE[%d]: Error writing to serial: %s - attempting reconnect+retry"
                % (idx, err_first))
            self._usb_stats['errno5_total'] += 1

            now = time.monotonic()
            self._errno5_recent = [
                (i, t) for (i, t) in self._errno5_recent if now - t < 1.5]
            self._errno5_recent.append((idx, now))
            distinct_aces = set(i for (i, _) in self._errno5_recent)
            if len(distinct_aces) >= 2:
                self._usb_stats['cascades'] += 1

                logging.info(
                    '[multiACE] %s',
                    self._t('msg.cascade_detected',
                        count=len(distinct_aces),
                        total=self._usb_stats['cascades']))
                self._errno5_recent = []
            try:
                self._state_log.warning(
                    'SERIAL_WRITE_FAILED_FIRST idx=%d error=%s', idx, err_first)
            except Exception:
                pass

            saved_cb_map = dict(self._callback_maps.get(idx, {}))

            try:
                if ser.is_open:
                    ser.close()
            except Exception:
                pass
            self._connected_per_ace[idx] = False

            self._serial_failed_per_ace[idx] = True
            self._reconnect_or_pause(idx, err_first)

            if not self._serial_failed_per_ace.get(idx, False):
                new_cb_map = self._callback_maps.setdefault(idx, {})
                for mid, cb in saved_cb_map.items():
                    if mid not in new_cb_map:
                        new_cb_map[mid] = cb
                new_ser = self._serials.get(idx)
                if new_ser is not None:
                    try:
                        new_ser.write(data)
                        self._usb_stats['errno5_recovered'] += 1
                        self.log_always(self._t('msg.serial_write_recovered',
                            ace=self._disp(idx)))
                        try:
                            self._state_log.info(
                                'SERIAL_WRITE_RECOVERED idx=%d', idx)
                            self._audit_state(
                                'SERIAL_WRITE_RECOVERED', {'idx': idx})
                        except Exception:
                            pass
                        return
                    except Exception as e2:
                        err_second = str(e2)
                else:
                    err_second = 'no_serial_after_reconnect'
            else:
                err_second = 'reconnect_failed'

            self._usb_stats['errno5_unrecovered'] += 1
            try:
                self._state_log.warning(
                    'SERIAL_WRITE_FAILED idx=%d error=%s first_error=%s',
                    idx, err_second, err_first)
            except Exception:
                pass
            raise Exception(
                '[multiACE] serial[%d] write failed (reconnect+retry both failed)'
                % idx)

    def _maybe_pause_fa_exhausted(self, idx, slot, attempts):
        """Last-resort resumable PAUSE when the FA arm retry budget is
        exhausted (fa_failed_final) for the slot feeding the ACTIVE printing
        head on a V2. Same policy as the comms-loss give-up pause (§10): the
        ACE 2 cannot freewheel, so a lane whose arm terminally failed prints
        air until the next arm trigger (tester field case 2026-07-19: 6/6
        FORBIDDEN on two slots, lanes declared dead mid-print). V1 lanes stay
        alert-only (the extruder pulls through a freewheeling V1). Mid-swap
        the inline feed machinery owns failures (phase3 flow check + the §12
        pause with pos-restore) - do not pause from here without that
        restore. RESUME lands in _on_print_start, which re-arms the active
        lane (§8) - the pause IS the long-horizon retry."""
        if not self._is_v2_idx(idx):
            return
        if self._fa_failed_pause_sent:
            return
        if self._fa_context != 'print':
            return
        if self._swap_in_progress:
            return
        try:
            extruder = self.toolhead.get_extruder()
            head = getattr(extruder, 'extruder_index',
                           getattr(extruder, 'extruder_num', None))
        except Exception:
            head = None
        if head is None:
            return
        source = self._head_source.get(head)
        if source is None:
            return
        if int(source.get('ace_index', -1)) != idx:
            return
        if int(source.get('slot', -1)) != slot:
            return
        self._fa_failed_pause_sent = True
        detail = self._t('msg.fa_failed_pause',
                         ace=self._disp(idx), slot=self._disp(slot),
                         attempts=attempts, head=self._disp(head))
        def _do_pause(eventtime):
            try:
                self.gcode.run_script(
                    'RESPOND TYPE=error MSG="%s"' % detail.replace('"', "'"))
            except Exception:
                pass
            try:
                em = self.printer.lookup_object('exception_manager', None)
                if em is not None:
                    em.raise_exception_async(
                        id=em.list.MODULE_ID_FEEDING, index=head, code=0,
                        message=detail, oneshot=1, level=2)
            except Exception:
                pass
            try:
                self.gcode.run_script('PAUSE')
            except Exception as pe:
                logging.info('[multiACE] FA-exhausted PAUSE failed: %s'
                             % str(pe))
            return self.reactor.NEVER
        try:
            self.reactor.register_timer(_do_pause, self.reactor.NOW)
        except Exception:
            pass

    def _handle_per_ace_failure(self, idx, err):
        was_failed = self._serial_failed_per_ace.get(idx, False)
        self._serial_failed_per_ace[idx] = True
        if not was_failed:
            self.log_error(self._t('msg.ace_serial_failed',
                ace=self._disp(idx), error=err))
            try:
                self._state_log.error('ACE_FAILED idx=%d error=%s', idx, err)
                self._audit_state('ACE_FAILED', {'idx': idx, 'error': err})
            except Exception:
                pass
            try:
                self._disconnect_from(idx)
            except Exception:
                pass
        if not self._serial_failed_pause_sent:
            self._serial_failed_pause_sent = True
            def _do_pause(eventtime):
                try:
                    active = self.toolhead.get_extruder().get_name() \
                        if self.toolhead else 'extruder'
                    head = 0 if active == 'extruder' \
                        else int(active.replace('extruder', '') or 0)
                except Exception:
                    head = 0
                detail = self._t('msg.pause_ace_comms_lost',
                                 ace=self._disp(idx), head=self._disp(head))
                try:
                    self.gcode.run_script(
                        'RESPOND TYPE=error MSG="%s"' % detail.replace('"', "'"))
                except Exception:
                    pass
                try:
                    em = self.printer.lookup_object('exception_manager', None)
                    if em is not None:
                        em.raise_exception_async(
                            id=em.list.MODULE_ID_FEEDING, index=head, code=0,
                            message=detail, oneshot=1, level=2)
                except Exception:
                    pass
                try:
                    sp = getattr(self, '_swap_saved_pos', None)
                    if self._swap_in_progress and sp:
                        lines = []
                        if getattr(self, '_swap_switched_head', False):
                            on = getattr(self, '_swap_orig_ext_name', None)
                            if on:
                                oh = (0 if on == 'extruder'
                                      else int(on.replace('extruder', '') or 0))
                                lines.append('T%d A0' % oh)
                        lines += [
                            'G90',
                            'G0 Z%.3f F600' % (sp[2] + 3.0),
                            'G0 Y%.3f F12000' % sp[1],
                            'G0 X%.3f F12000' % sp[0],
                            'G0 Z%.3f F600' % (sp[2] + 2.0),
                            'M400',
                        ]
                        self.gcode.run_script('\n'.join(lines))
                        logging.info(
                            '[multiACE] comms-loss PAUSE: restored print pos '
                            'X=%.2f Y=%.2f Z=%.2f before pause (was mid-swap)'
                            % (sp[0], sp[1], sp[2]))
                except Exception as pe:
                    logging.info('[multiACE] comms-loss PAUSE: mid-swap pos '
                                 'restore failed: %s' % pe)
                try:
                    self.gcode.run_script('PAUSE')
                except Exception as pe:
                    logging.info('[multiACE] PAUSE call failed: %s' % str(pe))
                return self.reactor.NEVER
            try:
                self.reactor.register_timer(_do_pause, self.reactor.NOW)
            except Exception:
                pass

    def _reconnect_or_pause(self, idx, err):
        """Protocol-agnostic recovery-first handler for a comms loss ([Errno 5]).
        The ONE recovery path for BOTH V1 and V2 (parity - Dirk: "V1 freewheel is
        not an excuse to keep printing", it caused airprint). MUST run on the
        reactor thread: the V2 threads marshal it via register_async_callback,
        the V1 reader (on the reactor thread) schedules it the same way, and the
        V1 sync-write path calls it inline. _open_ace registers timers/threads/
        fds and is not thread-safe, so it must only run on the reactor.

        Guarded by _reconnecting_per_ace so read/write failures on the same ACE
        never run _open_ace concurrently. Backoff 0.3/0.8/1.6 s; on success the
        print CONTINUES (FA re-armed for the active head via on_ready), only the
        brief gap is lost. PAUSE (resumable) is the last resort when the device
        can't be reopened (e.g. gone until a power-cycle)."""
        if self._reconnecting_per_ace.get(idx, False):
            return
        self._reconnecting_per_ace[idx] = True
        try:
            logging.info('[multiACE] ACE %d comms lost (%s) - reconnecting'
                         % (idx, err))
            try:
                self._state_log.warning('COMMS_LOST idx=%d error=%s', idx, err)
            except Exception:
                pass
            reconnected = False
            for attempt, delay in enumerate((0.3, 0.8, 1.6), start=1):
                try:
                    self.reactor.pause(self.reactor.monotonic() + delay)
                except Exception:
                    pass
                try:
                    reconnected = self._open_ace(
                        idx, on_ready=lambda i=idx: self._rearm_fa_after_reconnect(i))
                except Exception as ce:
                    logging.info('[multiACE] reconnect[%d] attempt %d raised: %s'
                                 % (idx, attempt, str(ce)))
                    reconnected = False
                if reconnected:
                    break
                logging.info('[multiACE] reconnect[%d] attempt %d/3 failed'
                             % (idx, attempt))
            if reconnected:
                self._serial_failed_per_ace[idx] = False
                self._usb_stats['errno5_recovered'] += 1
                self.log_always(self._t('msg.serial_write_recovered',
                                        ace=self._disp(idx)))
                try:
                    self._audit_state('RECONNECTED', {'idx': idx})
                except Exception:
                    pass
            else:
                self._usb_stats['errno5_unrecovered'] += 1
                self._handle_per_ace_failure(idx, err)
        finally:
            self._reconnecting_per_ace[idx] = False

    def _rearm_fa_after_reconnect(self, idx):
        """After a successful reconnect of ACE idx, resume feed-assist for the
        currently-active head IF it is sourced from this ACE and we were
        feeding (printing). Without this the extruder keeps moving but the V2
        (which can't freewheel) wouldn't feed until the next FA dispatch."""
        if not self._auto_feed_enabled:
            return
        try:
            extruder = self.toolhead.get_extruder()
            head_index = getattr(extruder, 'extruder_index',
                                 getattr(extruder, 'extruder_num', None))
        except Exception:
            head_index = None
        if head_index is None:
            return
        source = self._head_source.get(head_index)
        if source is None:
            return
        if int(source.get('ace_index', -1)) != idx:
            return
        self._feed_assist_per_ace[idx] = -1
        self._fa_rearm_reset(idx)
        try:
            self._arm_fa_for(idx, source['slot'])
            self.log_always(
                '[multiACE] FA re-armed after ACE %s reconnect (head %d slot %s)'
                % (self._disp(idx), head_index, self._disp(source['slot'])))
        except Exception as e:
            logging.info('[multiACE] FA re-arm after reconnect failed: %s' % e)

    def _reopen_failed_aces_on_resume(self):
        """A comms-loss give-up PAUSE leaves the failed ACE dead: the recovery
        exhausted its retries and never reopened it, so its reader/writer
        threads are gone while _connected_per_ace may still read stale-True.
        When the user fixes the cable and resumes, reopen any ACE still flagged
        failed so its threads restart, and clear the stale FA slot so the
        subsequent _arm_fa_for actually re-sends start_feed_assist (the V2
        keep-armed slot would otherwise make _arm_fa_for skip -> no feed ->
        airprint after continue; Dirk build 2f9c428)."""
        for idx in list(self._serial_failed_per_ace.keys()):
            if not self._serial_failed_per_ace.get(idx, False):
                continue
            try:
                ok = self._open_ace(
                    idx, on_ready=lambda i=idx: self._rearm_fa_after_reconnect(i))
            except Exception as e:
                ok = False
                logging.info('[multiACE] resume reopen ACE %d raised: %s'
                             % (idx, str(e)))
            if ok:
                self.log_always(
                    '[multiACE] resume: reopened ACE %s (was failed) - FA will '
                    're-arm' % self._disp(idx))
            else:
                self.log_error(
                    '[multiACE] resume: ACE %s still unreachable - feed will '
                    'not resume for its heads' % self._disp(idx))

    def _on_homing_move_begin(self, hmove):
        self._homing_active = True
        self._touch_homing_flag()

    def _on_homing_move_end(self, hmove):
        self._homing_active = False
        self._last_homing_end = self.reactor.monotonic()
        self._touch_homing_flag()

    def _v1_fa_blocked_by_homing(self, idx):
        """True when a V1 FA dispatch must wait: this ACE is V1 and a
        homing/probe move is active or ended less than FA_HOMING_SETTLE
        ago. V2 is never blocked (its writes don't run on the reactor
        thread)."""
        proto = self._protocols.get(idx)
        if proto is None or getattr(proto, 'NAME', None) != 'v1':
            return False
        if self._homing_active:
            return True
        return (self.reactor.monotonic() - self._last_homing_end) < FA_HOMING_SETTLE

    def _wait_homing_clear(self, timeout=60.0):
        """Defer an ad-hoc device command (e.g. the dryer, triggered from the
        web mid print-start) until any homing/probe window clears, so its
        synchronous V1 serial write can't stall the probe's trsync - which
        otherwise surfaces as 0003-0528 'Communication timeout during homing'.
        Yields via reactor.pause so homing keeps running; bounded by timeout so
        it can never hang. No-op when no homing is active/recent. Do NOT use
        this for commands that themselves home (LOAD/UNLOAD/SWAP) - they would
        wait on their own homing."""
        deadline = time.monotonic() + timeout
        waited = False
        while self._homing_active or \
                (self.reactor.monotonic() - self._last_homing_end) < FA_HOMING_SETTLE:
            if time.monotonic() > deadline:
                self.log_error(
                    '[multiACE] homing-clear wait timed out (%.0fs) - proceeding'
                    % timeout)
                break
            waited = True
            self.reactor.pause(self.reactor.monotonic() + 0.1)
        if waited:
            self._fa_trace('command deferred until homing/probe finished')

    def _arm_fa_for(self, idx, slot, from_recovery=False):
        self._fa_trace('_arm_fa_for(idx=%d, slot=%d) called; gate=%s context=%s'
                       % (idx, slot, self._auto_feed_enabled, self._fa_context))
        if not from_recovery:
            self._fa_rearm_reset(idx, slot)

        if getattr(self, '_v2_active_rev_assist', False):
            self._v2_active_rev_assist = False
            self._fa_trace('_v2_active_rev_assist cleared by _arm_fa_for')

        if not self._auto_feed_enabled:
            logging.info(
                '[multiACE] FA suppressed (gate off): idx=%d slot=%d' % (idx, slot))
            return

        if self._fa_context == 'print' and idx in self._fa_print_disable:
            logging.info(
                '[multiACE] FA suppressed for ACE %d during print (fa_print_disable)' % idx)
            return
        if self._fa_context == 'load' and idx in self._fa_load_disable:
            logging.info(
                '[multiACE] FA suppressed for ACE %d during load (fa_load_disable)' % idx)
            return
        _fa_head = (self._head_for_ace(idx)
                    if getattr(self, '_ace_mode', 'multi') == 'head' else slot)
        if _fa_head is not None and self.head_is_manual(_fa_head):
            self._fa_trace(
                'FA suppressed: head %d is manual (TPU bypass)' % _fa_head)
            return

        prev_slot = self._feed_assist_per_ace.get(idx, -1)
        if prev_slot == slot:
            if self._is_v2_idx(idx):
                slot_status = self._v2_get_slot_status(idx, slot)
                if slot_status in V2_FA_RUNNING_STATES:
                    logging.info(
                        '[multiACE] FA _start skipped: prev_slot=%d == slot=%d '
                        '(already running, status=%s)' % (
                            prev_slot, slot, slot_status))
                    return
                self._fa_log.info(
                    '[v2-recover] stale FA cache, rearming ACE %d slot %d '
                    'status=%s' % (
                        idx, slot,
                        slot_status if slot_status is not None else 'unknown'))
                self._clear_fa_cache_for(idx, slot)
                prev_slot = -1
            else:
                logging.info(
                    '[multiACE] FA _start skipped: prev_slot=%d == slot=%d '
                    '(already running)' % (prev_slot, slot))
                return
        logging.info('[multiACE] FA _start proceeding: idx=%d slot=%d prev_slot=%d' % (idx, slot, prev_slot))

        any_active_before = any(
            s != -1 for s in self._feed_assist_per_ace.values())
        now = time.monotonic()
        if not any_active_before and self._fa_context == 'print':
            gap_ms = int((now - self._fa_last_active_ts) * 1000)
            if gap_ms > self._fa_gap_threshold_ms:
                self._telemetry('FA_GAP', {
                    'gap_ms': gap_ms,
                    'resumed_ace': idx,
                    'resumed_slot': slot,
                    'context': self._fa_context,
                })
        self._fa_last_active_ts = now

        self._feed_assist_per_ace[idx] = slot
        if idx == self._active_device_index:
            self._feed_assist_index = slot
        _vst = self._v2_velocity_state.get(idx)
        if _vst is not None:
            _vst['last_arm_time'] = self.reactor.monotonic()

        max_retries = self._fa_start_retries
        retry_delay = self._fa_start_retry_delay
        settle_delay = self._fa_settle_after_stop

        def start_callback_factory(attempt):
            def start_callback(self, response):
                code = response.get('code', 0)
                msg = (response.get('msg', '') or '').lower()

                if not self._auto_feed_enabled:
                    return
                if self._feed_assist_per_ace.get(idx, -1) != slot:
                    return
                if code == 0 and (msg == 'success' or msg == ''):
                    if attempt > 0:

                        self._fa_log.warning(
                            'start_feed_assist OK after %d retry(s): ACE %d slot %d'
                            % (attempt, idx, slot))
                    return
                if msg == 'error_2':
                    vstate = self._v2_velocity_state.get(idx)
                    snap = (vstate or {}).get('last_slot_statuses', {})
                    if snap.get(slot) == 'assisting':
                        self._fa_log.info(
                            'start_feed_assist error_2 ignored - ACE %d slot %d already assisting'
                            % (idx, slot))
                        return
                if msg in ('forbidden', 'error_2') and attempt < max_retries:
                    next_attempt = attempt + 1

                    self._fa_log.info(
                        'start_feed_assist %s, retry %d/%d in %.1fs: ACE %d slot %d'
                        % (msg.upper(), next_attempt, max_retries,
                           retry_delay, idx, slot))
                    def _retry(eventtime):

                        if not self._auto_feed_enabled:
                            return self.reactor.NEVER
                        if self._feed_assist_per_ace.get(idx, -1) != slot:
                            return self.reactor.NEVER
                        try:
                            self.send_request_to(idx,
                                {"method": "start_feed_assist", "params": {"index": slot}},
                                start_callback_factory(next_attempt))
                            vstate = self._v2_velocity_state.get(idx)
                            if vstate is not None:
                                vstate['last_arm_time'] = self.reactor.monotonic()
                            self._fa_log.info(
                                'start_feed_assist RETRY %d/%d sent: ACE %d slot %d'
                                % (next_attempt, max_retries, idx, slot))
                        except Exception as e:
                            self.log_error(self._t('msg.fa_retry_send_failed',
                                error=e))
                            self._fa_log.error(
                                'start_feed_assist RETRY send failed: %s' % e)
                        return self.reactor.NEVER
                    self.reactor.register_timer(
                        _retry, self.reactor.monotonic() + retry_delay)
                    return

                if self._feed_assist_per_ace.get(idx, -1) == slot:
                    self._feed_assist_per_ace[idx] = -1
                if (idx == self._active_device_index
                        and self._feed_assist_index == slot):
                    self._feed_assist_index = -1
                final_msg = self._t('msg.fa_failed_final',
                    attempts=attempt + 1, ace=self._disp(idx),
                    slot=self._disp(slot), code=code,
                    msg=response.get('msg', ''))
                self.log_error(final_msg)
                self._fa_log.error(final_msg)
                self._maybe_pause_fa_exhausted(idx, slot, attempt + 1)
            return start_callback

        def _send_start():
            if self._v1_fa_blocked_by_homing(idx):
                self._fa_trace(
                    'FA start deferred (homing active/recent): ACE %d slot %d'
                    % (idx, slot))
                def _retry_after_homing(eventtime):
                    if not self._auto_feed_enabled:
                        return self.reactor.NEVER
                    if self._feed_assist_per_ace.get(idx, -1) != slot:
                        return self.reactor.NEVER
                    _send_start()
                    return self.reactor.NEVER
                self.reactor.register_timer(
                    _retry_after_homing,
                    self.reactor.monotonic() + FA_HOMING_SETTLE)
                return
            try:
                self.send_request_to(idx,
                    {"method": "start_feed_assist", "params": {"index": slot}},
                    start_callback_factory(0))

                vstate = self._v2_velocity_state.get(idx)
                if vstate is not None:
                    vstate['last_arm_time'] = self.reactor.monotonic()
                logging.info('[multiACE] FA start_feed_assist SENT to ACE %d slot %d' % (idx, slot))
            except Exception as e:
                logging.info('[multiACE] send start_feed_assist to ACE %d failed: %s' % (idx, e))

        if prev_slot != -1:
            try:
                self.send_request_to(idx,
                    {"method": "stop_feed_assist", "params": {"index": prev_slot}},
                    lambda *a, **kw: None)
                logging.info('[multiACE] FA pre-start stop sent: ACE %d slot %d (before start slot %d, settle %.1fs)'
                             % (idx, prev_slot, slot, settle_delay))
            except Exception as e:
                logging.info('[multiACE] pre-start stop_feed_assist failed: %s' % e)

            def _delayed_start(eventtime):
                if not self._auto_feed_enabled:
                    self._fa_trace(
                        'post-stop delayed start SUPPRESSED (gate closed): idx=%d slot=%d'
                        % (idx, slot))
                    return self.reactor.NEVER
                if self._feed_assist_per_ace.get(idx, -1) != slot:
                    self._fa_trace(
                        'post-stop delayed start SUPPRESSED (slot changed): idx=%d expected=%d actual=%d'
                        % (idx, slot, self._feed_assist_per_ace.get(idx, -1)))
                    return self.reactor.NEVER
                _send_start()
                return self.reactor.NEVER
            self.reactor.register_timer(
                _delayed_start, self.reactor.monotonic() + settle_delay)
        else:
            _send_start()

    def _disarm_fa_for(self, idx):
        prev_slot = self._feed_assist_per_ace.get(idx, -1)
        if prev_slot == -1:
            return
        self._feed_assist_per_ace[idx] = -1
        if idx == self._active_device_index:
            self._feed_assist_index = -1
        if not any(s != -1 for s in self._feed_assist_per_ace.values()):
            self._fa_last_active_ts = time.monotonic()

        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_stop_fa',
                    ace=self._disp(idx), error=response.get('msg')))

        try:
            self.send_request_to(idx,
                {"method": "stop_feed_assist", "params": {"index": prev_slot}},
                callback)
        except Exception as e:
            logging.info('[multiACE] send stop_feed_assist to ACE %d failed: %s' % (idx, e))

    def _disable_feed_assist_all(self):
        def _noop_cb(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))

        any_running = False
        for idx in sorted(list(self._feed_assist_per_ace.keys())):
            slot = self._feed_assist_per_ace.get(idx, -1)
            if slot == -1:
                continue

            if not self._connected_per_ace.get(idx, False):
                logging.info(
                    '[multiACE] _disable_feed_assist_all: skip ACE %d (disconnected)' % idx)
                self._feed_assist_per_ace[idx] = -1
                continue
            gate_list = self._gate_status_per_ace.get(idx, [GATE_UNKNOWN] * 4)
            if 0 <= slot < len(gate_list) and gate_list[slot] == GATE_EMPTY:
                logging.info(
                    '[multiACE] _disable_feed_assist_all: skip ACE %d slot %d (empty)' % (idx, slot))
                self._feed_assist_per_ace[idx] = -1
                continue

            proto = self._protocols.get(idx) if hasattr(self, '_protocols') else None
            if proto is not None and getattr(proto, 'NAME', None) == 'v2':
                logging.info(
                    '[multiACE] _disable_feed_assist_all: keep ACE %d armed (V2 - velocity tracker handles mode switch)' % idx)
                continue
            any_running = True
            try:
                self.wait_ace_ready_on(idx)
                self.send_request_to(idx,
                    {"method": "unwind_filament",
                     "params": {"index": slot, "length": 5, "speed": 80}},
                    _noop_cb)
                self.dwell(delay=(5.0 / 80.0) + 0.1)
                self.wait_ace_ready_on(idx)
                self._disarm_fa_for(idx)
                self.wait_ace_ready_on(idx)
            except Exception as e:
                logging.info(
                    '[multiACE] _disable_feed_assist_all: error on idx %d: %s' % (idx, e))
        if self._feed_assist_index != -1:
            self._feed_assist_index = -1
        if any_running:
            self.dwell(0.3)

    def _v2_arm_fa_for_unload(self, head):
        """Arm V2 feed_assist on the slot mapped to `head` so the velocity
        tracker can dispatch mode=3 (rollback assist) during the tip-form
        retract (G1 E-N moves inside INNER_FILAMENT_UNLOAD).

        Also sets _v2_active_rev_assist = True so the velocity tracker
        STARTS dispatching MODE_SWITCH on direction changes (it's gated
        on this flag - skipped during normal print to avoid error_2
        spam, enabled during unload so V2 actively rev-assists the
        ~10s tip-form retract instead of braking the filament).
        Flag is cleared the next time _arm_fa_for runs (= we're back
        in print/load context).

        Called from:
          * cmd_ACE_UNLOAD_HEAD (gcode ACE_UNLOAD_HEAD path)
          * filament_feed_ace.FEED_ACT_UNLOAD (display Unload button →
            cmd_FEED_AUTO path)
        Both call sites must arm V2 FA, because on a manual unload there
        is no print context and the FA gate (_auto_feed_enabled) is
        closed, so the regular _arm_fa_for path never runs. Without a
        prior arm the velocity tracker sees armed_slot=None and skips
        dispatch - the tip-form runs without V2-side rollback help.

        Bypasses the FA gate intentionally: V2 buffer assist is the
        safe semantic on this hardware regardless of print context.
        No-op for V1 ACEs (V1 needs FA stopped, not started, before
        unload - handled by the V1 branch in the caller).
        Returns True if FA is armed (already or now), False otherwise.
        """
        source = self._head_source.get(head)
        if source is None:
            return False
        active_idx = source.get('ace_index')
        src_slot = source.get('slot', -1)
        if active_idx is None or not (0 <= src_slot <= 3):
            return False
        proto = self._protocols.get(active_idx)
        if proto is None or getattr(proto, 'NAME', None) != 'v2':
            return False

        self._v2_active_rev_assist = True
        self._fa_trace('_v2_active_rev_assist enabled by _v2_arm_fa_for_unload')

        def _noop_cb(self, response):
            pass

        cur_fa_slot = self._feed_assist_per_ace.get(active_idx, -1)
        if cur_fa_slot == src_slot:
            self._fa_trace(
                'unload v2 FA already armed on ACE %d slot %d'
                % (active_idx, src_slot))
            return True
        try:

            if 0 <= cur_fa_slot <= 3:
                self.send_request_to(active_idx,
                    {"method": "stop_feed_assist",
                     "params": {"index": cur_fa_slot}},
                    _noop_cb)
            self.send_request_to(active_idx,
                {"method": "start_feed_assist",
                 "params": {"index": src_slot}},
                _noop_cb)
            self._feed_assist_per_ace[active_idx] = src_slot
            if active_idx == self._active_device_index:
                self._feed_assist_index = src_slot
            self._fa_trace(
                'unload v2 arm FA on ACE %d slot %d '
                '(was %d, for rollback-assist during tip-form)'
                % (active_idx, src_slot, cur_fa_slot))
            return True
        except Exception as e:
            logging.info('[multiACE] V2 unload arm FA failed: %s' % e)
            return False

    def _enable_feed_assist_for_head(self, head):
        source = self._head_source.get(head)
        if source is None:

            logging.info(
                '[multiACE] _enable_feed_assist_for_head: no head_source for head %d, '
                'skipping FA (use ACE_LOAD_HEAD to set source first)' % head)
            return

        target_idx = source['ace_index']
        slot = source['slot']

        self._disable_feed_assist_all()

        if target_idx != self._active_device_index:
            self._set_active_idx(target_idx)

        self.wait_ace_ready_on(target_idx)
        self._arm_fa_for(target_idx, slot)
        self.wait_ace_ready_on(target_idx)
        self.dwell(delay=0.7)

    _V2_FILAMENT_INFO_PENDING_TTL = 5.0
    _V2_FILAMENT_INFO_EMPTY_TTL = 60.0

    def _merge_v2_filament_info(self, idx, result):

        protocol = self._protocols.get(idx)
        if protocol is None or getattr(protocol, 'NAME', None) != 'v2':
            return
        cache = self._v2_filament_info_per_ace.setdefault(idx, {})
        pending = self._v2_filament_info_pending.setdefault(idx, {})
        empty = self._v2_filament_info_empty.setdefault(idx, {})
        now = time.monotonic()
        slots = result.get('slots') or []
        for i, slot in enumerate(slots):
            if slot.get('rfid') == 2:
                cached = cache.get(i)
                if cached:
                    slot['type'] = cached.get('type', '')
                    slot['color'] = list(cached.get('color', [0, 0, 0]))
                    slot['brand'] = cached.get('brand', '')
                    slot['sku'] = cached.get('sku', '')
                else:
                    slot['rfid'] = 1
                    empty_ts = empty.get(i)
                    if empty_ts is not None and (now - empty_ts) < self._V2_FILAMENT_INFO_EMPTY_TTL:
                        continue
                    pending_ts = pending.get(i)
                    if pending_ts is not None and (now - pending_ts) < self._V2_FILAMENT_INFO_PENDING_TTL:

                        continue
                    if pending_ts is not None:
                        self._fa_log.info(
                            '[multiACE] V2 cmd13 pending stale (%.1fs) ACE %d slot %d - re-issuing',
                            now - pending_ts, idx, i)
                    pending[i] = now
                    def _store(self, response, _idx=idx, _slot=i):
                        self._v2_filament_info_pending.get(
                            _idx, {}).pop(_slot, None)
                        if response is None:
                            self._fa_log.info(
                                '[multiACE] V2 cmd13 response NONE ACE %d slot %d',
                                _idx, _slot)
                            return
                        res = response.get('result') or {}
                        ftype = res.get('type', '')
                        self._fa_log.info(
                            '[multiACE] V2 cmd13 response ACE %d slot %d: '
                            'type=%r color=%r brand=%r sku=%r (raw=%r)',
                            _idx, _slot, ftype,
                            res.get('color'), res.get('brand'),
                            res.get('sku'), response)
                        if not ftype:
                            self._v2_filament_info_empty.setdefault(
                                _idx, {})[_slot] = time.monotonic()
                            return
                        self._v2_filament_info_empty.get(_idx, {}).pop(_slot, None)
                        self._v2_filament_info_per_ace.setdefault(_idx, {})[_slot] = {
                            'type': ftype,
                            'color': list(res.get('color', [0, 0, 0])),
                            'brand': res.get('brand', ''),
                            'sku': res.get('sku', ''),
                        }
                    try:
                        self.send_request_to(idx, {
                            'method': 'get_filament_info',
                            'params': {'index': i},
                        }, _store)
                    except Exception as e:
                        pending.pop(i, None)
                        logging.info(
                            '[multiACE] V2 get_filament_info enqueue failed '
                            'idx=%d slot=%d: %s', idx, i, e)
            else:
                cache.pop(i, None)
                pending.pop(i, None)
                empty.pop(i, None)

    def _v2_quantize_velocity(self, v_mm_s, direction='fwd'):

        v_abs = abs(v_mm_s)
        if direction == 'rev':
            STEP = 5
            return max(1, min(50, int(math.ceil(v_abs / STEP) * STEP)))
        STEP = 10
        return max(10, min(50, int(math.ceil(v_abs / STEP) * STEP)))

    def _v2_dispatch_mode_switch(self, idx, armed_slot, target_mode,
                                  disp, sustained, current_v=0.0):
        """MODE_SWITCH dispatch with pre-stop + retry-on-error_2.

        Called from the velocity tracker tick ONLY when a direction
        change happens during swap unload (when active rev-assist via
        mode=3 is actually needed). During print phase the tracker
        skips dispatch entirely - V2 stays in mode=2 and brief
        slicer retracts are absorbed by the buffer.

        Restored from 83f5ce7-style unload behavior:
        * For target_mode=3 (fwd->rev): dispatch_speed from direction-
          aware _v2_quantize_velocity (rev branch: floor=1 step=5),
          matches actual demand so V2's internal motor-stall detection
          doesn't trip during slow tip-form retracts.
        * For target_mode=2 (rev->fwd): use start_feed_assist instead
          of feed_or_rollback_raw mode=2 - start_feed_assist puts V2
          into "passive armed" state (pumps on buffer-arm signal,
          doesn't expect continuous encoder motion), so no assist_error
          trip during idle after the rev phase ends.

        Pre-stop reason: V2 FW 1.1.31 rejects in-place mode transitions
        with error_2. The slot must be in `ready` before the new mode
        dispatch is accepted.
        Retry reason: pre-stop has a ~5-30ms post-stop settling window
        in V2 FW; if the FIFO gap between SEND stop and SEND mode-set
        falls inside that window, V2 still returns error_2. Retry
        after 50ms reactor dwell.
        """
        if target_mode == 3:
            dispatch_speed = self._v2_quantize_velocity(current_v, 'rev')
        else:

            dispatch_speed = 10
        old_mode = disp['last_mode']
        disp['last_mode'] = target_mode
        disp['last_speed'] = dispatch_speed
        _trans_label = {2: 'fwd', 3: 'rev'}
        trans_str = '%s->%s' % (
            _trans_label.get(old_mode, '?'),
            _trans_label.get(target_mode, '?'))

        def _mode_cb(self, response, _q=dispatch_speed,
                     _m=target_mode, _om=old_mode,
                     _ts=trans_str, _s=armed_slot, _i=idx,
                     _retries=0):
            code = response.get('code', -1) if response else -1
            msg = response.get('msg', '?') if response else 'no-response'
            self._fa_log.info(
                '[v2-vel] ace=%d MODE_SWITCH slot=%d '
                'mode=%d->%d (%s) speed=%d -> code=%d msg=%s%s' % (
                    _i, _s, _om, _m, _ts, _q, code, msg,
                    (' retry=%d' % _retries) if _retries else ''))
            if (code == 2 and 'error_2' in (msg or '')
                    and _retries < 2):
                def _retry(eventtime, _r=_retries):
                    self._fa_log.info(
                        '[v2-vel] ace=%d slot=%d '
                        'MODE_SWITCH retry %d/2 (was error_2)'
                        % (_i, _s, _r + 1))
                    try:
                        if _m == 2:
                            self.send_request_to(_i, {
                                'method': 'start_feed_assist',
                                'params': {'index': _s, 'speed': 10},
                            }, lambda self, response, _rr=_r + 1:
                                _mode_cb(self=self, response=response,
                                         _retries=_rr))
                        else:
                            self.send_request_to(_i, {
                                'method': 'feed_or_rollback_raw',
                                'params': {
                                    'index': _s,
                                    'speed': _q,
                                    'length': 0,
                                    'mode': _m,
                                },
                            }, lambda self, response, _rr=_r + 1:
                                _mode_cb(self=self, response=response,
                                         _retries=_rr))
                    except Exception as e:
                        self._fa_log.info(
                            '[v2-vel] MODE_SWITCH retry '
                            'enqueue failed: %s' % e)
                    return self.reactor.NEVER
                try:
                    self.reactor.register_callback(
                        _retry, self.reactor.monotonic() + 0.05)
                except Exception as e:
                    self._fa_log.info(
                        '[v2-vel] MODE_SWITCH retry '
                        'schedule failed: %s' % e)

        def _pre_stop_cb(self, response, _s=armed_slot, _i=idx):
            code = response.get('code', -1) if response else -1
            msg = response.get('msg', '?') if response else 'no-response'
            self._fa_log.info(
                '[v2-vel] ace=%d MODE_SWITCH pre-stop '
                'slot=%d -> code=%d msg=%s' % (_i, _s, code, msg))

        self._fa_log.info(
            '[v2-vel] ace=%d slot=%d MODE_SWITCH -> '
            'mode=%d->%d (%s) speed=%d (sustained %.2fs) [unload]' % (
                idx, armed_slot, old_mode, target_mode,
                trans_str, dispatch_speed, sustained))
        try:
            self.send_request_to(idx, {
                'method': 'stop_feed_assist',
                'params': {'index': armed_slot},
            }, _pre_stop_cb)
        except Exception as e:
            self._fa_log.info(
                '[v2-vel] MODE_SWITCH pre-stop enqueue '
                'failed: %s' % e)
        try:
            if target_mode == 2:

                self.send_request_to(idx, {
                    'method': 'start_feed_assist',
                    'params': {'index': armed_slot, 'speed': 10},
                }, _mode_cb)
            else:
                self.send_request_to(idx, {
                    'method': 'feed_or_rollback_raw',
                    'params': {
                        'index': armed_slot,
                        'speed': dispatch_speed,
                        'length': 0,
                        'mode': target_mode,
                    },
                }, _mode_cb)
        except Exception as e:
            self._fa_log.info(
                '[v2-vel] MODE_SWITCH enqueue failed: %s' % e)

    def _make_v2_velocity_tick_for(self, idx):

        state = self._v2_velocity_state.setdefault(idx, {
            'last_quantum': None,
            'last_direction': None,
            'last_change_time': 0.0,
            'last_log_time': 0.0,
            'last_armed_slot': None,

            'last_arm_time': 0.0,
            'print_disarm_since': None,
        })

        def _tick(eventtime):
            proto = self._protocols.get(idx)
            if proto is None or getattr(proto, 'NAME', None) != 'v2':
                return self.reactor.NEVER
            info = self._info_per_ace.get(idx)
            if info is None:
                return eventtime + 0.5
            slots = info.get('slots') or []

            status_snapshot = {}
            for s in slots:
                sidx = s.get('index', -1)
                if 0 <= sidx <= 3:
                    status_snapshot[sidx] = s.get('slot_status', '?')
            last_snapshot = state.setdefault('last_slot_statuses', {})
            if last_snapshot:
                changed = []
                for sidx, ss in status_snapshot.items():
                    prev = last_snapshot.get(sidx)
                    if prev is not None and prev != ss:
                        changed.append((sidx, prev, ss))
                if changed:
                    chg_str = ' '.join(
                        'slot%d:%s->%s' % (sidx, prev, ss)
                        for sidx, prev, ss in sorted(changed))
                    snap_str = ' '.join(
                        '%d=%s' % (sidx, ss)
                        for sidx, ss in sorted(status_snapshot.items()))
                    self._fa_log.info(
                        '[v2-diag] ace=%d slot-status-change: %s | snapshot: %s'
                        % (idx, chg_str, snap_str))
                    now = self.reactor.monotonic()
                    for sidx, prev, ss in changed:
                        if prev == 'ready' and ss == 'assisting':
                            ts = self._fa_intent_ts.get((idx, sidx), 0.0)
                            age = now - ts
                            if age > 3.0:
                                self._fa_log.warning(
                                    '[v2-diag] UNSOLICITED assist on ACE %d slot %d '
                                    '(no start_feed_assist sent in last %.1fs)'
                                    % (idx, sidx, age))
            state['last_slot_statuses'] = status_snapshot

            try:
                _mr = self.printer.lookup_object('motion_report', None)
                if _mr is not None:
                    _ev = float(_mr.get_status(eventtime).get(
                        'live_extruder_velocity', 0.0) or 0.0)
                    if abs(_ev) >= 0.3:
                        state['last_extrude_active_time'] = eventtime
            except Exception:
                pass
            _extrude_idle = (
                eventtime - state.get('last_extrude_active_time', 0.0)
                > FA_EXTRUDE_IDLE_GRACE)

            target_slot = None
            active_head = None
            try:
                cur_ext = self.toolhead.get_extruder()
                active_head = getattr(cur_ext, 'extruder_index',
                                      getattr(cur_ext, 'extruder_num', None))
                if active_head is not None:
                    src = self._head_source.get(active_head)
                    if src is not None and src.get('ace_index') == idx:
                        target_slot = src.get('slot')
            except Exception:
                pass

            armed_slot = None
            armed_status = None
            if target_slot is not None:
                for s in slots:
                    if s.get('index') != target_slot:
                        continue
                    ss = s.get('slot_status')
                    if ss in ('assisting', 'rollback_assisting',
                              'feeding', 'rollback', 'preloading'):
                        armed_slot = target_slot
                        armed_status = ss
                    break

            if armed_slot is None:
                state['armed_since'] = None
                state['armed_since_slot'] = None
                _verify_to = self._fa_settle_after_stop + FA_ASSIST_VERIFY_MARGIN
                if (target_slot is not None
                        and self._feed_assist_per_ace.get(idx, -1) == target_slot
                        and self._auto_feed_enabled
                        and self._fa_context == 'print'
                        and not getattr(self, '_v2_active_rev_assist', False)
                        and not _extrude_idle
                        and self._v2_get_slot_status(idx, target_slot)
                            not in V2_FA_RUNNING_STATES
                        and (eventtime - state.get('last_arm_time', 0.0)
                             > _verify_to)
                        and self._fa_rearm_backoff_ok(idx, target_slot)):
                    self._fa_log.warning(
                        '[v2-recover] FA arm not confirmed on ACE %d slot %d '
                        '(%.1fs since arm, never -> assisting) - resending'
                        % (idx, target_slot, _verify_to))
                    self._v2_schedule_fa_rearm(
                        idx, target_slot, 'arm-dropped:no-assist')
                if state['last_armed_slot'] is not None:
                    last_idx = state['last_armed_slot']
                    new_state = 'unknown'
                    for s in slots:
                        if s.get('index') == last_idx:
                            new_state = s.get('slot_status', 'unknown')
                            break
                    if new_state not in V2_FA_RUNNING_STATES:
                        self._fa_log.info(
                            '[v2-vel] ace=%d disarmed (was slot=%s, now=%s)' % (
                                idx, last_idx, new_state))

                        if self._feed_assist_per_ace.get(idx, -1) == last_idx:
                            self._fa_log.info(
                                '[v2-recover] clearing stale FA cache ACE %d '
                                'slot %d after disarm status=%s' % (
                                    idx, last_idx, new_state))
                            self._clear_fa_cache_for(idx, last_idx)
                            if (target_slot == last_idx and self._auto_feed_enabled
                                    and self._fa_context in ('print', 'load')
                                    and not _extrude_idle
                                    and not getattr(self, '_v2_active_rev_assist', False)
                                    and self._fa_rearm_backoff_ok(idx, last_idx)):
                                self._v2_schedule_fa_rearm(
                                    idx, last_idx, 'slot-disarmed:%s' % new_state)

                    state['last_armed_slot'] = None
                    state['last_quantum'] = None
                    state['last_direction'] = None

                if (target_slot is not None
                        and active_head is not None
                        and self.head_uses_ace(active_head)
                        and self._feed_assist_per_ace.get(idx, -1) == -1
                        and self._auto_feed_enabled
                        and self._fa_context == 'print'
                        and not self._swap_in_progress
                        and not _extrude_idle
                        and not getattr(self, '_v2_active_rev_assist', False)
                        and self._v2_get_slot_status(idx, target_slot)
                            not in V2_FA_RUNNING_STATES):
                    if state.get('print_disarm_since') is None:
                        state['print_disarm_since'] = eventtime
                    elif ((eventtime - state['print_disarm_since']) > _verify_to
                            and self._fa_rearm_backoff_ok(idx, target_slot)):
                        self._fa_log.warning(
                            '[v2-recover] print-head FA disarmed without re-arm '
                            'on ACE %d slot %d (%.1fs down, host action) - '
                            're-arming' % (idx, target_slot, _verify_to))
                        self._v2_schedule_fa_rearm(
                            idx, target_slot, 'print-head-disarmed')
                        state['print_disarm_since'] = None
                else:
                    state['print_disarm_since'] = None
                return eventtime + 0.5
            state['print_disarm_since'] = None
            if state.get('armed_since_slot') != armed_slot:
                state['armed_since'] = eventtime
                state['armed_since_slot'] = armed_slot
            elif (eventtime - state.get('armed_since', eventtime)
                    > FA_STICK_CONFIRM_TIME):
                self._fa_rearm_reset(idx, armed_slot)
            if state['last_armed_slot'] != armed_slot:
                self._fa_log.info(
                    '[v2-vel] ace=%d armed slot=%d status=%s' % (
                        idx, armed_slot, armed_status))
                state['last_armed_slot'] = armed_slot

            try:
                mr = self.printer.lookup_object('motion_report', None)
                if mr is None:
                    return eventtime + 0.5
                ms = mr.get_status(eventtime)
                v = float(ms.get('live_extruder_velocity', 0.0) or 0.0)
            except Exception as e:
                self._fa_log.info(
                    '[v2-vel] ace=%d motion_report read failed: %s' % (idx, e))
                return eventtime + 0.5

            if abs(v) < 0.3:
                direction = 'fwd'
            else:
                direction = 'fwd' if v >= 0 else 'rev'
            quantum = self._v2_quantize_velocity(v, direction)

            quantum_changed = (state['last_quantum'] != quantum)
            direction_changed = (state['last_direction'] != direction
                                 and quantum > 0)
            if quantum_changed or direction_changed:
                state['last_quantum'] = quantum
                state['last_direction'] = direction
                state['last_change_time'] = eventtime
                self._fa_log.info(
                    '[v2-vel] ace=%d slot=%d %s vel=%+.2f q=%d dir=%s' % (
                        idx, armed_slot, armed_status, v, quantum, direction))

            if (self._v2_print_assist_mode == 'constant'
                    and armed_status in ('assisting', 'rollback_assisting')):
                cdisp = state.setdefault('cdispatch', {
                    'mode': 2,
                    'cand_dir': 'fwd',
                    'cand_since': eventtime,
                    'speed_pinned': False,
                })
                if (not cdisp['speed_pinned']
                        and self._v2_constant_assist_speed > 0):
                    cdisp['speed_pinned'] = True
                    spd = self._v2_constant_assist_speed
                    self._fa_log.info(
                        '[v2-vel] ace=%d slot=%d constant-assist pin speed=%d'
                        % (idx, armed_slot, spd))
                    try:
                        self.send_request_to(idx, {
                            'method': 'update_feeding_speed',
                            'params': {'index': armed_slot, 'speed': spd},
                        }, None)
                    except Exception as e:
                        self._fa_log.info(
                            '[v2-vel] constant pin enqueue failed: %s' % e)
                if direction != cdisp['cand_dir']:
                    cdisp['cand_dir'] = direction
                    cdisp['cand_since'] = eventtime
                held = eventtime - cdisp['cand_since']
                want_mode = 2 if direction == 'fwd' else 3
                if (want_mode != cdisp['mode']
                        and held >= self._v2_assist_confirm_time):
                    cdisp['mode'] = want_mode
                    if getattr(self, '_v2_active_rev_assist', False):
                        self._v2_dispatch_mode_switch(
                            idx, armed_slot, want_mode,
                            state.setdefault('dispatch', {
                                'last_speed': None, 'last_mode': 2,
                                'candidate_speed': quantum,
                                'candidate_dir': direction,
                                'candidate_since': eventtime}),
                            held, current_v=v)
                    else:
                        self._fa_log.info(
                            '[v2-vel] ace=%d slot=%d constant: dir=%s '
                            'sustained %.2fs - mode->%d (no dispatch, '
                            'not in unload)'
                            % (idx, armed_slot, direction, held, want_mode))
                return eventtime + 0.1

            HYSTERESIS_S = 0.1
            if armed_status in ('assisting', 'rollback_assisting'):
                disp = state.setdefault('dispatch', {
                    'last_speed': None,
                    'last_mode': 2,
                    'candidate_speed': quantum,
                    'candidate_dir': direction,
                    'candidate_since': eventtime,
                })
                target_mode = 2 if direction == 'fwd' else 3
                if (disp['candidate_speed'] != quantum
                        or disp['candidate_dir'] != direction):
                    disp['candidate_speed'] = quantum
                    disp['candidate_dir'] = direction
                    disp['candidate_since'] = eventtime
                sustained = eventtime - disp['candidate_since']
                if sustained >= HYSTERESIS_S:
                    speed_changed = disp['last_speed'] != quantum
                    mode_changed = disp['last_mode'] != target_mode
                    if mode_changed:

                        if getattr(self, '_v2_active_rev_assist', False):
                            self._v2_dispatch_mode_switch(
                                idx, armed_slot, target_mode,
                                disp, sustained, current_v=v)
                        else:
                            disp['last_mode'] = target_mode
                            self._fa_log.info(
                                '[v2-vel] ace=%d slot=%d direction change '
                                '(%s) - not in unload, V2 stays in '
                                'mode=%d (no dispatch)'
                                % (idx, armed_slot,
                                   'fwd' if target_mode == 2 else 'rev',
                                   disp['last_mode']))
                    elif speed_changed:

                        disp['last_speed'] = quantum

                        def _spd_cb(self, response, _q=quantum,
                                    _s=armed_slot, _i=idx):
                            code = response.get('code', -1) if response else -1
                            msg = response.get('msg', '?') if response else 'no-response'
                            if code != 0:
                                self._fa_log.info(
                                    '[v2-vel] ace=%d UPDATE_SPEED slot=%d '
                                    'speed=%d -> code=%d msg=%s' % (
                                        _i, _s, _q, code, msg))

                        self._fa_log.info(
                            '[v2-vel] ace=%d slot=%d UPDATE_SPEED -> %d '
                            '(sustained %.2fs)' % (
                                idx, armed_slot, quantum, sustained))
                        try:
                            self.send_request_to(idx, {
                                'method': 'update_feeding_speed',
                                'params': {'index': armed_slot, 'speed': quantum},
                            }, _spd_cb)
                        except Exception as e:
                            self._fa_log.info(
                                '[v2-vel] UPDATE_SPEED enqueue failed: %s' % e)
                    else:

                        disp['last_speed'] = quantum

            return eventtime + 0.1

        return _tick

    def _make_heartbeat_tick_for(self, idx):
        def _tick(eventtime):
            if self._serial_failed_per_ace.get(idx, False):
                return eventtime + 1.0
            ser = self._serials.get(idx)
            if ser is None or not ser.is_open:
                return eventtime + 1.0
            is_active = (idx == self._active_device_index)

            def callback(self, response):
                if response is None:
                    return
                result = response.get('result')
                if result is None:
                    return

                self._refresh_slot_overrides_if_changed()
                prev_info = self._info_per_ace.get(idx, self._make_default_info(idx))
                prev_slots = prev_info.get('slots', [])
                self._merge_v2_filament_info(idx, result)
                for _s in result.get('slots', []) or []:
                    if isinstance(_s, dict):
                        _bt, _st, _vn = self._split_type_subtype(_s.get('type', ''))
                        _s['type'] = _bt
                        _s['subtype'] = _st
                        if _vn and not (_s.get('brand') or ''):
                            _s['brand'] = _vn
                display_refresh_needed = False
                for i in range(4):
                    try:
                        new_slot = result['slots'][i]
                    except (KeyError, IndexError):
                        continue
                    prev_slot = prev_slots[i] if i < len(prev_slots) else {}
                    if is_active:
                        was_empty = self._is_empty_status(prev_slot.get('status'))
                        now_empty = self._is_empty_status(new_slot.get('status'))
                        if was_empty != now_empty:
                            display_refresh_needed = True
                    if (is_active
                            and self._gate_status_per_ace.get(idx, [GATE_UNKNOWN] * 4)[i] == GATE_EMPTY
                            and not self._is_empty_status(new_slot.get('status'))
                            and not self._swap_in_progress
                            and not self._is_actively_printing()):
                        self.log_always(self._t('msg.auto_feed'))
                        self.reactor.register_async_callback(
                            (lambda et, c=self._pre_load, gate=i: c(gate)))
                    elif (is_active
                            and self._gate_status_per_ace.get(idx, [GATE_UNKNOWN] * 4)[i] == GATE_EMPTY
                            and not self._is_empty_status(new_slot.get('status'))
                            and not self._swap_in_progress
                            and self._is_actively_printing()):
                        logging.info('[multiACE] slot insert on ACE %d slot %d '
                                     'during print - pre-load deferred (not '
                                     'while actively printing)' % (idx, i))
                    if (new_slot.get('rfid') == 2
                            and prev_slot.get('rfid') != 2
                            and not self._swap_in_progress):

                        target_heads = self._get_heads_for_ace_slot(idx, i)
                        if target_heads:
                            logging.info(self._t('msg.find_rfid_target_heads',
                                ace=self._disp(idx), slot=self._disp(i),
                                heads=target_heads))
                            logging.info(self._t('msg.raw_slot_dump', slot=new_slot))
                            new_type = new_slot.get('type', 'PLA')
                            new_subtype = new_slot.get('subtype', '')
                            new_color_hex = self.rgb2hex(*new_slot.get('color', (0, 0, 0)))
                            new_brand = new_slot.get('brand', 'Generic')

                            head_source_changed = False
                            for head in target_heads:
                                src = self._head_source.get(head)
                                if src is None:
                                    continue
                                if (src.get('type') != new_type
                                        or src.get('subtype', '') != new_subtype
                                        or src.get('color') != new_color_hex
                                        or src.get('brand') != new_brand):
                                    src['type'] = new_type
                                    src['subtype'] = new_subtype
                                    src['color'] = new_color_hex
                                    src['brand'] = new_brand
                                    head_source_changed = True
                            if head_source_changed:
                                try:
                                    self._save_head_source()
                                except Exception as he:
                                    logging.info(
                                        '[multiACE] head_source RFID heal save failed: %s' % he)

                            override = self._override_for(idx, i)
                            if override is not None:
                                push_type   = override.get('material') or new_type
                                push_color  = self._override_color_to_rgba(override.get('color', ''))
                                push_brand  = override.get('brand') or new_brand
                                push_subtype = override.get('subtype', '') or ''
                            else:
                                push_type   = new_type
                                push_color  = new_color_hex
                                push_brand  = new_brand
                                push_subtype = new_subtype
                            for head in target_heads:
                                if not self.head_uses_ace(head):
                                    continue
                                self._ptc_push_guarded(
                                    head, push_type, push_color, push_brand,
                                    push_subtype, 'rfid-transition')
                        else:
                            fb_head = self._display_head_for_slot(idx, i, is_active)
                            source = (self._head_source.get(fb_head)
                                      if fb_head is not None else None)
                            if fb_head is None or not self.head_uses_ace(fb_head):
                                pass
                            elif not (source and source['ace_index'] != idx):

                                override_a = self._override_for(idx, i)
                                if override_a is not None:
                                    push_type    = override_a.get('material') or new_slot.get('type', 'PLA')
                                    push_color   = self._override_color_to_rgba(override_a.get('color', ''))
                                    push_brand   = override_a.get('brand') or new_slot.get('brand', 'Generic')
                                    push_subtype = override_a.get('subtype', '') or ''
                                else:
                                    push_type    = new_slot.get('type', 'PLA')
                                    push_color   = self.rgb2hex(*new_slot.get('color', (0, 0, 0)))
                                    push_brand   = new_slot.get('brand', 'Generic')
                                    push_subtype = new_slot.get('subtype', '')
                                logging.info(self._t('msg.find_rfid_fallback',
                                    slot=self._disp(i), head=fb_head))
                                logging.info(self._t('msg.raw_slot_dump', slot=new_slot))
                                self._ptc_push_guarded(
                                    fb_head, push_type, push_color, push_brand,
                                    push_subtype, 'rfid-fallback')
                    gate_list = self._gate_status_per_ace.setdefault(
                        idx, [GATE_UNKNOWN] * 4)
                    gate_list[i] = GATE_EMPTY if self._is_empty_status(new_slot.get('status')) else GATE_AVAILABLE
                self._info_per_ace[idx] = result

                if not self._is_v2_idx(idx):
                    try:
                        self._v1_check_fa_health(idx, result)
                    except Exception as _e:
                        logging.info(
                            '[multiACE] V1 FA health check failed ACE %d: %s'
                            % (idx, _e))

                if idx == self._active_device_index:
                    self._info = result

                if (is_active and display_refresh_needed
                        and not self._swap_in_progress):
                    try:
                        self._push_rfid_info()
                    except Exception as pe:
                        logging.info(
                            '[multiACE] slot empty/present change re-push failed: %s' % pe)

                if not self._swap_in_progress:
                    try:
                        ptc = self.printer.lookup_object('print_task_config', None)
                        if ptc is not None:
                            ptc_status = ptc.get_status()
                            ptc_types = ptc_status.get('filament_type', [''] * 4)
                            ptc_vendors = ptc_status.get('filament_vendor', [''] * 4)
                            ptc_rgbas = ptc_status.get('filament_color_rgba', [''] * 4)
                            ptc_subs = ptc_status.get('filament_sub_type', [''] * 4)
                            slots_list = result.get('slots', [])
                            for slot_idx in range(min(4, len(slots_list))):
                                slot = slots_list[slot_idx]
                                override = self._override_for(idx, slot_idx)
                                has_rfid = slot.get('rfid') == 2
                                if override is None and not has_rfid:
                                    continue
                                target_heads = self._get_heads_for_ace_slot(
                                    idx, slot_idx)

                                if not target_heads:
                                    fb_head = self._display_head_for_slot(
                                        idx, slot_idx, is_active)
                                    if (fb_head is not None
                                            and self.head_uses_ace(fb_head)
                                            and not self._head_source.get(fb_head)):
                                        target_heads = [fb_head]
                                if override is not None:
                                    push_type = override.get('material') or slot.get('type', 'PLA')
                                    push_color = self._override_color_to_rgba(override.get('color', ''))
                                    push_vendor = override.get('brand') or slot.get('brand', 'Generic')
                                    push_subtype = override.get('subtype', '') or ''
                                else:
                                    push_type = slot.get('type', 'PLA')
                                    push_color = self.rgb2hex(*slot.get('color', (0, 0, 0)))
                                    push_vendor = slot.get('brand', 'Generic')
                                    push_subtype = slot.get('subtype', '')
                                want_type = push_type or ''
                                want_vendor = push_vendor or ''
                                want_color = (push_color or '').upper()
                                if len(want_color) == 8:
                                    want_color = want_color[:6]
                                want_sub = self._norm_subtype(push_subtype)
                                for head in target_heads:
                                    if not self.head_uses_ace(head):
                                        continue
                                    cur_type = ptc_types[head] if head < len(ptc_types) else ''
                                    cur_vendor = ptc_vendors[head] if head < len(ptc_vendors) else ''
                                    cur_color = (ptc_rgbas[head] if head < len(ptc_rgbas) else '') or ''
                                    cur_sub = ptc_subs[head] if head < len(ptc_subs) else ''
                                    cur_color_cmp = cur_color.upper()
                                    if len(cur_color_cmp) == 8:
                                        cur_color_cmp = cur_color_cmp[:6]
                                    needs_heal = (cur_type != want_type
                                                  or self._norm_vendor(cur_vendor)
                                                  != self._norm_vendor(want_vendor)
                                                  or cur_color_cmp != want_color
                                                  or self._norm_subtype(cur_sub) != want_sub)
                                    want_key = (want_type, want_vendor,
                                                want_color, want_sub)
                                    if needs_heal and \
                                            self._heal_official_skip.get(head) != want_key:
                                        logging.info(
                                            '[multiACE] display heal: head %d was "%s"/"%s"/%s/"%s", repushing %s/%s/%s/"%s"' % (
                                                head, cur_type, cur_vendor, cur_color, cur_sub,
                                                push_type, push_vendor, push_color, push_subtype))
                                        self._expect_ptc_push(head, push_type, push_color, push_vendor, push_subtype)
                                        try:
                                            self.gcode.run_script_from_command(
                                                'SET_PRINT_FILAMENT_CONFIG '
                                                'CONFIG_EXTRUDER=%d '
                                                'FILAMENT_TYPE="%s" '
                                                'FILAMENT_COLOR_RGBA=%s '
                                                'VENDOR="%s" '
                                                'FILAMENT_SUBTYPE="%s"' % (
                                                    head, push_type, push_color, push_vendor, push_subtype))
                                            self._heal_official_skip.pop(head, None)
                                            self._heal_fail_count.pop(head, None)
                                        except Exception as phe:
                                            m = str(phe)
                                            if ('not configurable' in m
                                                    or 'official' in m
                                                    or 'filament_spool_id' in m):
                                                self._heal_official_skip[head] = want_key
                                                self._heal_fail_count.pop(head, None)
                                                logging.info(
                                                    '[multiACE] display heal: head %d push rejected '
                                                    '(%s) - skipping repush until the identity '
                                                    'changes' % (head, m))
                                            else:
                                                prev_key, cnt = self._heal_fail_count.get(
                                                    head, (None, 0))
                                                cnt = cnt + 1 if prev_key == want_key else 1
                                                self._heal_fail_count[head] = (want_key, cnt)
                                                if cnt >= HEAL_MAX_FAILS:
                                                    self._heal_official_skip[head] = want_key
                                                    self._heal_fail_count.pop(head, None)
                                                    logging.warning(
                                                        '[multiACE] display heal: head %d rejected '
                                                        '%d times, giving up until the identity '
                                                        'changes: %s' % (head, cnt, m))
                                                else:
                                                    logging.info(
                                                        '[multiACE] display heal error (%d/%d): %s'
                                                        % (cnt, HEAL_MAX_FAILS, m))
                    except Exception as he:
                        logging.info('[multiACE] display heal error: %s' % he)
            try:
                self.send_request_to(idx, {"method": "get_status"}, callback)
            except Exception as he:
                logging.info('[multiACE] Heartbeat[%d] send failed: %s' % (idx, str(he)))
            return eventtime + 1.0
        return _tick

    def _handle_serial_failure(self, err, first, first_error=None):
        self._handle_per_ace_failure(self._active_device_index, err)

    def _pre_load(self, gate):
        feed_length = self.head_feed_length[gate]

        if feed_length <= 0:
            return

        self.log_always(self._t('msg.wait_ace_preload'))
        self.wait_ace_ready()

        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % gate, None)

        self._feed(gate, feed_length,
                   self.get_feed_speed(self._active_device_index), 0)

        while not self.is_ace_ready():
            self.reactor.pause(self.reactor.monotonic() + 0.105)
            if sensor and sensor.get_status(0)['filament_detected']:
                self._stop_feeding(gate)
                self.wait_ace_ready()
                self.log_always(self._t('msg.filament_detected_preload'))
                break

        if sensor and sensor.get_status(0)['filament_detected']:
            self.log_always(self._t('msg.select_autoload_menu'))

    def send_request(self, request, callback):
        self.send_request_to(self._active_device_index, request, callback)

    def wait_ace_ready(self):
        self.wait_ace_ready_on(self._active_device_index)

    def wait_ace_ready_on(self, idx, timeout=30.0, max_reconnects=2):
        info = self._info_per_ace.get(idx)
        if info is None:
            return

        protocol = self._protocols.get(idx)
        if protocol is not None and getattr(protocol, 'NAME', '') == 'v2':
            timeout = max(timeout, 60.0)
        deadline = time.monotonic() + timeout
        reconnect_count = 0
        feeding_waits = 0
        while info.get('status') != 'ready':
            if time.monotonic() > deadline:

                if (feeding_waits < WAIT_ACE_FEEDING_MAX
                        and self._v2_any_slot_active(idx)):
                    feeding_waits += 1
                    self.log_always(self._t('msg.ace_wait_busy_feeding',
                        ace=self._disp(idx), attempt=feeding_waits,
                        max=WAIT_ACE_FEEDING_MAX))
                    deadline = time.monotonic() + timeout
                    continue

                if reconnect_count >= max_reconnects:
                    self.log_error(self._t('msg.ace_stuck_powercycle',
                        ace=self._disp(idx),
                        status=info.get('status', '?'),
                        attempts=reconnect_count))
                    self._handle_per_ace_failure(idx, 'stuck_after_reconnects')
                    raise self.printer.command_error(
                        '[multiACE] ACE %d firmware stuck - power-cycle required' % idx)
                reconnect_count += 1
                self.log_error(self._t('msg.ace_wait_timeout_reconnect',
                    ace=self._disp(idx), timeout=timeout,
                    status=info.get('status', '?'),
                    attempt=reconnect_count, max=max_reconnects))
                try:
                    bg = self.printer.lookup_object('ace_bg_swap', None)
                    bg_state = (sorted(getattr(bg, '_busy', ()))
                                if bg is not None else 'n/a')
                    logging.error(
                        '[multiACE][DBG] wait_ace_ready_on(idx=%d) timeout: '
                        'active=%d bg_busy_heads=%s caller chain:\n%s'
                        % (idx, self._active_device_index, bg_state,
                           ''.join(traceback.format_stack(limit=10)[:-1])))
                except Exception:
                    pass
                try:
                    self._disconnect_from(idx)
                except Exception:
                    pass
                self.reactor.pause(self.reactor.monotonic() + 0.5)
                if self._open_ace(idx):
                    self.log_always(self._t('msg.ace_reconnected_after_timeout',
                        ace=self._disp(idx)))
                    info = self._info_per_ace.get(idx)
                    if info is None:
                        return

                    deadline = time.monotonic() + timeout
                    continue

                self._handle_per_ace_failure(idx, 'wait_ace_ready_timeout')
                raise self.printer.command_error(
                    '[multiACE] ACE %d unresponsive - reconnect failed, '
                    'operation aborted' % idx)
            curr_ts = self.reactor.monotonic()
            self.reactor.pause(curr_ts + 0.5)
            info = self._info_per_ace.get(idx)
            if info is None:
                return

    def is_ace_ready(self):
        idx = self._active_device_index
        info = self._info_per_ace.get(idx)
        if info is None:
            return False
        return info.get('status') == 'ready'

    def dwell(self, delay=1.0):
        curr_ts = self.reactor.monotonic()
        self.reactor.pause(curr_ts + delay)

    def _extruder_move(self, length, speed):
        pos = self.toolhead.get_position()
        pos[3] += length
        self.toolhead.move(pos, speed)
        return pos[3]

    cmd_ACE_START_DRYING_help = 'Starts ACE Pro dryer'

    def cmd_ACE_START_DRYING(self, gcmd):
        temperature = gcmd.get_int('TEMP')
        duration = gcmd.get_int('DURATION', 240)

        if duration <= 0:
            raise gcmd.error('Wrong duration')
        if temperature <= 0 or temperature > self.max_dryer_temperature:
            raise gcmd.error('Wrong temperature')

        self._wait_homing_clear()

        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
                return

            self.gcode.respond_info(self._t('msg.dryer_started'))

        self.wait_ace_ready()
        self.send_request(
            request={"method": "drying", "params": {"temp": temperature, "fan_speed": 7000, "duration": duration}},
            callback=callback)

    cmd_ACE_STOP_DRYING_help = '[multiACE] Stop ACE Pro dryer. Usage: ACE_STOP_DRYING [ACE=N]'

    def cmd_ACE_STOP_DRYING(self, gcmd):

        ace_idx = gcmd.get_int('ACE', self._active_device_index)
        if ace_idx < 0 or ace_idx >= len(self._ace_devices):
            self.log_always(self._t('msg.ace_not_available', ace=self._disp(ace_idx)))
            return

        self._wait_homing_clear()

        def callback(self, response):
            if response is None:
                self.log_error(self._t('msg.dryer_no_response_stop',
                    ace=self._disp(ace_idx)))
                return
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
                return
            self.gcode.respond_info(self._t('msg.dryer_stopped_on_ace',
                ace=self._disp(ace_idx)))

        self.wait_ace_ready_on(ace_idx)
        self.send_request_to(ace_idx, {"method": "drying_stop"}, callback)

    def _enable_feed_assist(self, index):

        if self._feed_assist_index != -1 and self._feed_assist_index != index:
            self.wait_ace_ready()
            self._retract(self._feed_assist_index, 5, 80)
        self.wait_ace_ready()
        self._arm_fa_for(self._active_device_index, index)
        self.wait_ace_ready()
        self.dwell(delay=0.7)

    cmd_ACE_ENABLE_FEED_ASSIST_help = 'Enables ACE feed assist'

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX')

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        self._enable_feed_assist(index)

    def _disable_feed_assist(self, index=-1):

        rt_index = self._feed_assist_index
        if rt_index == -1:
            return
        self.wait_ace_ready()
        self._disarm_fa_for(self._active_device_index)
        self.wait_ace_ready()
        self._retract(rt_index, 5, 80)
        self.dwell(0.3)

    cmd_ACE_DISABLE_FEED_ASSIST_help = 'Disables ACE feed assist'

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        index = gcmd.get_int('INDEX', self._feed_assist_index)

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')

        self._disable_feed_assist(index)

    def _feed(self, index, length, speed, how_wait=None):
        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
                return

        self.wait_ace_ready()
        self.send_request(
            request={"method": "feed_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        if how_wait is not None:
            self.dwell(delay=(how_wait / speed) + 0.1)
        else:
            self.dwell(delay=(length / speed) + 0.1)

    cmd_ACE_FEED_help = 'Feeds filament from ACE'

    def cmd_ACE_FEED(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int(
            'SPEED', self.get_feed_speed(self._active_device_index))

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._feed(index, length, speed)

    def _retract(self, index, length, speed, head=None):
        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
                return

        manual_check = head if head is not None else index
        if self.head_is_manual(manual_check):
            self._fa_trace(
                'retract skipped: head %d is manual (TPU bypass)' % manual_check)
            return

        idx = self._active_device_index
        proto = self._protocols.get(idx)
        if proto is not None and getattr(proto, 'NAME', None) == 'v2':
            def _stop_cb(self, response):
                pass
            try:
                self.send_request_to(idx, {
                    'method': 'stop_feed_assist',
                    'params': {'index': index},
                }, _stop_cb)
                self._fa_trace(
                    '_retract v2 pre-stop FA on ACE %d slot %d '
                    '(release rollback-lock before unwind)'
                    % (idx, index))
            except Exception as e:
                logging.info(
                    '[multiACE] V2 _retract pre-stop failed: %s' % e)
            if self._feed_assist_per_ace.get(idx, -1) == index:
                self._feed_assist_per_ace[idx] = -1
                if idx == self._active_device_index:
                    self._feed_assist_index = -1

        self.wait_ace_ready()
        self.send_request(
            request={"method": "unwind_filament", "params": {"index": index, "length": length, "speed": speed}},
            callback=callback)
        self.dwell(delay=(length / speed) + 0.1)

    def _first_loaded_slot_for_ace(self, ace_idx):
        gates = self._gate_status_per_ace.get(ace_idx)
        if gates:
            for s in range(len(gates)):
                if gates[s] == GATE_AVAILABLE:
                    return s
        info = self._info_per_ace.get(ace_idx) or {}
        slots = info.get('slots') or []
        for s in range(len(slots)):
            slot = slots[s] if isinstance(slots[s], dict) else {}
            st = slot.get('status')
            if st is not None and not self._is_empty_status(st):
                return s
        return None

    def _armed_slot_for_ace(self, ace_idx):
        s = self._feed_assist_per_ace.get(ace_idx, -1)
        if isinstance(s, int) and 0 <= s <= 3:
            return s
        if self._is_v2_idx(ace_idx):
            for s in range(4):
                try:
                    if self._v2_get_slot_status(ace_idx, s) in V2_FA_RUNNING_STATES:
                        return s
                except Exception:
                    break
        return None

    def _ace_slot_for_head(self, head):
        src = self._head_source.get(head)
        if src is not None:
            s = src.get('slot')
            if isinstance(s, int) and 0 <= s <= 3:
                if getattr(self, '_armed_slot_logged', None):
                    self._armed_slot_logged.pop(head, None)
                return s
        if getattr(self, '_ace_mode', 'multi') == 'head' and self.head_uses_ace(head):
            ace_idx = self.head_ace_for(head)
            s = self._armed_slot_for_ace(ace_idx)
            if s is None:
                s = self._first_loaded_slot_for_ace(ace_idx)
            else:
                if not hasattr(self, '_armed_slot_logged'):
                    self._armed_slot_logged = {}
                if self._armed_slot_logged.get(head) != (ace_idx, s):
                    self._armed_slot_logged[head] = (ace_idx, s)
                    logging.info('[multiACE] _ace_slot_for_head: head %d has '
                                 'no head_source - using ACE %d ARMED slot %d '
                                 '(FA still running)' % (head, ace_idx, s))
            if s is not None:
                return s
        return head

    def _resolve_retract_length(self, slot):
        if self._retract_length_override is not None:
            return self._retract_length_override
        return self.get_retract_length(self._active_device_index, slot)

    def retract_fil(self, slot, head=None):
        self._retract(slot, self._resolve_retract_length(slot),
                      self.get_retract_speed(self._active_device_index),
                      head=head)

    cmd_ACE_RETRACT_help = 'Retracts filament back to ACE'

    def cmd_ACE_RETRACT(self, gcmd):
        index = gcmd.get_int('INDEX')
        length = gcmd.get_int('LENGTH')
        speed = gcmd.get_int(
            'SPEED', self.get_retract_speed(self._active_device_index))

        if index < 0 or index >= 4:
            raise gcmd.error('Wrong index')
        if length <= 0:
            raise gcmd.error('Wrong length')
        if speed <= 0:
            raise gcmd.error('Wrong speed')

        self._retract(index, length, speed)

    def _set_feeding_speed(self, index, speed):
        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))

        self.send_request(
            request={"method": "update_feeding_speed", "params": {"index": index, "speed": speed}},
            callback=callback)

    def _stop_feeding(self, index):
        def callback(self, response):
            if response.get('code', 0) != 0:
                self.log_error(self._t('msg.ace_error_generic', error=response.get('msg')))
                return

        self.send_request(
            request={"method": "stop_feed_filament", "params": {"index": index}},
            callback=callback)

    cmd_ACE_SWITCH_help = 'Switch active ACE unit. Usage: ACE_SWITCH TARGET=0 [AUTOLOAD=1]'

    EXTRUDER_MAP = {
        0: ('left', 1),
        1: ('left', 0),
        2: ('right', 0),
        3: ('right', 1),
    }

    def _refresh_slot_overrides(self):
        """Re-read slot_overrides.json into self._slot_overrides.
        Picker overrides are stored by the FastAPI backend; ace.py
        consults this dict in _push_rfid_info and the heartbeat heal
        block so the printer's display matches the user-set labels.

        On read failure (missing file → no overrides; partial mid-write
        → JSONDecodeError) we keep the previously-loaded dict in
        memory rather than clearing it, so a transient race with the
        backend's write doesn't make all overrides disappear from the
        display for one tick."""
        try:
            import json as _json
            import os as _os
            if not _os.path.exists(self._slot_overrides_file):
                self._slot_overrides = {}
                self._slot_overrides_mtime = 0.0
                return
            with open(self._slot_overrides_file, 'r') as f:
                data = _json.load(f)
            if isinstance(data, dict):
                self._slot_overrides = data
                try:
                    self._slot_overrides_mtime = _os.path.getmtime(self._slot_overrides_file)
                except OSError:
                    pass
        except Exception as e:
            logging.info(
                '[multiACE] _refresh_slot_overrides: keeping previous, error: %s' % e)

    def _refresh_slot_overrides_if_changed(self):
        """Cheap mtime poll - reloads only when slot_overrides.json
        has been touched since we last read it (e.g. backend POST,
        backend auto-clear-on-eject, or another writer). When the set
        of override keys changes (added or removed), trigger a
        _push_rfid_info so the display picks up the new state - most
        importantly, when an override gets dropped (e.g. physical
        eject) the now-empty slot's display field needs to be cleared
        too."""
        try:
            import os as _os
            if not _os.path.exists(self._slot_overrides_file):
                if self._slot_overrides:
                    self._slot_overrides = {}
                    self._slot_overrides_mtime = 0.0
                    try:
                        self._push_rfid_info()
                    except Exception as pe:
                        logging.info('[multiACE] re-push after override drop: %s' % pe)
                return
            m = _os.path.getmtime(self._slot_overrides_file)
            if m == self._slot_overrides_mtime:
                return
            old_keys = set(self._slot_overrides.keys())
            self._refresh_slot_overrides()
            new_keys = set(self._slot_overrides.keys())
            if old_keys != new_keys:
                try:
                    self._push_rfid_info()
                except Exception as pe:
                    logging.info('[multiACE] re-push after override change: %s' % pe)
        except OSError:
            pass

    def _norm_subtype(self, s):
        """Canonicalise a filament subtype for comparison: the firmware's
        'generic' entry shows on the display as 'Basic' / '' interchangeably,
        so treat all three as equal. Otherwise the heal would loop (push ''
        -> display shows 'Basic' -> mismatch -> push again)."""
        s = (s or '').strip().lower()
        return '' if s in ('', 'basic', 'generic') else s

    def _norm_vendor(self, v):
        """Companion to _norm_subtype for the VENDOR comparison: '' and
        'Generic' are the same identity ('' is what an override/brandless RFID
        carries as SOLL, 'Generic' is what _norm_vendor_push canonicalises on
        the WRITE into print_task_config). Without this the display heal loops
        at 1 Hz forever: SOLL vendor '' vs stored 'Generic' -> mismatch ->
        repush '' -> the SET_PRINT_FILAMENT_CONFIG wrapper normalises it back
        to 'Generic' -> mismatch again (HW 2026-07-10: 2549 heal pushes in one
        session, 1/s per head). Same class as the _norm_subtype loop above."""
        s = (v or '').strip().lower()
        return '' if s in ('', 'generic') else s

    def _norm_vendor_push(self, v):
        """Canonicalise a generic-equivalent vendor to the DB-valid 'Generic'
        on the way INTO print_task_config, so every head carrying the same
        generic material stores a BYTE-IDENTICAL vendor string. The stock
        auto-replenish match (print_task_config.py, black box) compares
        vendor/type/subtype byte-exact between the ran-out head's backup and the
        candidates; a head stored '' vs another stored 'Generic' failed to match
        even at identical colour+material (2026-07-08 'cannot auto replenish'
        with a same-colour PLA head loaded). Real brands (Bambu/Sunlu/...) and
        the stock RFID sentinel 'NONE' pass through unchanged. NOT '' - the
        canonical form must stay a valid DB key (get_load_temp('Generic',...),
        §5), and 'Generic'/'Basic' is what 3 of 4 heads + the DB already have."""
        s = (v or '').strip().lower()
        return 'Generic' if s in ('', 'generic') else v

    def _norm_subtype_push(self, s):
        """Companion to _norm_vendor_push: a generic-equivalent subtype ->
        'Basic' (get_load_temp('Generic','PLA','Basic') -> 250, §5). Real
        subtypes (Matte/Silk/CF/...) pass through unchanged. Distinct from
        _norm_subtype above, which canonicalises to '' for COMPARISON - this one
        emits the DB-valid 'Basic' for the WRITE into print_task_config."""
        v = (s or '').strip().lower()
        return 'Basic' if v in ('', 'basic', 'generic') else s

    @staticmethod
    def _is_empty_status(status):
        """True if a slot's reported status means "empty". V1 (ACE Pro)
        firmware reports 'empty1', V2 (ACE 2) reports 'empty' (audit A-1).
        Match both via the same prefix test the web backend already uses
        (main.py _parse_state: raw_status.startswith('empty')). Plain
        '== empty' missed V1, so V1 empty slots never became GATE_EMPTY
        (no _pre_load on insert, wrong auto_feed gating)."""
        return str(status or '').startswith('empty')

    _DEFAULT_MATERIALS = (
        'PLA', 'PLA-CF',
        'PETG', 'PETG-CF', 'PETG-HF',
        'ABS', 'ASA',
        'TPU',
        'PA', 'PA-CF', 'PA-GF', 'PA6-CF', 'PA6-GF',
        'PC', 'PC-ABS',
        'PVA',
    )
    _FILAMENT_DB_PATHS = (
        '/home/lava/klipper/klippy/extras/filament_parameters.py',
        '/home/printer_data/klipper/klippy/extras/filament_parameters.py',
        '/usr/share/klipper/klippy/extras/filament_parameters.py',
    )
    _FILAMENT_DB_META_KEYS = frozenset((
        'version', 'hard_filaments_max_flow_k', 'soft_filaments_max_flow_k',
    ))

    def _parse_filament_db_materials(self):
        """Read the firmware material list straight from
        filament_parameters.py (the web backend's single source of truth).
        On 1.4 the module is NOT loaded as a Klipper object, so lookup
        fails; the FILE still ships the FILAMENT_PARA_CFG_DEFAULT literal.
        Parse its top-level dict keys with ast (no import - the module needs
        a printer object) and drop the non-material meta keys."""
        import ast as _ast
        for path in self._FILAMENT_DB_PATHS:
            try:
                with open(path, 'r') as f:
                    tree = _ast.parse(f.read())
            except Exception:
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Assign):
                    continue
                for tgt in node.targets:
                    if (isinstance(tgt, _ast.Name)
                            and tgt.id == 'FILAMENT_PARA_CFG_DEFAULT'
                            and isinstance(node.value, _ast.Dict)):
                        keys = set()
                        for k in node.value.keys:
                            if isinstance(k, _ast.Constant) and isinstance(k.value, str):
                                if k.value not in self._FILAMENT_DB_META_KEYS:
                                    keys.add(k.value.upper())
                        if keys:
                            return keys
        return set()

    def _get_known_main_types(self):
        """Upper-cased set of base material types known to the firmware.
        Source priority: loaded filament_parameters object (rare on 1.4) ->
        the DB file (same as the web) -> hardcoded fallback. Cached - the
        list is static at runtime, so we must NOT re-read/deepcopy it every
        heartbeat."""
        cache = getattr(self, '_known_main_types_cache', None)
        if cache is not None:
            return cache
        types = set()
        try:
            fp = self.printer.lookup_object('filament_parameters', None)
            if fp is not None:
                cfg = fp.get_status()
                for k, v in cfg.items():
                    if isinstance(v, dict) and any(
                            str(kk).startswith('vendor_') for kk in v):
                        types.add(str(k).upper())
        except Exception as e:
            logging.info('[multiACE] _get_known_main_types object: %s' % e)
        if not types:
            types = self._parse_filament_db_materials()
        if not types:
            types = set(self._DEFAULT_MATERIALS)
        self._known_main_types_cache = types
        return types

    def _split_type_subtype(self, type_str):
        """A spool's RFID 'type' can arrive with the vendor PREFIXED and/or the
        sub-type SUFFIXED onto the base material (e.g. 'Snapmaker PLA Tr' or
        'PLA Glow'), with the brand field left empty. The firmware DB only knows
        base materials, so the merged string as FILAMENT_TYPE is not printable
        and the display shows e.g. 'Snapmaker PLA'. Scan the tokens for the FIRST
        known base material: tokens before it = vendor, the token = base, tokens
        after = subtype. Returns (base, subtype, vendor). Leaves the string
        unchanged as the base (vendor/subtype empty) when no token is a known
        material - don't invent a split. Case-insensitive."""
        t = (type_str or '').strip()
        if not t:
            return ('', '', '')
        known = self._get_known_main_types()
        if t.upper() in known:
            return (t, '', '')
        parts = t.split()
        for i, tok in enumerate(parts):
            if tok.upper() in known:
                return (tok, ' '.join(parts[i + 1:]), ' '.join(parts[:i]))
        return (t, '', '')

    def _override_for(self, ace_idx, slot_idx):
        """Return the override dict for (ace, slot) when at least one
        meaningful field is set, else None."""
        o = self._slot_overrides.get('%d_%d' % (int(ace_idx), int(slot_idx)))
        if not o:
            return None
        if not (o.get('material') or o.get('color')):
            return None
        return o

    def _override_color_to_rgba(self, hex_color):
        """Picker stores '#rrggbb'; display wants RRGGBBAA."""
        h = (hex_color or '').lstrip('#').upper()
        if len(h) == 6:
            return h + 'FF'
        if len(h) == 8:
            return h
        return 'FFFFFFFF'

    def _ptc_color_to_override_hex(self, c):
        """SET_PRINT_FILAMENT_CONFIG arg comes in as RRGGBB or RRGGBBAA
        (with or without #). Picker overrides store '#rrggbb'."""
        if c is None:
            return ''
        s = str(c).lstrip('#').upper()
        if len(s) >= 6:
            return '#' + s[:6]
        return ''

    def _save_slot_overrides(self):
        """Write self._slot_overrides back to slot_overrides.json
        atomically (.tmp + os.replace) so concurrent readers - the
        FastAPI backend's mtime poller, ace.py's own mtime poller -
        never see a half-written file."""
        try:
            import json as _json
            import os as _os
            d = _os.path.dirname(self._slot_overrides_file)
            if d and not _os.path.exists(d):
                _os.makedirs(d, exist_ok=True)
            tmp = self._slot_overrides_file + '.tmp'
            with open(tmp, 'w') as f:
                _json.dump(self._slot_overrides, f, indent=2)
            _os.replace(tmp, self._slot_overrides_file)
            try:
                self._slot_overrides_mtime = _os.path.getmtime(
                    self._slot_overrides_file)
            except OSError:
                pass
        except Exception as e:
            logging.info('[multiACE] _save_slot_overrides: %s' % e)

    def _expect_ptc_push(self, head, ftype, color_rgba, vendor, subtype):
        """Record a SET_PRINT_FILAMENT_CONFIG line we just queued so the
        wrapper can recognise it as an ace.py-internal push and skip
        the override-capture path. Cap the buffer at 32 entries so a
        gcode that errored before the wrapper ran can't grow it
        unbounded."""
        self._expected_ptc_pushes.append({
            'head':    int(head),
            'type':    str(ftype or ''),
            'color':   str(color_rgba or '').upper().lstrip('#'),
            'vendor':  str(vendor or ''),
            'subtype': str(subtype or ''),
        })
        if len(self._expected_ptc_pushes) > 32:
            self._expected_ptc_pushes = self._expected_ptc_pushes[-32:]

    def _ptc_push_guarded(self, head, ftype, color_rgba, vendor, subtype, ctx):
        """Push one head's filament identity, tolerating a rejection.

        Used by the two RFID pushes that are NOT part of the display heal:
        the fresh rfid==2 transition and the unloaded-head fallback. Both
        used to call run_script_from_command bare, inside the heartbeat's
        response callback - and the response dispatcher does not guard
        callbacks either, so a rejected push escaped into a reactor
        context, which can end in a printer shutdown: a WORSE outcome than
        the heal's popup loop for the very same firmware rejection.

        Rejections are also negative-cached per head on the pushed
        identity: the fallback runs on EVERY heartbeat, so without this a
        rejection there would block the touchscreen with a fresh level-3
        popup every second - catching the exception alone does not help,
        the firmware raises it before we see it.
        """
        key = (str(ftype or ''), str(color_rgba or ''),
               str(vendor or ''), str(subtype or ''))
        if self._ptc_push_block.get(head) == key:
            return False
        self._expect_ptc_push(head, ftype, color_rgba, vendor, subtype)
        try:
            self.gcode.run_script_from_command(
                'SET_PRINT_FILAMENT_CONFIG '
                'CONFIG_EXTRUDER=%d '
                'FILAMENT_TYPE="%s" '
                'FILAMENT_COLOR_RGBA=%s '
                'VENDOR="%s" '
                'FILAMENT_SUBTYPE="%s"' % (
                    head, ftype, color_rgba, vendor, subtype))
            self._ptc_push_block.pop(head, None)
            return True
        except Exception as e:
            self._ptc_push_block[head] = key
            logging.warning(
                '[multiACE] %s push for head %d rejected - not repeating it '
                'for this identity: %s' % (ctx, head, e))
            return False

    def _ptc_spool_id_for(self, head):
        """The spool id currently configured on a head, 0 if none.

        Stock 1.5.2 only. Returns 0 on anything older (no such field), on a
        head without one, or if print_task_config cannot be read - so a
        machine that never sees a spool id behaves exactly as before.
        """
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is None:
                return 0
            ids = (getattr(ptc, 'print_task_config', None)
                   or {}).get('filament_spool_id') or []
            return int(ids[head]) if 0 <= head < len(ids) else 0
        except Exception:
            return 0

    def _ptc_official_for(self, head):
        """True when stock has flagged this head as an official spool.

        Set from an RFID read (filament_detect stamps OFFICIAL for any
        identity that arrives with parameters) and cleared only by a
        SUCCESSFUL set - an unload does NOT clear it, so a head can stay
        flagged from a spool that is long gone.
        """
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is None:
                return False
            flags = (getattr(ptc, 'print_task_config', None)
                     or {}).get('filament_official') or []
            return bool(flags[head]) if 0 <= head < len(flags) else False
        except Exception:
            return False

    def _ptc_identity_unchanged(self, head, params):
        """Does this push carry the identity the head already shows?

        Decides whether an existing spool id may ride along. Same identity =
        we are only refreshing what is there, so the binding still describes
        the filament and is preserved. DIFFERENT identity = another spool is
        in that head, the binding would now point at the wrong one - we leave
        the id out and let stock clear it, rather than keep a link that lies.
        """
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            cfg = getattr(ptc, 'print_task_config', None) or {}

            def _cur(key):
                v = cfg.get(key) or []
                return str(v[head]) if 0 <= head < len(v) else ''
            return (str(params.get('FILAMENT_TYPE', '') or '').strip('"')
                        == _cur('filament_type')
                    and str(params.get('FILAMENT_COLOR_RGBA', '') or ''
                            ).upper().lstrip('#')
                        == _cur('filament_color_rgba').upper().lstrip('#'))
        except Exception:
            return False

    def _match_expected_push(self, incoming):
        """Index of the recorded push matching this command, else None.

        Peeked BEFORE the stock handler runs (to decide whether to carry a
        spool id) and popped after, so the comparison lives in one place.
        """
        norm_sub = self._norm_subtype(incoming.get('subtype', ''))
        for i, exp in enumerate(self._expected_ptc_pushes):
            if (exp['head'] == incoming['head']
                    and exp['type'] == incoming['type']
                    and exp['color'] == incoming['color']
                    and exp['vendor'] == incoming['vendor']
                    and self._norm_subtype(exp.get('subtype', '')) == norm_sub):
                return i
        return None

    def _wrap_set_print_filament_config(self, gcmd):
        """Replacement handler for SET_PRINT_FILAMENT_CONFIG. Always
        chains to the original print_task_config handler first so the
        printer state still updates. Then either pops a matching
        expected entry (= our own push) or treats the gcode as a
        display-driven user edit and persists it as an override."""

        if self._orig_set_ptc is not None:
            params = gcmd.get_command_parameters()
            saved = None
            _ph = int(gcmd.get_int('CONFIG_EXTRUDER', -1))
            _skip_push = False
            if self._match_expected_push({
                    'head':    _ph,
                    'type':    str(gcmd.get('FILAMENT_TYPE', '') or ''),
                    'color':   str(gcmd.get('FILAMENT_COLOR_RGBA', '')
                                   or '').upper().lstrip('#'),
                    'vendor':  str(gcmd.get('VENDOR', '') or ''),
                    'subtype': str(gcmd.get('FILAMENT_SUBTYPE', '') or ''),
                }) is not None:
                if 'FILAMENT_SPOOL_ID' not in params:
                    _sid = self._ptc_spool_id_for(_ph)
                    if _sid > 0 and self._ptc_identity_unchanged(_ph, params):
                        saved = dict(params)
                        params['FILAMENT_SPOOL_ID'] = str(_sid)
                        logging.info(
                            '[multiACE] head %d keeps spool id %d - same '
                            'identity, the binding still fits'
                            % (self._disp(_ph), _sid))
                    elif _sid > 0:
                        logging.info(
                            '[multiACE] head %d drops spool id %d - a '
                            'different filament is in it now, the binding '
                            'would point at the wrong spool'
                            % (self._disp(_ph), _sid))
                _official = self._ptc_official_for(_ph)
                if not _official:
                    self._force_official_count.pop(_ph, None)
                elif self._identity_priority == 'spoollink':
                    _skip_push = True
                    logging.info(
                        '[multiACE] head %d is flagged official - leaving it '
                        'to SpoolLink (identity_priority=spoollink)'
                        % self._disp(_ph))
                elif ('FORCE' not in params
                        and self._identity_priority == 'multiace'
                        and self.head_uses_ace(_ph)):
                    _n = self._force_official_count.get(_ph, 0)
                    if _n < FORCE_OFFICIAL_MAX:
                        if saved is None:
                            saved = dict(params)
                        params['FORCE'] = '1'
                        self._force_official_count[_ph] = _n + 1
                        logging.info(
                            '[multiACE] head %d is flagged official - forcing '
                            'our identity through (identity_priority=multiace)'
                            % self._disp(_ph))
                    elif _n == FORCE_OFFICIAL_MAX:
                        self._force_official_count[_ph] = _n + 1
                        logging.warning(
                            '[multiACE] head %d keeps coming back as official '
                            'after %d forced pushes - something is re-stamping '
                            'it. Leaving it alone; set identity_priority: '
                            'spoollink to stop trying.'
                            % (self._disp(_ph), FORCE_OFFICIAL_MAX))
            if str(params.get('FILAMENT_TYPE', '') or '').strip():
                nv = (self._norm_vendor_push(params.get('VENDOR'))
                      if 'VENDOR' in params else None)
                ns = (self._norm_subtype_push(params.get('FILAMENT_SUBTYPE'))
                      if 'FILAMENT_SUBTYPE' in params else None)
                if ((nv is not None and nv != params.get('VENDOR'))
                        or (ns is not None
                            and ns != params.get('FILAMENT_SUBTYPE'))):
                    if saved is None:
                        saved = dict(params)
                    if nv is not None:
                        params['VENDOR'] = nv
                    if ns is not None:
                        params['FILAMENT_SUBTYPE'] = ns
            try:
                if _skip_push:
                    pass
                elif saved is not None and self._raw_set_ptc is not None:
                    self._raw_set_ptc(gcmd)
                else:
                    self._orig_set_ptc(gcmd)
            finally:
                if saved is not None:
                    params.clear()
                    params.update(saved)
        try:
            head = gcmd.get_int('CONFIG_EXTRUDER', None)
            if head is None:
                return
            incoming = {
                'head':    int(head),
                'type':    str(gcmd.get('FILAMENT_TYPE', '') or ''),
                'color':   str(gcmd.get('FILAMENT_COLOR_RGBA', '') or '').upper().lstrip('#'),
                'vendor':  str(gcmd.get('VENDOR', '') or ''),
                'subtype': str(gcmd.get('FILAMENT_SUBTYPE', '') or ''),
            }
            _i = self._match_expected_push(incoming)
            if _i is not None:
                self._expected_ptc_pushes.pop(_i)
                return

            self._capture_display_edit(incoming)
        except Exception as e:
            logging.info(
                '[multiACE] _wrap_set_print_filament_config error: %s' % e)

    def _capture_display_edit(self, ev):
        """Persist a display-driven SET_PRINT_FILAMENT_CONFIG into
        self._slot_overrides.

        Mapping rules:
        - head_source[head] set with real values (= loaded) -> (src.ace, src.slot)
        - head_source[head] is None (= unloaded but slot N of the active
          ACE is wired to extruder N by the parallel splitter)
          -> (active_device, head)
        - head_source[head] still in cmd_ACE_LOAD_HEAD's placeholder
          state (type='' / color='000000') -> skip; pushes during that
          window are ace.py's own internal work and the (ace, slot)
          mapping isn't user intent yet.
        """
        if self._swap_in_progress:

            return
        head = int(ev['head'])
        if not self.head_uses_ace(head):
            self._fa_trace(
                'display edit for head %d ignored (no ACE slot: manual or '
                'feeder)' % head)
            return
        if (ev.get('vendor') or '').strip().upper() == 'NONE':
            self._fa_trace(
                'display edit for head %d ignored (VENDOR=NONE = stock RFID '
                'auto-fill, not a user edit)' % head)
            return
        src = self._head_source.get(head)
        if src:

            if getattr(self, '_in_internal_load_head', False):
                return
            ace_idx = int(src.get('ace_index', 0))
            slot_idx = int(src.get('slot', 0))
        else:
            if getattr(self, '_ace_mode', 'multi') == 'head':
                ace_idx = self.head_ace_for(head)
                _s = self._first_loaded_slot_for_ace(ace_idx)
                slot_idx = _s if _s is not None else head
            else:
                ace_idx = self._active_device_index
                slot_idx = head

        key = '%d_%d' % (ace_idx, slot_idx)
        existing = self._slot_overrides.get(key) or {}

        ptc = self.printer.lookup_object('print_task_config', None)
        ptc_status = ptc.get_status() if ptc is not None else {}
        ptc_types = ptc_status.get('filament_type', []) or []
        ptc_vendors = ptc_status.get('filament_vendor', []) or []
        ptc_subs = ptc_status.get('filament_sub_type', []) or []
        ptc_rgbas = ptc_status.get('filament_color_rgba', []) or []
        ptc_type = (ptc_types[head] if head < len(ptc_types) else '') or ''
        ptc_vendor = (ptc_vendors[head] if head < len(ptc_vendors) else '') or ''
        ptc_sub = (ptc_subs[head] if head < len(ptc_subs) else '') or ''
        ptc_rgba = (ptc_rgbas[head] if head < len(ptc_rgbas) else '') or ''
        if ptc_type == 'NONE':
            ptc_type = ''
        if ptc_vendor == 'NONE':
            ptc_vendor = ''
        if ptc_rgba.upper() in ('00000000', '000000FF'):
            ptc_rgba = ''

        inc_type = (ev.get('type') or '').strip()
        inc_color_raw = (ev.get('color') or '').strip().lstrip('#').upper()
        inc_vendor = (ev.get('vendor') or '').strip()
        inc_subtype = (ev.get('subtype') or '').strip()

        has_identity = bool(existing.get('material') or existing.get('color')
                            or ptc_type)
        if (not inc_type and not inc_vendor
                and (inc_color_raw in ('', '00000000')
                     or (inc_color_raw in ('000000', '000000FF')
                         and not has_identity))):
            return

        merged_material = inc_type or existing.get('material') or ptc_type
        merged_brand = inc_vendor or existing.get('brand') or ptc_vendor
        if inc_type:
            merged_subtype = inc_subtype
        else:
            merged_subtype = inc_subtype or existing.get('subtype') or ptc_sub
        if inc_color_raw and inc_color_raw != '00000000':
            merged_color = self._ptc_color_to_override_hex(inc_color_raw)
        elif existing.get('color'):
            merged_color = existing['color']
        elif ptc_rgba:
            merged_color = self._ptc_color_to_override_hex(ptc_rgba)
        else:
            merged_color = ''

        new_override = {
            'ace':      ace_idx,
            'slot':     slot_idx,
            'material': merged_material,
            'brand':    merged_brand,
            'subtype':  merged_subtype,
            'color':    merged_color,
        }
        if existing == new_override:
            return
        self._slot_overrides[key] = new_override
        logging.info(
            '[multiACE] display edit -> override (ACE %d / slot %d): %s' % (
                ace_idx, slot_idx, new_override))
        self._save_slot_overrides()

    def _refresh_filament_exist_flags(self):
        """Recompute print_task_config.filament_exist from the live toolhead
        sensors. The Snapmaker display shows "/" (no filament) for a head only
        when filament_exist[head] is False; with filament present but no type
        it shows "?", with a type the material. Stock recomputes that flag only
        on runout / feed-port / sensor-toggle events (print_task_config
        _runout_evt_handle etc.), so after an idle unload it goes stale (stays
        True) and an emptied head wrongly shows "?" instead of "/". We can't
        set the flag via SET_PRINT_FILAMENT_CONFIG (no such parameter), so
        trigger the stock refresh whenever the toolhead filament state may have
        changed (load/unload, display resync)."""
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None and hasattr(ptc, 'update_filament_flags'):
                ptc.update_filament_flags()
        except Exception as e:
            logging.info('[multiACE] _refresh_filament_exist_flags: %s' % e)

    def _push_rfid_info(self):
        logging.info('[multiACE] _push_rfid_info: active_device=%d, head_source=%s' % (
            self._active_device_index, str({k: (v['ace_index'] if v else None) for k, v in self._head_source.items()})))
        active = self._active_device_index
        self._refresh_filament_exist_flags()

        lines = []
        backup_heads = []
        for head in range(4):
            if not self.head_uses_ace(head):
                logging.info(
                    '[multiACE] _push_rfid_info: head %d - non-ACE '
                    '(manual/feeder), leaving display filament info untouched'
                    % head)
                continue
            source = self._head_source.get(head)
            if source:

                src_ace = int(source.get('ace_index', 0))
                src_slot = int(source.get('slot', 0))
                ace_info = self._info_per_ace.get(src_ace, {}) or {}
                slots = ace_info.get('slots', []) or []
                slot = slots[src_slot] if src_slot < len(slots) else {}
                override = self._override_for(src_ace, src_slot)
                fallback_type = source.get('type') or slot.get('type', 'PLA')
                fallback_color = source.get('color') or self.rgb2hex(*slot.get('color', (0, 0, 0)))
                fallback_brand = source.get('brand') or slot.get('brand', 'Generic')
                fallback_subtype = source.get('subtype', '') or slot.get('subtype', '')
                logging.info(
                    '[multiACE] _push_rfid_info: head %d - loaded from ACE %d / slot %d, '
                    'pushing %s' % (head, src_ace, src_slot,
                                    'override' if override is not None else 'source'))

                if override is not None:
                    push_type = override.get('material') or fallback_type
                    push_color = self._override_color_to_rgba(override.get('color', ''))
                    push_brand = override.get('brand') or fallback_brand
                    push_subtype = override.get('subtype', '') or ''
                    self._expect_ptc_push(head, push_type, push_color, push_brand, push_subtype)
                    lines.append(
                        'SET_PRINT_FILAMENT_CONFIG '
                        'CONFIG_EXTRUDER=%d '
                        'FILAMENT_TYPE="%s" '
                        'FILAMENT_COLOR_RGBA=%s '
                        'VENDOR="%s" '
                        'FILAMENT_SUBTYPE="%s"' % (
                            head, push_type, push_color, push_brand, push_subtype))
                    backup_heads.append(head)
                else:
                    rfid_type = source.get('type') or (
                        slot.get('type', '') if slot.get('rfid') == 2 else '')
                    if not rfid_type:
                        logging.info(
                            '[multiACE] _push_rfid_info: head %d - loaded, no '
                            'override/RFID; clearing display ("?")' % head)
                        self._expect_ptc_push(head, '', '000000FF', '', '')
                        lines.append(
                            'SET_PRINT_FILAMENT_CONFIG '
                            'CONFIG_EXTRUDER=%d '
                            'FILAMENT_TYPE="" '
                            'FILAMENT_COLOR_RGBA=000000FF '
                            'VENDOR="" '
                            'FILAMENT_SUBTYPE=""' % head)
                    else:
                        self._expect_ptc_push(head, rfid_type, fallback_color, fallback_brand, fallback_subtype)
                        lines.append(
                            'SET_PRINT_FILAMENT_CONFIG '
                            'CONFIG_EXTRUDER=%d '
                            'FILAMENT_TYPE="%s" '
                            'FILAMENT_COLOR_RGBA=%s '
                            'VENDOR="%s" '
                            'FILAMENT_SUBTYPE="%s"' % (
                                head, rfid_type, fallback_color, fallback_brand, fallback_subtype))
                        backup_heads.append(head)
            else:

                if getattr(self, '_ace_mode', 'multi') == 'head':
                    disp_ace = self.head_ace_for(head)
                    disp_slot = head
                    _s = self._first_loaded_slot_for_ace(disp_ace)
                    if _s is not None:
                        disp_slot = _s
                else:
                    disp_ace = active
                    disp_slot = head

                empty_override = self._override_for(disp_ace, disp_slot)
                if empty_override is not None:
                    ace_info = self._info_per_ace.get(disp_ace, {}) or {}
                    aslots = ace_info.get('slots', []) or []
                    aslot = aslots[disp_slot] if disp_slot < len(aslots) else {}
                    push_type = empty_override.get('material') or aslot.get('type', 'PLA')
                    push_color = self._override_color_to_rgba(empty_override.get('color', ''))
                    push_brand = empty_override.get('brand') or aslot.get('brand', 'Generic')
                    push_subtype = empty_override.get('subtype', '') or ''
                    logging.info(
                        '[multiACE] _push_rfid_info: head %d - unloaded, '
                        'pushing override (ACE %d / slot %d)' % (
                            head, disp_ace, disp_slot))
                    self._expect_ptc_push(head, push_type, push_color, push_brand, push_subtype)
                    lines.append(
                        'SET_PRINT_FILAMENT_CONFIG '
                        'CONFIG_EXTRUDER=%d '
                        'FILAMENT_TYPE="%s" '
                        'FILAMENT_COLOR_RGBA=%s '
                        'VENDOR="%s" '
                        'FILAMENT_SUBTYPE="%s"' % (
                            head, push_type, push_color, push_brand, push_subtype))
                    continue
                ace_info = self._info_per_ace.get(disp_ace, {}) or {}
                aslots = ace_info.get('slots', []) or []
                aslot = aslots[disp_slot] if disp_slot < len(aslots) else {}
                if aslot.get('rfid') == 2:
                    push_type = aslot.get('type', 'PLA')
                    push_color = self.rgb2hex(*aslot.get('color', (0, 0, 0)))
                    push_brand = aslot.get('brand', 'Generic')
                    push_subtype = aslot.get('subtype', '')
                    logging.info(
                        '[multiACE] _push_rfid_info: head %d - unloaded, '
                        'pushing ACE %d slot %d RFID' % (head, disp_ace, disp_slot))
                    self._expect_ptc_push(head, push_type, push_color, push_brand, push_subtype)
                    lines.append(
                        'SET_PRINT_FILAMENT_CONFIG '
                        'CONFIG_EXTRUDER=%d '
                        'FILAMENT_TYPE="%s" '
                        'FILAMENT_COLOR_RGBA=%s '
                        'VENDOR="%s" '
                        'FILAMENT_SUBTYPE="%s"' % (
                            head, push_type, push_color, push_brand, push_subtype))
                    continue
                logging.info(
                    '[multiACE] _push_rfid_info: head %d - empty, clearing display' % head)
                self._expect_ptc_push(head, '', '000000FF', '', '')
                lines.append(
                    'SET_PRINT_FILAMENT_CONFIG '
                    'CONFIG_EXTRUDER=%d '
                    'FILAMENT_TYPE="" '
                    'FILAMENT_COLOR_RGBA=000000FF '
                    'VENDOR="" '
                    'FILAMENT_SUBTYPE=""' % head)
        for _ln in lines:
            try:
                self.gcode.run_script_from_command(_ln)
            except Exception as pe:
                logging.info(
                    '[multiACE] _push_rfid_info: one head refused, '
                    'continuing with the rest: %s' % pe)
        if backup_heads:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None:
                for _bh in backup_heads:
                    try:
                        ptc.backup_filament_info(_bh)
                    except Exception as e:
                        logging.info(
                            '[multiACE] replenish backup sync failed head '
                            '%d: %s' % (_bh, e))

    cmd_MULTIACE_REFRESH_OVERRIDES_help = (
        '[multiACE] Reload slot_overrides.json and push to display')

    def cmd_MULTIACE_REFRESH_OVERRIDES(self, gcmd):
        self._refresh_slot_overrides()
        self._push_rfid_info()

    cmd_ACE_SET_PICKUP_CLEANING_help = (
        '[multiACE] Enable/disable Pickup-Cleaning (ENABLE=0|1). Live + '
        'persisted as ace__pickup_cleaning.')

    def cmd_ACE_SET_PICKUP_CLEANING(self, gcmd):
        enable = bool(gcmd.get_int('ENABLE', 1, minval=0, maxval=1))
        self._pickup_cleaning = enable
        try:
            if self.save_variables:
                self.save_variable('ace__pickup_cleaning', enable, write=True)
        except Exception as e:
            logging.info('[multiACE] persist ace__pickup_cleaning failed: %s'
                         % e)
        self.log_always('[multiACE] Pickup-Cleaning %s'
                        % ('ON' if enable else 'OFF'))

    cmd_ACE_PICKUP_CLEAN_help = (
        '[multiACE] Short nozzle wipe at the discard position after a bare-T '
        'tool pickup (HEAD=n). No-op unless Pickup-Cleaning is enabled. The '
        'preflight stamps this after picks that have no other cleaning move.')

    def cmd_ACE_PICKUP_CLEAN(self, gcmd):
        if not getattr(self, '_pickup_cleaning', False):
            return
        head = gcmd.get_int('HEAD', None)
        try:
            ps = self.printer.lookup_object('print_stats', None)
            printing = (ps is not None and ps.get_status(
                self.reactor.monotonic()).get('state') == 'printing')
        except Exception:
            printing = False
        if not printing or self._swap_in_progress:
            return
        self.log_always('[multiACE] Pickup-Cleaning: nozzle wipe at the discard '
                        'position (head %s)'
                        % (self._disp(head) if head is not None else '?'))
        self._discard_wipe(head, 'pickup-clean')

    def _discard_wipe(self, head, tag):
        """Shared discard-wipe excursion (Z hop -> discard -> stock ooze-cutoff
        wipe -> pos/E restore). Callers: ACE_PICKUP_CLEAN and the post-resume
        no-op wipe. Fail-open; returns True when the wipe ran."""
        try:
            homed = self.toolhead.get_status(
                self.reactor.monotonic()).get('homed_axes', '')
        except Exception:
            homed = ''
        if not all(a in homed for a in 'xyz'):
            logging.info('[multiACE] [%s] axes not homed (%s) - '
                         'skipped' % (tag, homed or 'none'))
            return False
        try:
            heater = self.toolhead.get_extruder().get_heater()
            if not bool(getattr(heater, 'can_extrude', True)):
                logging.info('[multiACE] [%s] below min_extrude_temp '
                             '- skipped' % tag)
                return False
        except Exception:
            pass
        gcode_move = self.printer.lookup_object('gcode_move')
        saved_pos = self.toolhead.get_position()[:3]
        saved_speed = gcode_move.speed
        saved_absolute = gcode_move.absolute_coord
        saved_e_base = gcode_move.base_position[3]
        saved_e_last = gcode_move.last_position[3]
        _added_suppress = (head is not None
                           and head not in self._runout_suppress_heads)
        if _added_suppress:
            self._runout_suppress_heads.add(head)
        try:
            self.gcode.run_script_from_command('G91')
            self.gcode.run_script_from_command('G1 Z2 F600')
            self.gcode.run_script_from_command('G90')
            self.gcode.run_script_from_command(
                'MOVE_TO_DISCARD_FILAMENT_POSITION')
            self.toolhead.wait_moves()
            self.gcode.run_script_from_command(
                'INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2')
            self.toolhead.wait_moves()
            logging.info('[multiACE] [%s] nozzle wipe done (head %s)'
                         % (tag, self._disp(head) if head is not None else '?'))
            _wipe_ok = True
        except Exception as e:
            logging.info('[multiACE] [%s] wipe failed (continuing): '
                         '%s' % (tag, e))
            _wipe_ok = False
        finally:
            if _added_suppress:
                self._runout_suppress_heads.discard(head)
            try:
                e_diff = gcode_move.last_position[3] - saved_e_last
                gcode_move.base_position[3] = saved_e_base + e_diff
                self.gcode.run_script_from_command('G90')
                self.gcode.run_script_from_command(
                    'G0 Z%.3f F600' % (saved_pos[2] + 3.0))
                self.gcode.run_script_from_command(
                    'G0 Y%.3f F12000' % saved_pos[1])
                self.gcode.run_script_from_command(
                    'G0 X%.3f F12000' % saved_pos[0])
                self.gcode.run_script_from_command(
                    'G0 Z%.3f F600' % (saved_pos[2] + 2.0))
                self.toolhead.wait_moves()
                if saved_absolute:
                    self.gcode.run_script_from_command('G90')
                self.gcode.run_script_from_command(
                    'G1 F%d' % (saved_speed * 60))
            except Exception as re:
                logging.info('[multiACE] [%s] pos restore failed: %s'
                             % (tag, re))
        return _wipe_ok

    def cmd_ACE_SWITCH(self, gcmd):
        target = gcmd.get_int('TARGET')
        autoload = gcmd.get_int('AUTOLOAD', 0)

        if self._swap_in_progress:
            self.log_always(self._t('msg.switch_in_progress'))
            return
        self._swap_in_progress = True

        try:
            self._perform_switch(gcmd, target, autoload)
        finally:
            self._swap_in_progress = False
            self._swap_saved_pos = None

    def _perform_switch(self, gcmd, target, autoload):

        self._refresh_ace_devices('switch')

        if not self._ace_devices:
            self.log_always(self._t('msg.no_ace_devices_detected'))
            return

        if not self._is_ace_present(target):
            self._usb_log.info('RETRY [switch] target=%d not present, starting retries', target)
            for retry in range(5):
                self._usb_stats['retries'] += 1
                self.reactor.pause(self.reactor.monotonic() + 1.0)
                self._refresh_ace_devices('switch_retry_%d' % (retry + 1))
                self._usb_log.info('RETRY [switch] attempt=%d/%d present=%d target=%d', retry + 1, 5, len(self._ace_present), target)
                if self._is_ace_present(target):
                    break
        if not self._is_ace_present(target):
            self.log_always(self._t('msg.ace_not_available_present',
                ace=self._disp(target), count=len(self._ace_present)))
            return

        switching_ace = target != self._active_device_index

        if not switching_ace and not autoload:
            self.log_always(self._t('msg.ace_already_active',
                ace=self._disp(target)))
            return

        if not switching_ace and autoload:
            logging.info(self._t('msg.ace_already_active_loading',
                ace=self._disp(target)))
        else:
            if target >= len(self._ace_devices) or not self._connected_per_ace.get(target, False):
                self.log_always(self._t('msg.ace_not_connected',
                    ace=self._disp(target)))
                return

            current_slot = self._feed_assist_per_ace.get(self._active_device_index, -1)
            preserve_print_fa = False
            if current_slot != -1 and self._auto_feed_enabled and not autoload:
                try:
                    cur_ext = self.toolhead.get_extruder()
                    print_head = getattr(cur_ext, 'extruder_index',
                                getattr(cur_ext, 'extruder_num', None))
                except Exception:
                    print_head = None
                if print_head is not None and self.head_uses_ace(print_head):
                    psrc = self._head_source.get(print_head)
                    if (psrc is not None
                            and psrc.get('ace_index') == self._active_device_index
                            and psrc.get('slot', -1) == current_slot):
                        preserve_print_fa = True
                        logging.info(
                            '[multiACE] switch: keeping print-head FA armed '
                            '(ACE %d slot %d head %d) across active switch to '
                            'ACE %d' % (self._active_device_index, current_slot,
                                        print_head, target))
                        self._fa_trace(
                            'switch: print-head FA preserved across active switch')
            if current_slot != -1 and not preserve_print_fa:
                try:
                    self._disarm_fa_for(self._active_device_index)
                except Exception as e:
                    logging.info('[multiACE] switch: stop_feed_assist failed: %s' % e)

                self.wait_ace_ready()

            if autoload:
                self.log_always(self._t('msg.switch_unloading_from',
                    ace=self._disp(self._active_device_index)))
                _target_gates = self._gate_status_per_ace.get(
                    target, [GATE_UNKNOWN] * 4)
                for gate in range(4):
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % gate, None)
                    filament_in_head = sensor and sensor.get_status(0)['filament_detected']
                    module, channel = self.EXTRUDER_MAP[gate]
                    no_replacement = (gate < len(_target_gates)
                                      and _target_gates[gate] == GATE_EMPTY)
                    if filament_in_head and no_replacement:
                        self.log_always(self._t('msg.switch_skip_unload_no_replacement',
                            head=self._disp(gate), ace=self._disp(target),
                            slot=self._disp(gate)))
                    elif filament_in_head:
                        logging.info(self._t('msg.switch_extruder_full_unload',
                            head=gate))
                        self.gcode.run_script_from_command(
                            "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=prepare" % (module, channel, gate))
                        self.gcode.run_script_from_command(
                            "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=doing" % (module, channel, gate))
                    else:
                        logging.info(self._t('msg.switch_extruder_skip_unload',
                            head=gate))
                machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
                if machine_state_manager is not None:
                    self._machine_state_after_feed_op()
                self.log_always(self._t('msg.switch_unload_complete'))

            logging.info(self._t('msg.switch_activating',
                ace=self._disp(target)))
            self._set_active_idx(target)
            self._push_rfid_info()

        if autoload:
            self.log_always(self._t('msg.switch_loading_from',
                ace=self._disp(target)))
            loaded_any = False

            for gate in range(4):
                sensor = self.printer.lookup_object(
                    'filament_motion_sensor e%d_filament' % gate, None)
                filament_in_head = sensor and sensor.get_status(0)['filament_detected']

                if filament_in_head:
                    logging.info(self._t('msg.switch_extruder_already_loaded',
                        head=gate))
                elif self.gate_status[self._ace_slot_for_head(gate)] == GATE_AVAILABLE:
                    module, channel = self.EXTRUDER_MAP[gate]
                    logging.info(self._t('msg.switch_extruder_loading',
                        head=gate))
                    self.gcode.run_script_from_command(
                        "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d LOAD=1" % (module, channel, gate))
                    loaded_any = True
                else:
                    logging.info(self._t('msg.switch_extruder_no_filament',
                        head=gate))

            if loaded_any:
                self.log_always(self._t('msg.switch_load_complete',
                    ace=self._disp(target)))
            else:
                self.log_always(self._t('msg.switch_nothing_to_load'))

        self._audit_state('SWITCH', {'target': target, 'autoload': autoload})

    def _get_heads_for_ace_slot(self, ace_index, slot):

        heads = []
        for head, source in self._head_source.items():
            if source and source['ace_index'] == ace_index and source['slot'] == slot:
                heads.append(head)
        return heads

    def _restore_head_source(self):

        saved = self.save_variables.allVariables.get(self.VARS_ACE_HEAD_SOURCE, None)
        if saved and isinstance(saved, dict):
            for head in range(4):
                key = str(head)
                if key in saved and saved[key]:
                    self._head_source[head] = saved[key]
                    logging.info('[multiACE] Restored head %d -> ACE %d / Slot %d' % (
                        head, saved[key]['ace_index'], saved[key]['slot']))

    def notify_external_load(self, module, channel, head):
        """Hook called from filament_feed_ace.py after a successful
        display-initiated FEED_AUTO LOAD or FEED_MANUAL FINISH. The
        load completed outside our LOAD_HEAD wrapper, so head_source
        either points at the previous failed target (load_failed=True)
        or is empty. Clear load_failed if it matches the active
        ACE/slot, otherwise best-effort populate head_source from the
        slot whose status just transitioned to loaded.
        """
        if self._in_internal_load_head:
            return
        if head is None or head < 0 or head >= 4:
            return
        if not self.head_uses_ace(head):
            return
        ace_index = self._active_device_index
        src = self._head_source.get(head)
        if src is not None and src.get('load_failed'):
            src['load_failed'] = False
            try:
                self._save_head_source()
            except Exception as e:
                logging.info('[multiACE] notify_external_load save failed: %s' % e)
            self._fa_log.info(
                '[load-hook] external load CONFIRMED: head=%d ace=%s slot=%s' % (
                    head, src.get('ace_index'), src.get('slot')))
            return
        if src is not None:
            return
        target_slot = self._ace_slot_for_head(head)
        info = self._info_per_ace.get(ace_index) or {}
        slots = info.get('slots') or []
        slot_info = (slots[target_slot]
                     if isinstance(target_slot, int) and 0 <= target_slot < len(slots)
                     else {})
        self._head_source[head] = {
            'ace_index': ace_index,
            'slot': target_slot,
            'type': slot_info.get('type', ''),
            'color': self.rgb2hex(*slot_info.get('color', (0, 0, 0))),
            'brand': slot_info.get('brand', ''),
        }
        try:
            self._save_head_source()
        except Exception as e:
            logging.info('[multiACE] notify_external_load save failed: %s' % e)
        self._fa_log.info(
            '[load-hook] external load resolved: head=%d -> ace=%d slot=%d '
            '(_ace_slot_for_head; module=%s channel=%s)' % (
                head, ace_index, target_slot, module, channel))

    def _save_head_source(self):

        save_data = {}
        for head in range(4):
            save_data[str(head)] = self._head_source[head]

        value_str = (json.dumps(save_data)
                     .replace(': true', ': True')
                     .replace(': false', ': False')
                     .replace(': null', ': None'))
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE='%s'"
            % (self.VARS_ACE_HEAD_SOURCE, value_str))

    def head_is_manual(self, head):
        try:
            return bool(self.head_manual.get(int(head), False))
        except (TypeError, ValueError):
            return False

    def head_is_feeder(self, head):
        if getattr(self, '_ace_mode', 'multi') != 'head':
            return False
        try:
            return bool(self.head_feeder.get(int(head), False))
        except (TypeError, ValueError):
            return False

    def head_uses_ace(self, head):
        if self.head_is_manual(head):
            return False
        if getattr(self, '_ace_mode', 'multi') == 'head':
            return not self.head_is_feeder(head)
        return True

    def head_ace_for(self, head):
        if getattr(self, '_ace_mode', 'multi') != 'head':
            try:
                return int(head)
            except (TypeError, ValueError):
                return 0
        try:
            return int(self.head_ace.get(int(head), int(head)))
        except (TypeError, ValueError):
            return 0

    def _ensure_active_ace_for_head(self, head):
        if getattr(self, '_ace_mode', 'multi') != 'head':
            return self._active_device_index
        if not self.head_uses_ace(head):
            return self._active_device_index
        src = self._head_source.get(head)
        if src is not None and isinstance(src.get('ace_index'), int):
            target = int(src['ace_index'])
        else:
            target = self.head_ace_for(head)
        if target == self._active_device_index:
            return target
        if target < 0 or target >= len(self._ace_devices) \
                or not self._connected_per_ace.get(target, False):
            logging.info(
                '[multiACE] head %d wired to ACE %d but not connected - '
                'keeping active ACE %d' % (head, target, self._active_device_index))
            return self._active_device_index
        self._set_active_idx(target)
        logging.info(
            '[multiACE] head %d -> active ACE %d (head-mode per-head wiring)'
            % (head, target))
        return target

    def _head_for_ace(self, ace_idx):
        if getattr(self, '_ace_mode', 'multi') != 'head':
            return None
        for h in range(4):
            if self.head_uses_ace(h) and self.head_ace_for(h) == ace_idx:
                return h
        return None

    def _display_head_for_slot(self, ace_idx, slot_idx, is_active):
        if getattr(self, '_ace_mode', 'multi') == 'head':
            h = self._head_for_ace(ace_idx)
            if h is None or not self.head_uses_ace(h):
                return None
            show = self._first_loaded_slot_for_ace(ace_idx)
            show = show if show is not None else h
            return h if slot_idx == show else None
        if is_active and slot_idx < 4 and self.head_uses_ace(slot_idx):
            return slot_idx
        return None

    def _ensure_extruder_change_handler(self):
        if self._extruder_handler_registered:
            return
        self.printer.register_event_handler(
            'extruder:activate_extruder', self._on_extruder_change)
        self._extruder_handler_registered = True

    def _head_is_loaded(self, head):
        """True if the head currently has filament - either an ACE source
        (head_source set) or filament physically at the toolhead sensor (covers
        a hand-loaded manual head, which has no head_source)."""
        if self._head_source.get(head) is not None:
            return True
        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
        try:
            return bool(sensor and sensor.get_status(0).get('filament_detected'))
        except Exception:
            return False

    cmd_ACE_SET_HEAD_MANUAL_help = (
        '[multiACE] Toggle manual/TPU bypass for a head. '
        'Usage: ACE_SET_HEAD_MANUAL HEAD=0..3 ENABLE=0|1. '
        'When enabled: no ACE feed/retract/feed-assist/RFID for that head '
        '(load it by hand; the head sensor stays active). Persisted.')

    def cmd_ACE_SET_HEAD_MANUAL(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        enable = gcmd.get_int('ENABLE', minval=0, maxval=1)
        was_manual = self.head_is_manual(head)
        if bool(enable) != was_manual and self._head_is_loaded(head):
            raise gcmd.error(
                self._t('msg.head_manual_loaded', head=self._disp(head)))
        self.head_manual[head] = bool(enable)
        if self.save_variables:
            self._save_head_manual()
        if enable and not was_manual:
            self._clear_filament_display(head)
        self.log_always(
            '[multiACE] head %d manual mode %s'
            % (head, 'ENABLED' if enable else 'disabled'))

    cmd_ACE_SET_HEAD_FEEDER_help = (
        '[multiACE] Toggle stock-feeder mode for a head (head mode only). '
        'Usage: ACE_SET_HEAD_FEEDER HEAD=0..3 ENABLE=0|1. '
        'When enabled the head loads/unloads via its stock side feeder and the '
        'ACE never touches it; when disabled the head is ACE-driven. Persisted.')

    def cmd_ACE_SET_HEAD_FEEDER(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        enable = gcmd.get_int('ENABLE', minval=0, maxval=1)
        was_feeder = bool(self.head_feeder.get(head, False))
        if bool(enable) != was_feeder and self._head_is_loaded(head):
            raise gcmd.error(
                self._t('msg.head_manual_loaded', head=self._disp(head)))
        if not enable and was_feeder \
                and getattr(self, '_ace_mode', 'multi') == 'head':
            my_ace = int(self.head_ace.get(head, head))
            for other in range(4):
                if other == head:
                    continue
                if self.head_manual.get(other, False) \
                        or self.head_feeder.get(other, False):
                    continue
                if int(self.head_ace.get(other, other)) == my_ace:
                    raise gcmd.error(
                        '[multiACE] head %d is wired to ACE %d, which head %d '
                        'already uses - one ACE feeds exactly one head. '
                        'Rewire head %d first (ACE_SET_HEAD_ACE HEAD=%d '
                        'ACE=<free>), then disable feeder mode.'
                        % (self._disp(head), self._disp(my_ace),
                           self._disp(other), self._disp(head), head))
        self.head_feeder[head] = bool(enable)
        if self.save_variables:
            self._save_head_feeder()
        if enable and not was_feeder:
            self._clear_filament_display(head)
        self.log_always(
            '[multiACE] head %d feeder mode %s'
            % (head, 'ENABLED' if enable else 'disabled'))

    cmd_ACE_SET_HEAD_ACE_help = (
        '[multiACE] Set which ACE feeds an ACE head (head mode). '
        'Usage: ACE_SET_HEAD_ACE HEAD=0..3 ACE=0..3. '
        'Each ACE head is wired to exactly one ACE and can only load/swap that '
        "ACE's slots. Persisted.")

    def cmd_ACE_SET_HEAD_ACE(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        ace_idx = gcmd.get_int('ACE', minval=0, maxval=3)
        if int(self.head_ace.get(head, head)) != ace_idx \
                and self._head_is_loaded(head):
            raise gcmd.error(
                self._t('msg.head_manual_loaded', head=self._disp(head)))
        _swapped = None
        for other in range(4):
            if other == head:
                continue
            if self.head_manual.get(other, False) \
                    or self.head_feeder.get(other, False):
                continue
            if int(self.head_ace.get(other, other)) == ace_idx:
                if self._head_is_loaded(other):
                    raise gcmd.error(
                        '[multiACE] ACE %d is wired to head %d, which is '
                        'LOADED - unload head %d first, then rewire.'
                        % (self._disp(ace_idx), self._disp(other),
                           self._disp(other)))
                self.head_ace[other] = int(self.head_ace.get(head, head))
                _swapped = other
                break
        self.head_ace[head] = ace_idx
        if self.save_variables:
            self._save_head_ace()
        if _swapped is not None:
            self.log_always(
                '[multiACE] head %d -> ACE %d (swapped: head %d -> ACE %d)'
                % (head, self._disp(ace_idx), self._disp(_swapped),
                   self._disp(self.head_ace[_swapped])))
        else:
            self.log_always(
                '[multiACE] head %d -> ACE %d'
                % (head, self._disp(ace_idx)))

    cmd_ACE_SET_PURGE_help = (
        '[multiACE] Set the swap/load flush length (mm) for the next '
        'flush(es). Usage: ACE_SET_PURGE LENGTH=<mm>  or  ACE_SET_PURGE '
        'RESET=1 to fall back to the swap_purge_length config value. '
        'Intended for multiACE Pro to set per-colour-pair purge from the '
        'slicer. LENGTH=0 = use the stock default (80mm).')

    def cmd_ACE_SET_PURGE(self, gcmd):
        if gcmd.get_int('RESET', 0):
            self._purge_length_override = None
            self.log_always('[multiACE] purge length override cleared '
                            '(using swap_purge_length=%d)'
                            % self.swap_purge_length)
            return
        length = gcmd.get_int('LENGTH', minval=0, maxval=200)
        self._purge_length_override = length
        self.log_always('[multiACE] purge length override set to %d mm%s'
                        % (length, ' (stock default)' if length == 0 else ''))

    def _restore_head_manual(self):
        saved = self.save_variables.allVariables.get(
            self.VARS_ACE_HEAD_MANUAL, None)
        if saved and isinstance(saved, dict):
            for head in range(4):
                key = str(head)
                if key in saved:
                    self.head_manual[head] = bool(saved[key])
                    if self.head_manual[head]:
                        logging.info(
                            '[multiACE] Restored head %d -> manual mode'
                            % head)

    def _save_head_manual(self):
        save_data = {str(h): bool(self.head_manual[h]) for h in range(4)}
        value_str = (json.dumps(save_data)
                     .replace(': true', ': True')
                     .replace(': false', ': False'))
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE='%s'"
            % (self.VARS_ACE_HEAD_MANUAL, value_str))

    def _restore_head_feeder(self):
        saved = self.save_variables.allVariables.get(
            self.VARS_ACE_HEAD_FEEDER, None)
        if saved and isinstance(saved, dict):
            for head in range(4):
                key = str(head)
                if key in saved:
                    self.head_feeder[head] = bool(saved[key])
                    if self.head_feeder[head]:
                        logging.info(
                            '[multiACE] Restored head %d -> feeder mode' % head)
            return
        legacy = self.save_variables.allVariables.get(self.VARS_ACE_HEAD, None)
        if legacy is not None:
            for head in range(4):
                self.head_feeder[head] = (head != self._ace_head)
            logging.info(
                '[multiACE] Migrated legacy head mode (ACE head %d) -> per-head '
                'feeder flags' % self._ace_head)

    def _save_head_feeder(self):
        save_data = {str(h): bool(self.head_feeder[h]) for h in range(4)}
        value_str = (json.dumps(save_data)
                     .replace(': true', ': True')
                     .replace(': false', ': False'))
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE='%s'"
            % (self.VARS_ACE_HEAD_FEEDER, value_str))

    def _restore_head_ace(self):
        saved = self.save_variables.allVariables.get(
            self.VARS_ACE_HEAD_ACE, None)
        if saved and isinstance(saved, dict):
            for head in range(4):
                key = str(head)
                if key in saved:
                    try:
                        a = int(saved[key])
                        if 0 <= a <= 3:
                            self.head_ace[head] = a
                    except (TypeError, ValueError):
                        pass
            return
        legacy = self.save_variables.allVariables.get(self.VARS_ACE_HEAD, None)
        if legacy is not None:
            self.head_ace[self._ace_head] = self.HEAD_MODE_ACE
            logging.info(
                '[multiACE] Migrated legacy combiner head %d -> ACE %d'
                % (self._ace_head, self.HEAD_MODE_ACE))

    def _save_head_ace(self):
        save_data = {str(h): int(self.head_ace[h]) for h in range(4)}
        value_str = json.dumps(save_data)
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=%s VALUE='%s'"
            % (self.VARS_ACE_HEAD_ACE, value_str))

    def _ensure_ace_available(self, ace_index):

        if (0 <= ace_index < len(self._ace_devices)
                and self._connected_per_ace.get(ace_index, False)):
            return True
        for attempt in range(5):
            self._refresh_ace_devices('ensure_%d' % (attempt + 1))
            if self._is_ace_present(ace_index):
                if attempt > 0:
                    self._usb_log.info('ENSURE ace=%d found after %d retries', ace_index, attempt)
                return True
            self._usb_stats['retries'] += 1
            self.reactor.pause(self.reactor.monotonic() + 1.0)
        self._usb_log.warning('ENSURE ace=%d FAILED after 5 attempts (present %d)', ace_index, len(self._ace_present))
        return False

    def _switch_ace_for_head(self, head_index):
        source = self._head_source.get(head_index)
        if not source:
            return False

        target_ace = source['ace_index']

        if target_ace == self._active_device_index:
            self._audit_state('SWITCH_AUTO_NOOP', {
                'head': head_index, 'target_ace': target_ace,
                'reason': 'already_active'})
            return True

        if target_ace >= len(self._ace_devices):
            self.log_always(self._t('msg.ace_out_of_range_for_head',
                ace=self._disp(target_ace), head=self._disp(head_index)))
            self._audit_state('SWITCH_AUTO_FAILED', {
                'head': head_index, 'target_ace': target_ace,
                'reason': 'ace_out_of_range'})
            return False

        if not self._connected_per_ace.get(target_ace, False):
            self.log_error(self._t('msg.ace_not_connected_for_head',
                ace=self._disp(target_ace), head=self._disp(head_index)))
            self._audit_state('SWITCH_AUTO_FAILED', {
                'head': head_index, 'target_ace': target_ace,
                'reason': 'not_connected'})
            return False

        logging.info(self._t('msg.activating_ace_for_head',
            ace=self._disp(target_ace), head=self._disp(head_index)))

        self._set_active_idx(target_ace)

        self._audit_state('SWITCH_AUTO', {
            'head': head_index, 'target_ace': target_ace})
        return True

    def _on_extruder_change(self):
        self._fa_trace('_on_extruder_change fired; gate=%s context=%s active_ace=%d'
                       % (self._auto_feed_enabled, self._fa_context, self._active_device_index))
        if not any(self._head_source[h] for h in range(4)):
            return

        try:
            extruder = self.toolhead.get_extruder()
            head_index = getattr(extruder, 'extruder_index',
                        getattr(extruder, 'extruder_num', None))
        except Exception:
            head_index = None

        if head_index is None:
            self._audit_state('SWITCH_AUTO', {
                'head': None,
                'reason': 'no_head_index',
            })
            return

        source = self._head_source.get(head_index)
        if source is None:
            self._audit_state('SWITCH_AUTO', {
                'head': head_index,
                'reason': 'no_head_source',
            })
            return

        if not self.head_uses_ace(head_index):
            self._fa_trace('_on_extruder_change: head %d does not use ACE '
                           '(feeder/manual) - skip FA' % head_index)
            return

        bg = self.printer.lookup_object('ace_bg_swap', None)
        if bg is not None and bg.is_busy(head_index):
            self._fa_trace('_on_extruder_change: head %d has a RUNNING bg '
                           'unload - skip FA arm (swap will wait + re-arm on '
                           'load)' % head_index)
            return

        target_ace = source['ace_index']
        target_slot = source['slot']

        if target_ace >= len(self._ace_devices) or not self._connected_per_ace.get(target_ace, False):
            self._audit_state('SWITCH_AUTO_FAILED', {
                'head': head_index,
                'target_ace': target_ace,
                'reason': 'not_connected',
            })
            self.log_error(self._t('msg.target_ace_not_connected_t',
                head=self._disp(head_index), ace=self._disp(target_ace)))
            return

        prev_active = self._active_device_index
        prev_slot = self._feed_assist_per_ace.get(prev_active, -1)

        if prev_active != target_ace and prev_slot != -1:
            try:
                self._disarm_fa_for(prev_active)
            except Exception as e:
                logging.info('[multiACE] stop_feed_assist on ACE %d failed: %s' % (prev_active, e))

        if prev_active != target_ace:
            self._set_active_idx(target_ace)

        current_target_slot = self._feed_assist_per_ace.get(target_ace, -1)
        if current_target_slot != target_slot:

            target_ace_local = target_ace
            target_slot_local = target_slot
            head_index_local = head_index
            def _deferred_fa_start(eventtime):
                if not self._auto_feed_enabled:
                    self._fa_trace(
                        '_on_extruder_change deferred start SUPPRESSED '
                        '(gate closed): head=%d idx=%d slot=%d'
                        % (head_index_local, target_ace_local, target_slot_local))
                    return self.reactor.NEVER

                try:
                    cur_ext = self.toolhead.get_extruder()
                    cur_head = getattr(cur_ext, 'extruder_index',
                                       getattr(cur_ext, 'extruder_num', None))
                except Exception:
                    cur_head = None
                if cur_head != head_index_local:
                    self._fa_trace(
                        '_on_extruder_change deferred start SUPPRESSED '
                        '(stale head): expected=%d actual=%s'
                        % (head_index_local, cur_head))
                    return self.reactor.NEVER
                try:
                    self._arm_fa_for(target_ace_local, target_slot_local)
                except Exception as e:
                    logging.info(
                        '[multiACE] deferred start_feed_assist ACE %d slot %d failed: %s'
                        % (target_ace_local, target_slot_local, e))
                return self.reactor.NEVER
            self.reactor.register_timer(
                _deferred_fa_start, self.reactor.monotonic() + 0.1)

        self._audit_state('SWITCH_AUTO', {
            'head': head_index,
            'target_ace': target_ace,
            'target_slot': target_slot,
            'prev_active': prev_active,
            'prev_slot': prev_slot,
        })

        now = time.monotonic()
        gap_ms = None
        if self._last_switch_auto_ts is not None:
            gap_ms = int((now - self._last_switch_auto_ts) * 1000)
        self._last_switch_auto_ts = now
        self._telemetry('SWITCH', {
            'head': head_index,
            'prev_ace': prev_active,
            'prev_slot': prev_slot,
            'target_ace': target_ace,
            'target_slot': target_slot,
            'gap_ms_since_last_switch': gap_ms,
            'print_active': self._fa_context == 'print',
            'ace_changed': prev_active != target_ace,
        })

    def _wait_bg_op(self, head, gcmd=None):
        bg = self.printer.lookup_object('ace_bg_swap', None)
        if bg is None:
            return
        try:
            busy = bg.is_busy(head)
        except Exception:
            return
        if not busy:
            return
        self.log_always('[multiACE] head %d: waiting for the background '
                        'unload to finish before the feed op'
                        % self._disp(head))
        deadline = self.reactor.monotonic() + 300.
        while self.reactor.monotonic() < deadline:
            try:
                if not bg.is_busy(head):
                    self.log_always('[multiACE] head %d: background unload '
                                    'finished - continuing'
                                    % self._disp(head))
                    self._rearm_fa_after_bg_wait(head)
                    return
            except Exception:
                return
            self.reactor.pause(self.reactor.monotonic() + 0.5)
        msg = ('[multiACE] head %d: background unload did not finish '
               'within 300s' % self._disp(head))
        if gcmd is not None:
            raise gcmd.error(msg)
        self.log_error(msg)

    def _rearm_fa_after_bg_wait(self, head):
        try:
            if not self._auto_feed_enabled:
                return
            ext = self.toolhead.get_extruder()
            cur_head = getattr(ext, 'extruder_index',
                               getattr(ext, 'extruder_num', None))
            if cur_head != head or not self.head_uses_ace(head):
                return
            source = self._head_source.get(head)
            if not source:
                return
            self._ensure_active_ace_for_head(head)
            self._arm_fa_for(source['ace_index'], source['slot'])
            self._fa_trace('FA re-armed after bg-op wait: head=%d ace=%d '
                           'slot=%d' % (head, source['ace_index'],
                                        source['slot']))
        except Exception as e:
            logging.info('[multiACE] FA re-arm after bg wait failed: %s' % e)

    def _bg_pick_flow_check(self, head, anti_ooze):
        self._bg_load_unverified.discard(head)
        _bg = self.printer.lookup_object('ace_bg_swap', None)
        gate_on = bool(getattr(_bg, 'pick_gate', False))
        _deficit = getattr(self, '_bg_prime_deficit', {}).pop(head, None)
        try:
            ext = self.toolhead.get_extruder()
            heater = ext.get_heater()
            if not bool(getattr(heater, 'can_extrude', True)):
                logging.info('[multiACE] [pick-check] head %d skipped: below '
                             'min_extrude_temp' % head)
                if _deficit is not None:
                    self._bg_prime_deficit[head] = _deficit
                return
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            def _detected():
                try:
                    return bool(sensor is not None and
                                sensor.get_status(0).get('filament_detected'))
                except Exception:
                    return None
            try:
                homed = self.toolhead.get_status(
                    self.reactor.monotonic()).get('homed_axes', '')
            except Exception:
                homed = ''
            if not all(a in homed for a in 'xyz'):
                self._bg_load_unverified.add(head)
                if _deficit is not None:
                    self._bg_prime_deficit[head] = _deficit
                self.log_always('[multiACE] [pick-check] head %d: axes not '
                                'homed (%s) - skipped, re-armed for the next '
                                'arrival (home first for an idle test)'
                                % (self._disp(head), homed or 'none'))
                return
            sensor_before = _detected()
            if sensor_before is False:
                logging.info('[multiACE] [pick-check] head %d: sensor reads '
                             'ABSENT on a bg-loaded head - skipping the push'
                             % head)
                self._wiggle_log.info(
                    'pickcheck head=%d verdict=SENSOR_ABSENT gate=%s'
                    % (head, gate_on))
                return 'sensor_absent' if gate_on else None
            coil = None
            try:
                coil = ext.binding_probe.sensor
            except Exception:
                logging.info('[multiACE] [pick-check] inductance_coil not '
                             'found - sensor-only check')
            src = self._head_source.get(head) or {}
            self.log_always('[multiACE] [pick-check] head %d: verifying the '
                            'background load (short extrude at the discard '
                            'position)' % self._disp(head))

            gcode_move = self.printer.lookup_object('gcode_move')
            saved_pos = self.toolhead.get_position()[:3]
            saved_speed = gcode_move.speed
            saved_absolute = gcode_move.absolute_coord
            saved_e_base = gcode_move.base_position[3]
            saved_e_last = gcode_move.last_position[3]
            _added_suppress = head not in self._runout_suppress_heads
            self._runout_suppress_heads.add(head)
            coil_start = coil_min = coil_max = None
            sensor_after = None
            try:
                self.gcode.run_script_from_command('G91')
                self.gcode.run_script_from_command('G1 Z2 F600')
                self.gcode.run_script_from_command('G90')
                self.gcode.run_script_from_command(
                    'MOVE_TO_DISCARD_FILAMENT_POSITION')
                self.toolhead.wait_moves()

                self.gcode.run_script_from_command('M83')
                def _measure(push_mm):
                    c0 = mn = mx = None
                    if coil is not None:
                        try:
                            c0 = coil.get_coil_freq()
                            mn = mx = c0
                        except Exception:
                            c0 = None
                    self.gcode.run_script_from_command(
                        'G1 E%.2f F%d' % (push_mm, PICK_CHECK_PUSH_FEEDRATE))
                    self.reactor.pause(self.reactor.monotonic() + 0.5)
                    if coil is not None and c0 is not None:
                        for _i in range(PICK_CHECK_COIL_SAMPLES):
                            try:
                                f = coil.get_coil_freq()
                                mn = min(mn, f)
                                mx = max(mx, f)
                            except Exception:
                                break
                            self.reactor.pause(self.reactor.monotonic()
                                               + PICK_CHECK_COIL_INTERVAL)
                    self.toolhead.wait_moves()
                    dip = (c0 - mn) if c0 is not None else None
                    up = (mx - c0) if c0 is not None else None
                    return c0, mn, mx, dip, up
                if _deficit is not None:
                    push = max(float(_deficit) + PICK_CHECK_FLOW_PUSH,
                               PICK_CHECK_MIN_PUSH)
                    self.log_always(
                        '[multiACE] [pick-check] head %d: topping up the '
                        'cut-short background prime (%d mm)'
                        % (self._disp(head), int(float(_deficit))))
                else:
                    push = max(float(anti_ooze) + PICK_CHECK_FLOW_PUSH,
                               PICK_CHECK_MIN_PUSH)
                coil_start, coil_min, coil_max, coil_delta, coil_up = \
                    _measure(push)
                if coil_up is not None and coil_up >= PICK_TURBULENCE_UPSWING:
                    self.log_always(
                        '[multiACE] [pick-check] head %d: coil TURBULENCE '
                        '(up=%.0f dip=%s) - settling %.1fs + re-measuring'
                        % (self._disp(head), coil_up, coil_delta,
                           PICK_TURBULENCE_SETTLE))
                    self.reactor.pause(self.reactor.monotonic()
                                       + PICK_TURBULENCE_SETTLE)
                    coil_start, coil_min, coil_max, coil_delta, coil_up = \
                        _measure(PICK_CHECK_MIN_PUSH)
                regripped = False
                ace_pushed = None
                _turbulent = (coil_up is not None
                              and coil_up >= PICK_TURBULENCE_UPSWING)
                if (gate_on and coil_delta is not None
                        and (coil_delta < PICK_CHECK_COIL_THRESHOLD
                             or _turbulent)):
                    self.log_always(
                        '[multiACE] [pick-check] head %d: NO FLOW '
                        '(delta=%s) - re-gripping %d mm (+ACE push) and '
                        're-measuring'
                        % (self._disp(head), coil_delta,
                           int(PICK_GATE_REGRIP)))
                    n_idx = src.get('ace_index')
                    n_slot = src.get('slot')
                    if (_bg is not None and n_idx is not None
                            and n_slot is not None):
                        try:
                            n_len = (PICK_GATE_ACE_PUSH_V2
                                     if self._is_v2_idx(n_idx)
                                     else PICK_GATE_ACE_PUSH_V1)
                            self._feed_assist_per_ace[n_idx] = -1
                            _bg._ace_send(self, n_idx, {
                                'method': 'stop_feed_assist',
                                'params': {'index': n_slot}})
                            ace_pushed = False
                            for _na in range(PICK_GATE_ACE_PUSH_RETRIES):
                                nresp = _bg._ace_send(self, n_idx, {
                                    'method': 'feed_filament',
                                    'params': {
                                        'index': n_slot,
                                        'length': int(n_len),
                                        'speed': int(
                                            PICK_GATE_ACE_PUSH_SPEED)}})
                                if not _bg._resp_rejected(nresp):
                                    ace_pushed = True
                                    break
                                if _na < PICK_GATE_ACE_PUSH_RETRIES - 1:
                                    self.reactor.pause(
                                        self.reactor.monotonic()
                                        + PICK_GATE_ACE_PUSH_RETRY_DELAY)
                        except Exception as ne:
                            logging.info('[multiACE] [pick-check] ACE push '
                                         'failed (extruder-only re-grip): '
                                         '%s' % ne)
                    self.gcode.run_script_from_command(
                        'G1 E%.2f F%d' % (PICK_GATE_REGRIP,
                                          PICK_GATE_REGRIP_FEEDRATE))
                    self.toolhead.wait_moves()
                    if ace_pushed is not None:
                        try:
                            _bg._ace_send(self, n_idx, {
                                'method': 'stop_feed_filament',
                                'params': {'index': n_slot}})
                            self._arm_fa_for(n_idx, n_slot)
                        except Exception as ne:
                            logging.info('[multiACE] [pick-check] FA re-arm '
                                         'after ACE push failed: %s' % ne)
                    push = push + PICK_GATE_REGRIP + PICK_CHECK_MIN_PUSH
                    regripped = True
                    coil_start, coil_min, coil_max, coil_delta, coil_up = \
                        _measure(PICK_CHECK_MIN_PUSH)
                self.reactor.pause(self.reactor.monotonic() + 0.5)
                sensor_after = _detected()
                if anti_ooze > 0:
                    self.gcode.run_script_from_command(
                        'G1 E-%.2f F1500' % float(anti_ooze))
                    self.toolhead.wait_moves()
                if BG_PICK_WIPE:
                    try:
                        self.gcode.run_script_from_command(
                            'INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2')
                        self.toolhead.wait_moves()
                        logging.info('[multiACE] [pick-check] nozzle wipe '
                                     'done (head %d)' % self._disp(head))
                    except Exception as we:
                        logging.info('[multiACE] [pick-check] nozzle wipe '
                                     'failed (continuing): %s' % we)
            finally:
                if _added_suppress:
                    self._runout_suppress_heads.discard(head)
                try:
                    e_diff = gcode_move.last_position[3] - saved_e_last
                    gcode_move.base_position[3] = saved_e_base + e_diff
                    self.gcode.run_script_from_command('G90')
                    self.gcode.run_script_from_command(
                        'G0 Z%.3f F600' % (saved_pos[2] + 3.0))
                    self.gcode.run_script_from_command(
                        'G0 Y%.3f F12000' % saved_pos[1])
                    self.gcode.run_script_from_command(
                        'G0 X%.3f F12000' % saved_pos[0])
                    self.gcode.run_script_from_command(
                        'G0 Z%.3f F600' % (saved_pos[2] + 2.0))
                    self.toolhead.wait_moves()
                    if saved_absolute:
                        self.gcode.run_script_from_command('G90')
                    self.gcode.run_script_from_command(
                        'G1 F%d' % (saved_speed * 60))
                except Exception as re:
                    logging.info('[multiACE] [pick-check] pos restore '
                                 'failed: %s' % re)

            _turbulent = (coil_up is not None
                          and coil_up >= PICK_TURBULENCE_UPSWING)
            coil_verdict = ('FLOW' if coil_delta is not None
                            and coil_delta >= PICK_CHECK_COIL_THRESHOLD
                            and not _turbulent
                            else 'NO_FLOW' if coil_delta is not None
                            else 'NO_COIL')
            sens_verdict = ('STUCK_OR_GONE' if sensor_after is False
                            else 'PRESENT' if sensor_after else 'UNKNOWN')
            _line = ('pickcheck head=%d ace=%s slot=%s anti_ooze=%.1f '
                     'push=%.1f prime_topup=%s regrip=%s ace_push=%s '
                     'coil_start=%s '
                     'coil_min=%s coil_max=%s coil_delta=%s coil_up=%s '
                     'turbulent=%s thr=%d '
                     'coil_verdict=%s sensor_before=%s sensor_after=%s '
                     'sensor_verdict=%s gate=%s'
                     % (head, src.get('ace_index'), src.get('slot'),
                        float(anti_ooze), push,
                        ('%.0f' % float(_deficit)) if _deficit is not None
                        else '-', regripped, ace_pushed,
                        coil_start, coil_min, coil_max, coil_delta, coil_up,
                        _turbulent,
                        PICK_CHECK_COIL_THRESHOLD, coil_verdict,
                        sensor_before, sensor_after, sens_verdict, gate_on))
            self._wiggle_log.info(_line)
            logging.info('[multiACE] [pick-check] %s' % _line)
            if gate_on and coil_verdict == 'NO_FLOW':
                self.log_always(
                    '[multiACE] [pick-check] head %d: NO FLOW persists '
                    'after the re-grip (delta=%s) - escalating to pause'
                    % (self._disp(head), coil_delta))
                return 'no_flow'
            self.log_always('[multiACE] [pick-check] head %d: coil %s '
                            '(delta=%s) / sensor %s%s'
                            % (self._disp(head), coil_verdict, coil_delta,
                               sens_verdict,
                               ' - recovered by re-grip' if regripped
                               else ''))
            return None
        except Exception as e:
            logging.exception('[multiACE] [pick-check] head %d failed '
                              '(log-only, swallowed)' % head)
            try:
                self.log_always('[multiACE] [pick-check] head %d: check '
                                'errored (%s) - log-only, print continues'
                                % (self._disp(head), e))
            except Exception:
                pass

    def _tipform_material_for(self, head):
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None:
                types = (ptc.get_status(None) or {}).get('filament_type')
                if types and 0 <= head < len(types):
                    t = (types[head] or '').strip()
                    if t and t.upper() != 'NONE':
                        return t
        except Exception:
            pass
        src = self._head_source.get(head) or {}
        return (src.get('type') or '').strip()

    def _tipform_vendor_for(self, head):
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None:
                vendors = (ptc.get_status(None) or {}).get('filament_vendor')
                if vendors and 0 <= head < len(vendors):
                    v = (vendors[head] or '').strip()
                    if v and v.upper() != 'NONE':
                        return v
        except Exception:
            pass
        src = self._head_source.get(head) or {}
        return (src.get('brand') or '').strip()

    def tipform_table_for(self, material, vendor=None, soft=False):
        tf = self.printer.lookup_object('ace_tipform', None)
        if tf is None:
            return None
        try:
            return tf.table_for(material, vendor=vendor, soft=soft)
        except Exception:
            logging.exception('[multiACE] tipform table lookup failed')
            return None

    def tipform_unload_temp_for(self, head, soft=False):
        tf = self.printer.lookup_object('ace_tipform', None)
        if tf is None or not hasattr(tf, 'unload_temp_for'):
            return None
        try:
            return tf.unload_temp_for(self._tipform_material_for(head),
                                      vendor=self._tipform_vendor_for(head),
                                      soft=bool(soft))
        except Exception:
            logging.exception('[multiACE] tipform unload-temp lookup failed')
            return None

    def _tipform_send(self, ace_idx, request, timeout=5.0):
        done = [None]

        def _cb(self, response):
            done[0] = response if response is not None else {}
        try:
            self.send_request_to(ace_idx, request, _cb)
        except Exception:
            return None
        deadline = self.reactor.monotonic() + timeout
        while done[0] is None and self.reactor.monotonic() < deadline:
            self.reactor.pause(self.reactor.monotonic() + 0.05)
        return done[0]

    def _tipform_rejected(self, resp):
        if not resp:
            return True
        if resp.get('code', -1) != 0:
            return True
        return str(resp.get('msg', '')).strip().upper() == 'FORBIDDEN'

    def _run_tipform(self, head, temp, soft, nozzle_diameter):
        material = self._tipform_material_for(head)
        vendor = self._tipform_vendor_for(head)
        table = self.tipform_table_for(material, vendor=vendor, soft=bool(soft))
        if table is None:
            tf = self.printer.lookup_object('ace_tipform', None)
            if tf is not None and getattr(tf, 'mode', 'stock') == 'custom':
                self.log_always(
                    '[multiACE] head %d: tip form STOCK (custom mode, no '
                    'table for %s%r; tables: %s)'
                    % (self._disp(head),
                       ('%s ' % vendor) if vendor else '',
                       material or '?',
                       ', '.join(sorted(getattr(tf, 'tables', {}).keys()))
                       or 'none'))
            self.gcode.run_script_from_command(
                "INNER_FILAMENT_UNLOAD TEMP=%d SOFT=%d NOZZLE_DIAMETER=%f\r\n"
                % (temp, soft, nozzle_diameter))
            return
        _tf_desc = (('%s %s' % (vendor, material)) if vendor and material
                    else (material.lower() if material else 'default'))
        _tf_line = ('[multiACE] head %d: custom tip form (%s, %d tokens)'
                    % (self._disp(head), _tf_desc, len(table)))
        self.log_always(_tf_line)
        logging.info(_tf_line)
        run = self.gcode.run_script_from_command
        src = self._head_source.get(head) or {}
        ace_idx = src.get('ace_index')
        if not isinstance(ace_idx, int):
            ace_idx = self._active_device_index
        slot = src.get('slot')
        if not isinstance(slot, int) or not 0 <= slot <= 3:
            slot = self._ace_slot_for_head(head)
        is_v2 = False
        try:
            is_v2 = bool(self._is_v2_idx(ace_idx))
        except Exception:
            pass
        unwind_speed = 80
        try:
            unwind_speed = int(self.get_retract_speed(ace_idx))
        except Exception:
            pass
        saved_rev_assist = getattr(self, '_v2_active_rev_assist', False)
        if is_v2:
            self._v2_active_rev_assist = False
        fwd_armed = False

        def _tf_fa_start():
            try:
                if self._v2_get_slot_status(ace_idx, slot) \
                        in V2_FA_RUNNING_STATES:
                    self._feed_assist_per_ace[ace_idx] = slot
                    return True
            except Exception:
                pass
            for _a in range(3):
                resp = self._tipform_send(ace_idx, {
                    'method': 'start_feed_assist', 'params': {'index': slot}})
                if not self._tipform_rejected(resp):
                    self._feed_assist_per_ace[ace_idx] = slot
                    return True
                self.reactor.pause(self.reactor.monotonic() + 1.0)
            return False

        def _tf_unwind(ln):
            for _a in range(3):
                self._tipform_send(ace_idx, {
                    'method': 'stop_feed_assist', 'params': {'index': slot}})
                resp = self._tipform_send(ace_idx, {
                    'method': 'unwind_filament',
                    'params': {'index': slot, 'length': int(ln),
                               'speed': int(unwind_speed)}})
                if not self._tipform_rejected(resp):
                    self._feed_assist_per_ace[ace_idx] = -1
                    return True
                self.reactor.pause(self.reactor.monotonic() + 2.0)
            return False

        try:
            run('MOVE_TO_DISCARD_FILAMENT_POSITION')
            run('M109 S%d' % int(temp))
            run('M83')
            for tok in table:
                kind = tok[0]
                if kind == 'move':
                    mm, feed = float(tok[1]), int(tok[2])
                    if is_v2 and mm > 0.:
                        if not fwd_armed:
                            run('M400')
                            fwd_armed = _tf_fa_start()
                        run('G1 E%.3f F%d' % (mm, feed))
                    elif is_v2 and mm <= -3.:
                        run('M400')
                        ln = int(round(-mm))
                        fwd_armed = False
                        if not _tf_unwind(ln):
                            logging.info('[multiACE] tipform: unwind %dmm '
                                         'rejected - bowden slack (V2 will '
                                         'brake)' % ln)
                        run('G1 E%.3f F%d' % (mm, feed))
                        run('M400')
                        self.reactor.pause(
                            self.reactor.monotonic()
                            + max(1.0, ln / max(unwind_speed, 1) + 0.5))
                    else:
                        run('G1 E%.3f F%d' % (mm, feed))
                elif kind == 'pause':
                    run('M400')
                    run('G4 P%d' % int(tok[1] * 1000.))
                elif kind == 'temp':
                    run('M104 S%d' % int(tok[1]))
                elif kind == 'waittemp':
                    c = float(tok[1])
                    run('M400')
                    run('M104 S%d' % int(c))
                    _h = None
                    try:
                        _h = self.printer.lookup_object(
                            'extruder' if head == 0
                            else 'extruder%d' % head).get_heater()
                    except Exception:
                        pass
                    if _h is not None:
                        _wt_deadline = self.reactor.monotonic() + 300.
                        while True:
                            _cur, _t = _h.get_temp(self.reactor.monotonic())
                            if abs(_cur - c) <= 3.:
                                break
                            if self.reactor.monotonic() > _wt_deadline:
                                self.log_always(
                                    '[multiACE] tipform waittemp:%d not '
                                    'reached in 300s (at %.0f) - continuing'
                                    % (int(c), _cur))
                                break
                            self.reactor.pause(
                                self.reactor.monotonic() + 0.5)
                elif kind == 'fan':
                    run('M106 S%d' % int(tok[1]))
        finally:
            if is_v2:
                if fwd_armed:
                    self._tipform_send(ace_idx, {
                        'method': 'stop_feed_assist',
                        'params': {'index': slot}})
                    self._feed_assist_per_ace[ace_idx] = -1
                self._v2_active_rev_assist = saved_rev_assist
        run('M400')
        run('M104 S0')
        run('M106 S255')
        run('G4 P5000')
        run('INNER_CUTOFF_BASE_DISCARD')
        run('INNER_ROUGHLY_CLEAN_NOZZLE_BASE_DISCARD ACTION=2')
        run('INNER_DISCARD_FILAMENT_BASE_DISCARD')
        run('M107')

    cmd_ACE_LOAD_HEAD_help = '[multiACE] Load a toolhead from ACE. Usage: ACE_LOAD_HEAD HEAD=0 [ACE=0] [SLOT=0]'
    def cmd_ACE_LOAD_HEAD(self, gcmd):

        head = gcmd.get_int('HEAD')
        self._last_load_ok = True

        if head < 0 or head > 3:
            raise gcmd.error('[multiACE] HEAD must be 0-3')
        if self.head_is_manual(head):
            self.log_always(
                '[multiACE] head %d is manual - ACE_LOAD_HEAD ignored, '
                'load it by hand' % head)
            return
        self._wait_bg_op(head, gcmd)
        _hm = (getattr(self, '_ace_mode', 'multi') == 'head'
               and self.head_uses_ace(head))
        if _hm:
            ace_index = gcmd.get_int('ACE', self.head_ace_for(head))
            slot = gcmd.get_int('SLOT', self._ace_slot_for_head(head))
        else:
            ace_index = gcmd.get_int('ACE', self._active_device_index)
            slot = gcmd.get_int('SLOT', head)
        if ace_index < 0 or not self._ensure_ace_available(ace_index):
            self.log_always(self._t('msg.ace_not_available',
                ace=self._disp(ace_index)))
            return
        if slot < 0 or slot > 3:
            raise gcmd.error('[multiACE] SLOT must be 0-3')
        if _hm and ace_index != self.head_ace_for(head):
            raise gcmd.error(
                '[multiACE] head %d is wired to ACE %d (one ACE per head) - '
                'refusing load from ACE %d. Rewire via ACE_SET_HEAD_ACE '
                'first.' % (self._disp(head),
                            self._disp(self.head_ace_for(head)),
                            self._disp(ace_index)))

        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
        _staged = getattr(self, '_bg_staged', {}).get(head)
        if _staged is not None and self.head_uses_ace(head):
            if int(_staged[0]) == int(ace_index) \
                    and int(_staged[1]) == int(slot):
                self._bg_staged.pop(head, None)
                self._bg_left_empty.discard(head)
                self.log_always(
                    '[multiACE] head %d: continuing the STAGED background '
                    'load (ACE %d / Slot %d already at the toolhead sensor)'
                    % (self._disp(head), self._disp(ace_index),
                       self._disp(slot)))
            else:
                raise gcmd.error(
                    '[multiACE] head %d has filament of ACE %d / Slot %d '
                    'STAGED at the sensor but the load targets ACE %d / '
                    'Slot %d - unload the head first (display/web), then '
                    'retry' % (self._disp(head), self._disp(_staged[0]),
                               self._disp(_staged[1]),
                               self._disp(ace_index), self._disp(slot)))
        elif sensor and sensor.get_status(0)['filament_detected']:
            if not self.head_uses_ace(head):
                self.log_always(self._t('msg.load_head_already_loaded',
                    head=self._disp(head)))
                return
            if self._head_source.get(head) is not None:
                self.log_always(self._t('msg.load_head_already_loaded',
                    head=self._disp(head)))
                return

            if len(self._ace_devices) == 1:
                only_idx = 0
                info = self._info_per_ace.get(only_idx, self._make_default_info(only_idx))
                slots = info.get('slots', [])
                slot_info = slots[slot] if slot < len(slots) else {}
                self._head_source[head] = {
                    'ace_index': only_idx,
                    'slot': slot,
                    'type': slot_info.get('type', 'PLA'),
                    'color': self.rgb2hex(*slot_info.get('color', (0, 0, 0))),
                    'brand': slot_info.get('brand', 'Generic'),
                }
                self._save_head_source()
                logging.info(self._t('msg.load_head_inferred_only_ace',
                    head=self._disp(head), slot=self._disp(slot)))
            else:
                self.log_error(self._t('msg.load_head_no_source_recorded',
                    head=self._disp(head), count=len(self._ace_devices)))
            return

        self.log_always(self._t('msg.load_head_starting',
            head=self._disp(head), ace=self._disp(ace_index), slot=self._disp(slot)))

        if ace_index != self._active_device_index:
            if not self._switch_ace_for_head_target(ace_index):
                raise gcmd.error(
                    '[multiACE] Failed to connect to ACE %d' % ace_index)

        if self.gate_status[slot] != GATE_AVAILABLE:
            self.log_always(self._t('msg.load_slot_no_filament',
                ace=self._disp(ace_index), slot=self._disp(slot)))
            return

        active_ext = self.toolhead.get_extruder().get_name()
        target_ext = 'extruder' if head == 0 else 'extruder%d' % head
        if active_ext != target_ext:
            logging.info('[multiACE] Load: switching to %s (was %s)' % (target_ext, active_ext))
            self.gcode.run_script_from_command('T%d A0' % head)
            self.toolhead.wait_moves()

        module, channel = self.EXTRUDER_MAP[head]

        self._head_source[head] = {
            'ace_index': ace_index,
            'slot': slot,
            'type': '',
            'color': '000000',
            'brand': '',
        }
        self._save_head_source()

        self.gcode.run_script_from_command(
            "SET_FILAMENT_SENSOR SENSOR=e%d_filament ENABLE=1" % head)

        ff_module = 'filament_feed %s' % module
        ff = None
        try:
            ff = self.printer.lookup_object(ff_module, None)
            if ff is None:
                logging.info('[multiACE] channel_state reset: %s not loaded' % ff_module)
            elif channel >= len(ff.channel_state):
                logging.info(
                    '[multiACE] channel_state reset: channel %d out of range (%d)' % (
                        channel, len(ff.channel_state)))
            else:
                prev_state = ff.channel_state[channel]
                ff.channel_state[channel] = 'inited'
                if 'load_finish' in ff.config:
                    ff.config['load_finish'][channel] = False
                logging.info(
                    '[multiACE] channel_state reset: %s ch=%d prev=%s -> inited, load_finish=False' % (
                        ff_module, channel, prev_state))
        except Exception as e:
            logging.info('[multiACE] channel_state reset error: %s' % e)

        wheel_before = self._read_wheel_counts(module, channel)

        self._in_internal_load_head = True
        try:
            try:
                self.gcode.run_script_from_command(
                    "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d LOAD=1"
                    % (module, channel, head))
            except Exception as e:
                self._audit_state('LOAD_HEAD_FAILED', {'head': head, 'ace': ace_index, 'slot': slot, 'reason': 'feed_auto_error', 'error': str(e)})

                try:
                    src = self._head_source.get(head)
                    if src is not None:
                        src['load_failed'] = True
                        self._save_head_source()
                except Exception:
                    pass
                self._last_load_ok = False
                raise
        finally:
            self._in_internal_load_head = False

        if self.head_uses_ace(head):
            _load_ok = True
            _skip_reason = None
            try:
                if ff is not None and channel < len(ff.channel_state):
                    _load_ok = bool(ff.config['load_finish'][channel])
                    if not _load_ok:
                        if not ff.config['auto_mode'][channel]:
                            _skip_reason = 'auto_mode'
                        else:
                            _skip_reason = str(ff.channel_state[channel])
            except Exception as ve:
                logging.info(
                    '[multiACE] load_finish verify unavailable: %s' % ve)
                _load_ok = True
            if not _load_ok:
                self._audit_state('LOAD_HEAD_FAILED', {
                    'head': head, 'ace': ace_index, 'slot': slot,
                    'reason': 'load_not_finished', 'detail': _skip_reason})
                try:
                    src = self._head_source.get(head)
                    if src is not None:
                        src['load_failed'] = True
                        self._save_head_source()
                except Exception:
                    pass
                self._last_load_ok = False
                if _skip_reason == 'auto_mode':
                    raise gcmd.error(self._t('msg.load_refused_auto_mode',
                        head=self._disp(head)))
                raise gcmd.error(self._t('msg.load_not_finished',
                    head=self._disp(head), state=_skip_reason))

        if not self.head_uses_ace(head):
            self._head_source[head] = None
            self._save_head_source()
            self._ghost_heads.discard(head)
            self._refresh_filament_exist_flags()
            self.log_always(
                '[multiACE] Head %d loaded via stock feeder (no ACE source)'
                % self._disp(head))
            self._audit_state('LOAD_HEAD', {'head': head, 'feeder': True})
            return

        rfid_deadline = self.reactor.monotonic() + 3.0
        while self.reactor.monotonic() < rfid_deadline:
            if self._info['slots'][slot].get('rfid', 0) != 0:
                break
            self.reactor.pause(self.reactor.monotonic() + 0.1)
        if self._info['slots'][slot].get('rfid', 0) == 0:
            logging.info('[multiACE] LOAD_HEAD: RFID not ready for slot %d after wait' % slot)

        slot_info = self._info['slots'][slot]
        self._head_source[head] = {
            'ace_index': ace_index,
            'slot': slot,
            'type': slot_info.get('type', 'PLA'),
            'subtype': slot_info.get('subtype', ''),
            'color': self.rgb2hex(*slot_info.get('color', (0, 0, 0))),
            'brand': slot_info.get('brand', 'Generic'),
        }
        self._save_head_source()
        self._ghost_heads.discard(head)

        load_override = self._override_for(ace_index, slot)
        if load_override is not None:
            push_type    = load_override.get('material') or self._head_source[head]['type']
            push_color   = self._override_color_to_rgba(load_override.get('color', ''))
            push_brand   = load_override.get('brand') or self._head_source[head]['brand']
            push_subtype = load_override.get('subtype', '') or ''
            do_push = True
        elif self._head_source[head]['type']:
            push_type    = self._head_source[head]['type']
            push_color   = self._head_source[head]['color']
            push_brand   = self._head_source[head]['brand']
            push_subtype = self._head_source[head].get('subtype', '') or ''
            do_push = True
        else:
            push_type    = ''
            push_color   = '000000FF'
            push_brand   = ''
            push_subtype = ''
            logging.info('[multiACE] LOAD_HEAD: head %d loaded with no override/'
                         'RFID identity; clearing display ("?")' % head)
            do_push = True
        if do_push:
            self._expect_ptc_push(head, push_type, push_color, push_brand, push_subtype)
            self.gcode.run_script_from_command(
                'SET_PRINT_FILAMENT_CONFIG '
                'CONFIG_EXTRUDER=%d '
                'FILAMENT_TYPE="%s" '
                'FILAMENT_COLOR_RGBA=%s '
                'VENDOR="%s" '
                'FILAMENT_SUBTYPE="%s"' % (
                    head, push_type, push_color, push_brand, push_subtype))

        self._refresh_filament_exist_flags()

        self.log_always(self._t('msg.load_head_loaded',
            head=self._disp(head), ace=self._disp(ace_index), slot=self._disp(slot)))
        self._audit_state('LOAD_HEAD', {'head': head, 'ace': ace_index, 'slot': slot})

    cmd_ACE_UNLOAD_HEAD_help = (
        '[multiACE] Unload a toolhead back to its ACE. '
        'Usage: ACE_UNLOAD_HEAD HEAD=0 [RETRACT_LENGTH=<mm>] [KEEP_HEAT=<temp>]')
    def cmd_ACE_UNLOAD_HEAD(self, gcmd):

        head = gcmd.get_int('HEAD')

        retract_override = gcmd.get_int('RETRACT_LENGTH', 0)
        keep_heat = gcmd.get_int('KEEP_HEAT', 0)

        self._last_unload_ok = True

        if head < 0 or head > 3:
            raise gcmd.error('[multiACE] HEAD must be 0-3')
        if self.head_is_manual(head):
            self.log_always(
                '[multiACE] head %d is manual - ACE_UNLOAD_HEAD ignored, '
                'unload it by hand' % head)
            return
        self._wait_bg_op(head, gcmd)
        if not self._head_is_loaded(head):
            self.log_always(self._t('msg.unload_head_already_empty',
                head=self._disp(head)))
            return

        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
        if sensor and not sensor.get_status(0)['filament_detected']:
            logging.info(self._t('msg.unload_sensor_no_filament', head=self._disp(head)))

        source = self._head_source.get(head)
        if source is None:
            staged = getattr(self, '_bg_staged', {}).pop(head, None)
            if staged is not None:
                self._bg_left_empty.discard(head)
                source = {'ace_index': int(staged[0]), 'slot': int(staged[1])}
                logging.info('[multiACE] unload head %d: using bg-staged '
                             'ACE %d slot %d as the source'
                             % (head, staged[0], staged[1]))
        if source:
            ace_index = source['ace_index']
            slot = source['slot']
            self.log_always(self._t('msg.unload_head_starting',
                head=self._disp(head), ace=self._disp(ace_index), slot=self._disp(slot)))

            if ace_index != self._active_device_index:
                if not self._switch_ace_for_head_target(ace_index):
                    raise gcmd.error(
                        '[multiACE] Failed to connect to ACE %d for unload!' % ace_index)
        else:
            logging.info(self._t('msg.unload_head_no_mapping', head=self._disp(head)))

        def _noop_cb(self, response):
            pass
        active_idx = self._active_device_index

        proto = self._protocols.get(active_idx)
        is_v2 = (proto is not None and getattr(proto, 'NAME', None) == 'v2')
        if not self.head_uses_ace(head):
            self._fa_trace('unload: head %d not ACE-driven - skip ACE FA' % head)
        elif is_v2:
            self._v2_arm_fa_for_unload(head)
            self._fa_trace(
                'unload skip-stop FA on ACE %d (V2 - velocity tracker '
                'handles rollback assist via mode=3)' % active_idx)
        else:
            stop_slots = set()
            tracked = self._feed_assist_per_ace.get(active_idx, -1)
            if 0 <= tracked <= 3:
                stop_slots.add(tracked)
            if source is not None:
                src_slot = source.get('slot', -1)
                if 0 <= src_slot <= 3:
                    stop_slots.add(src_slot)
            for slot_idx in sorted(stop_slots):
                try:
                    self.send_request_to(active_idx,
                        {"method": "stop_feed_assist", "params": {"index": slot_idx}},
                        _noop_cb)
                except Exception as e:
                    logging.info(
                        '[multiACE] targeted stop_feed_assist slot %d failed: %s' % (slot_idx, e))
            self._feed_assist_per_ace[active_idx] = -1
            if active_idx == self._active_device_index:
                self._feed_assist_index = -1
            self._fa_trace('targeted-stop FA on ACE %d slots=%s before unload' % (
                active_idx, sorted(stop_slots)))
        self.wait_ace_ready()

        if not self._swap_in_progress:
            self.gcode.run_script_from_command(
                "SET_FILAMENT_SENSOR SENSOR=e%d_filament ENABLE=0" % head)

        module, channel = self.EXTRUDER_MAP[head]

        self._retract_length_override = retract_override if retract_override > 0 else None
        try:
            self.gcode.run_script_from_command(
                "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=prepare"
                % (module, channel, head))
            self.gcode.run_script_from_command(
                "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=doing"
                % (module, channel, head))
        except Exception as e:
            self._audit_state('UNLOAD_HEAD_FAILED', {'head': head, 'reason': 'feed_auto_error', 'error': str(e), 'active_device': self._active_device_index})
            raise
        finally:
            self._retract_length_override = None

        if keep_heat > 0:
            self.gcode.run_script_from_command('M104 S%d' % keep_heat)

        self.gcode.run_script_from_command(
            "SET_FILAMENT_SENSOR SENSOR=e%d_filament ENABLE=1" % head)

        machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
        if machine_state_manager is not None:
            self._machine_state_after_feed_op()

        still_detected = bool(sensor
                              and sensor.get_status(0)['filament_detected'])
        unload_verified = (not still_detected
                           and getattr(self, '_last_unload_ok', True))
        if unload_verified:
            self._head_source[head] = None
            self._save_head_source()
            self._bg_load_unverified.discard(head)
            getattr(self, '_bg_prime_deficit', {}).pop(head, None)
        self._push_rfid_info()
        self._sync_ptc_to_active_ace()

        if not unload_verified:
            if still_detected:
                self.log_error(self._t('msg.unload_filament_still_detected', head=self._disp(head)))
            logging.info('[multiACE] UNLOAD_HEAD: keeping head_source[%d] '
                         '(unload not verified) so a retry targets the right '
                         'slot' % head)
        else:
            self.log_always(self._t('msg.unload_head_success', head=self._disp(head)))
        self._audit_state('UNLOAD_HEAD', {'head': head})

    cmd_ACE_TEST_help = (
        '[multiACE] Run load/unload test. PLAN items (comma-sep): '
        '0:1=load HEAD:ACE, H0:1=swap HEAD to ACE, A0=all from ACE, '
        'U=unload all, U0..U3=unload head, S0..S3=switch ACE, W5=wait 5s')
    def cmd_ACE_TEST(self, gcmd):
        plan_str = gcmd.get('PLAN', '')
        do_unload = gcmd.get_int('UNLOAD', 1)

        was_debug = self._state_debug_enabled
        self._state_debug_enabled = True
        self._state_log.info('TEST_START plan="%s" unload=%d', plan_str, do_unload)

        try:
            hs_dump = json.dumps({str(h): self._head_source[h] for h in range(4)})
        except Exception:
            hs_dump = str(self._head_source)
        self._state_log.info('TEST_START head_source=%s active_device=%d',
                             hs_dump, self._active_device_index)
        self._audit_state('TEST_START', {'plan': plan_str, 'unload': do_unload})

        steps = []
        if plan_str:
            for item in plan_str.split(','):
                item = item.strip()
                if not item:
                    continue
                if item == 'U':
                    steps.append({'action': 'UNLOAD_ALL'})
                elif item.startswith('U') and item[1:].isdigit():
                    steps.append({'action': 'UNLOAD', 'head': int(item[1:])})
                elif item.startswith('A') and item[1:].isdigit():
                    ace = int(item[1:])
                    for h in range(4):
                        steps.append({'action': 'LOAD', 'head': h, 'ace': ace})
                elif item.startswith('H') and ':' in item[1:]:
                    parts = item[1:].split(':')
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        steps.append({'action': 'SWAP', 'head': int(parts[0]), 'ace': int(parts[1])})
                    else:
                        raise gcmd.error('[multiACE] Invalid PLAN item: %s (use H0:1)' % item)
                elif item.startswith('S') and item[1:].isdigit():
                    steps.append({'action': 'SWITCH', 'ace': int(item[1:])})
                elif item.startswith('W') and item[1:].replace('.', '', 1).isdigit():
                    steps.append({'action': 'WAIT', 'seconds': float(item[1:])})
                elif ':' in item:
                    parts = item.split(':')
                    if len(parts) == 2:
                        steps.append({'action': 'LOAD', 'head': int(parts[0]), 'ace': int(parts[1])})
                    else:
                        raise gcmd.error('[multiACE] Invalid PLAN item: %s' % item)
                else:
                    raise gcmd.error(
                        '[multiACE] Invalid PLAN item: %s '
                        '(use HEAD:ACE, A0, U, U0..U3, S0..S3, W<seconds>)' % item)
        else:
            self._refresh_ace_devices('test')
            for i in range(min(len(self._ace_devices), 4)):
                steps.append({'action': 'LOAD', 'head': i, 'ace': i})

        self.log_always(self._t('msg.test_start',
            steps=len(steps), unload=('yes' if do_unload else 'no')))

        try:
            self.gcode.run_script_from_command('G28')
            self.toolhead.wait_moves()
        except Exception as e:
            self.log_always(self._t('msg.test_homing_failed', error=e))

        self._test_cancel = False
        results = []
        step_nr = 0
        for step in steps:
            if self._test_cancel:
                self.log_always(self._t('msg.test_cancelled', step=step_nr))
                results.append({'step': step_nr + 1, 'action': 'CANCEL', 'status': 'CANCELLED'})
                break
            step_nr += 1
            action = step['action']

            if action == 'LOAD':
                head = step['head']
                ace = step['ace']
                self.log_always(self._t('msg.test_step_load',
                    step=step_nr, total=len(steps),
                    head=self._disp(head), ace=self._disp(ace), slot=self._disp(head)))
                try:
                    self.gcode.run_script_from_command(
                        'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d' % (head, ace, head))
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % head, None)
                    detected = sensor and sensor.get_status(0)['filament_detected']
                    src = self._head_source.get(head)
                    if detected and src is not None:
                        results.append({'step': step_nr, 'action': 'LOAD', 'status': 'PASS', 'head': head, 'ace': ace})
                        self.log_always(self._t('msg.test_step_load_pass', step=step_nr))
                    else:
                        reason = []
                        if not detected:
                            reason.append('sensor=no_filament')
                        if src is None:
                            reason.append('mapping=missing')
                        results.append({'step': step_nr, 'action': 'LOAD', 'status': 'FAIL',
                                        'head': head, 'ace': ace, 'reason': ', '.join(reason)})
                        self.log_always(self._t('msg.test_step_fail_reasons', step=step_nr, reason=', '.join(reason)))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'LOAD', 'status': 'ERROR',
                                    'head': head, 'ace': ace, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'UNLOAD':
                head = step['head']
                self.log_always(self._t('msg.test_step_unload',
                    step=step_nr, total=len(steps), head=self._disp(head)))
                try:
                    self.gcode.run_script_from_command('ACE_UNLOAD_HEAD HEAD=%d' % head)
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % head, None)
                    still_loaded = sensor and sensor.get_status(0)['filament_detected']
                    if not still_loaded:
                        results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'PASS', 'head': head})
                        self.log_always(self._t('msg.test_step_unload_pass', step=step_nr))
                    else:
                        results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'FAIL',
                                        'head': head, 'reason': 'filament still detected'})
                        self.log_always(self._t('msg.test_step_unload_fail', step=step_nr))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'ERROR',
                                    'head': head, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'UNLOAD_ALL':
                self.log_always(self._t('msg.test_step_unload_all',
                    step=step_nr, total=len(steps)))
                try:
                    self.gcode.run_script_from_command('ACE_UNLOAD_ALL_HEADS')
                    all_clear = True
                    for h in range(4):
                        sensor = self.printer.lookup_object(
                            'filament_motion_sensor e%d_filament' % h, None)
                        if sensor and sensor.get_status(0)['filament_detected']:
                            all_clear = False
                    if all_clear:
                        results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'PASS'})
                        self.log_always(self._t('msg.test_step_unload_all_pass', step=step_nr))
                    else:
                        results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'FAIL',
                                        'reason': 'filament still detected'})
                        self.log_always(self._t('msg.test_step_unload_fail', step=step_nr))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'ERROR',
                                    'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'SWITCH':
                ace = step['ace']
                self.log_always(self._t('msg.test_step_switch',
                    step=step_nr, total=len(steps), ace=self._disp(ace)))
                try:
                    self.gcode.run_script_from_command('ACE_SWITCH TARGET=%d' % ace)
                    if self._active_device_index == ace:
                        results.append({'step': step_nr, 'action': 'SWITCH', 'status': 'PASS', 'ace': ace})
                        self.log_always(self._t('msg.test_step_switch_pass', step=step_nr, ace=self._disp(ace)))
                    else:
                        results.append({'step': step_nr, 'action': 'SWITCH', 'status': 'FAIL',
                                        'ace': ace, 'reason': 'active=%d' % self._active_device_index})
                        self.log_always(self._t('msg.test_step_switch_fail', step=step_nr, active=self._disp(self._active_device_index), expected=self._disp(ace)))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'SWITCH', 'status': 'ERROR',
                                    'ace': ace, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))

            elif action == 'SWAP':
                head = step['head']
                ace = step['ace']
                self.log_always(self._t('msg.test_step_swap',
                    step=step_nr, total=len(steps), head=self._disp(head), ace=self._disp(ace)))
                try:
                    self.gcode.run_script_from_command(
                        'ACE_SWAP_HEAD HEAD=%d ACE=%d' % (head, ace))
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % head, None)
                    detected = sensor and sensor.get_status(0)['filament_detected']
                    src = self._head_source.get(head)
                    if detected and src is not None and src['ace_index'] == ace:
                        results.append({'step': step_nr, 'action': 'SWAP', 'status': 'PASS',
                                        'head': head, 'ace': ace})
                        self.log_always(self._t('msg.test_step_swap_pass', step=step_nr, ace=self._disp(ace)))
                    else:
                        reason = []
                        if not detected:
                            reason.append('sensor=no_filament')
                        if src is None:
                            reason.append('mapping=missing')
                        elif src['ace_index'] != ace:
                            reason.append('mapping=ACE %d (expected %d)' % (src['ace_index'], ace))
                        results.append({'step': step_nr, 'action': 'SWAP', 'status': 'FAIL',
                                        'head': head, 'ace': ace, 'reason': ', '.join(reason)})
                        self.log_always(self._t('msg.test_step_fail_reasons', step=step_nr, reason=', '.join(reason)))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'SWAP', 'status': 'ERROR',
                                    'head': head, 'ace': ace, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'WAIT':
                seconds = step['seconds']
                self.log_always(self._t('msg.test_step_wait',
                    step=step_nr, total=len(steps), seconds=seconds))
                try:
                    self.reactor.pause(self.reactor.monotonic() + seconds)
                    results.append({'step': step_nr, 'action': 'WAIT', 'status': 'PASS', 'seconds': seconds})
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'WAIT', 'status': 'ERROR',
                                    'seconds': seconds, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))

        if do_unload:
            step_nr += 1
            self.log_always(self._t('msg.test_final_unload_all'))
            try:
                self.gcode.run_script_from_command('ACE_UNLOAD_ALL_HEADS')
                all_clear = True
                for h in range(4):
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % h, None)
                    if sensor and sensor.get_status(0)['filament_detected']:
                        all_clear = False
                if all_clear:
                    results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'PASS'})
                    self.log_always(self._t('msg.test_final_pass'))
                else:
                    results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'FAIL',
                                    'reason': 'filament still detected'})
                    self.log_always(self._t('msg.test_final_fail'))
            except Exception as e:
                results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'ERROR',
                                'reason': str(e)})
                self.log_always(self._t('msg.test_final_error', error=str(e)))

        passed = sum(1 for r in results if r['status'] == 'PASS')
        failed = sum(1 for r in results if r['status'] == 'FAIL')
        errors = sum(1 for r in results if r['status'] == 'ERROR')
        total = len(results)
        self.log_always(self._t('msg.test_complete',
            passed=passed, total=total, failed=failed, errors=errors))

        self._state_log.info('TEST_RESULT %s', json.dumps(results, default=str))
        self._state_debug_enabled = was_debug

    def _get_swap_temp(self, head):

        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            fp = self.printer.lookup_object('filament_parameters', None)
            if ptc is None or fp is None:
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step1 skip '
                    '(ptc=%s fp=%s)' % (head, ptc is not None, fp is not None))
            else:
                status = ptc.get_status()
                vendor = status.get('filament_vendor', [''] * 4)
                ftype = status.get('filament_type', [''] * 4)
                subtype = status.get('filament_sub_type', [''] * 4)
                v = vendor[head] if head < len(vendor) else ''
                t = ftype[head] if head < len(ftype) else ''
                s = subtype[head] if head < len(subtype) else ''
                temp = fp.get_load_temp(v, t, s)
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step1 ptc lookup: '
                    'vendor=%r type=%r sub=%r -> get_load_temp=%r'
                    % (head, v, t, s, temp))
                if temp and temp >= 170:
                    try:
                        _en = 'extruder' if head == 0 else 'extruder%d' % head
                        _ex = self.printer.lookup_object(_en, None)
                        _pt = int(_ex.get_heater().target_temp) if _ex else 0
                        if 170 <= _pt < int(temp):
                            logging.info(
                                '[multiACE] _get_swap_temp head=%d cap load %d '
                                '-> print target %d (hold)' % (head, int(temp), _pt))
                            return _pt
                    except Exception:
                        pass
                    return int(temp)
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step1 rejected '
                    '(temp=%r not in [170,inf))' % (head, temp))
        except Exception as e:
            logging.info(
                '[multiACE] _get_swap_temp head=%d step1 raised: %s: %s'
                % (head, type(e).__name__, e))

        try:
            extruder_name = 'extruder' if head == 0 else 'extruder%d' % head
            extruder = self.printer.lookup_object(extruder_name, None)
            if extruder is None:
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step2 skip '
                    '(%s not loaded)' % (head, extruder_name))
            else:
                target = extruder.get_heater().target_temp
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step2 %s.target_temp=%s'
                    % (head, extruder_name, target))
                if target >= 170:
                    return int(target)
                logging.info(
                    '[multiACE] _get_swap_temp head=%d step2 rejected '
                    '(target=%s < 170)' % (head, target))
        except Exception as e:
            logging.info(
                '[multiACE] _get_swap_temp head=%d step2 raised: %s: %s'
                % (head, type(e).__name__, e))

        logging.info(
            '[multiACE] _get_swap_temp head=%d -> swap_default_temp=%d (fallback)'
            % (head, self.swap_default_temp))
        return self.swap_default_temp

    cmd_ACE_SWAP_HEAD_help = '[multiACE] Mid-print filament swap. Usage: ACE_SWAP_HEAD HEAD=0 ACE=1 [SLOT=0]'
    def cmd_ACE_SWAP_HEAD(self, gcmd):

        head = gcmd.get_int('HEAD')
        ace_index = gcmd.get_int('ACE')
        slot = gcmd.get_int('SLOT', head)
        if gcmd.get_int('SKIP_POS_RESTORE', 0):
            logging.info('[multiACE] Swap: SKIP_POS_RESTORE=1 ignored '
                         '(deprecated, stale processed gcode) - doing the '
                         'full pos-restore')
        anti_ooze = gcmd.get_float(
            'ANTI_OOZE', float(self.swap_anti_ooze_retract),
            minval=0., maxval=50.)

        if head < 0 or head > 3:
            raise gcmd.error('[multiACE] HEAD must be 0-3')
        if self.head_is_manual(head):
            self.log_always(
                '[multiACE] head %d is manual - ACE_SWAP_HEAD ignored, '
                'load it by hand' % head)
            return
        self._wait_bg_op(head, gcmd)
        if ace_index < 0 or not self._ensure_ace_available(ace_index):
            raise gcmd.error('ACE %d not available' % ace_index)
        if slot < 0 or slot > 3:
            raise gcmd.error('[multiACE] SLOT must be 0-3')
        if (getattr(self, '_ace_mode', 'multi') == 'head'
                and self.head_uses_ace(head)
                and ace_index != self.head_ace_for(head)):
            raise gcmd.error(
                '[multiACE] head %d is wired to ACE %d (one ACE per head) - '
                'refusing swap on ACE %d. Rewire via ACE_SET_HEAD_ACE first.'
                % (self._disp(head), self._disp(self.head_ace_for(head)),
                   self._disp(ace_index)))
        if head in self._ghost_heads:
            raise gcmd.error(
                '[multiACE] SWAP refused: head %d is a ghost (filament at '
                'toolhead but no head_source mapping recorded). FA routing '
                'would have to guess which ACE to drive. '
                'Recover: ACEC__Unload_All then ACEB__Load_%d, then restart '
                'the print.' % (head, head))

        source = self._head_source.get(head)
        if (source and source['ace_index'] == ace_index and source['slot'] == slot
                and not source.get('load_failed')):
            logging.info('[multiACE] Swap: HEAD %d already on ACE %d / Slot %d - skipping' % (
                head, ace_index, slot))

            try:
                active_ext = self.toolhead.get_extruder().get_name()
                active_head = (0 if active_ext == 'extruder'
                               else int(active_ext.replace('extruder', '')))
            except Exception:
                active_head = None
            swap_temp = self._get_swap_temp(head)
            if head == active_head and swap_temp >= 170:
                heater = 'extruder' if head == 0 else 'extruder%d' % head
                self.gcode.run_script_from_command(
                    'SET_HEATER_TEMPERATURE HEATER=%s TARGET=%d' % (heater, swap_temp))
                self.gcode.run_script_from_command(
                    'TEMPERATURE_WAIT SENSOR=%s MINIMUM=%d' % (heater, swap_temp - 5))
                _had_pickcheck = head in getattr(self, '_bg_load_unverified', ())
                if _had_pickcheck:
                    _pick = self._bg_pick_flow_check(head, anti_ooze)
                    if _pick == 'no_flow':
                        self._pause_for_recovery(
                            gcmd,
                            detail_msg=self._t('msg.bg_pick_no_flow_pause',
                                               head=self._disp(head)),
                            recovery_steps=[
                                'Check the nozzle / filament path of head '
                                '%d (no flow despite re-grip)'
                                    % self._disp(head),
                                'Purge manually until filament extrudes',
                                'RESUME                (continue the print)',
                            ],
                            code=4,
                        )
                    elif _pick == 'sensor_absent':
                        self._pause_for_recovery(
                            gcmd,
                            detail_msg=self._t('msg.bg_pick_absent_pause',
                                               head=self._disp(head)),
                            recovery_steps=[
                                'Load head %d manually (web Load or '
                                'ACE_LOAD_HEAD)' % self._disp(head),
                                'RESUME                (continue the print)',
                            ],
                            code=4,
                        )
                if (self.reactor.monotonic()
                        < getattr(self, '_resume_wipe_deadline', 0.)):
                    self._resume_wipe_deadline = 0.
                    if not _had_pickcheck:
                        self.log_always(
                            '[multiACE] Post-resume ooze wipe: head %d sat '
                            'hot through the pause, no-op swap runs no '
                            'flush - wiping at the discard edge'
                            % self._disp(head))
                        self._discard_wipe(head, 'resume-wipe')
            elif head != active_head:
                logging.info('[multiACE] Swap: HEAD %d not active toolhead '
                             '(active=%s) - skip pre-heat to avoid holding '
                             'idle head at load_temp' % (head, active_head))
            return

        if ace_index in self._fa_load_disable:
            self.log_error(self._t('msg.swap_refused_fa_load_disable',
                ace=self._disp(ace_index), head=self._disp(head)))
            return

        target_gate = self._gate_status_per_ace.get(ace_index)
        if (target_gate is not None and slot < len(target_gate)
                and target_gate[slot] != GATE_AVAILABLE):
            cur_src = self._head_source.get(head)
            self._telemetry('SWAP_SUMMARY', {
                'head': head,
                'from_ace': cur_src['ace_index'] if cur_src else None,
                'from_slot': cur_src['slot'] if cur_src else None,
                'to_ace': ace_index,
                'to_slot': slot,
                'status': 'slot_empty_pre_unload',
                'total_ms': 0,
                'unload_ms': None,
                'load_ms': None,
                'context': self._fa_context,
            })
            self._pause_for_recovery(
                gcmd,
                detail_msg=self._t('msg.pause_swap_slot_empty',
                    head=self._disp(head), ace=self._disp(ace_index),
                    slot=self._disp(slot)),
                recovery_steps=[
                    'Load filament into ACE %d / Slot %d'
                        % (self._disp(ace_index), self._disp(slot)),
                    'ACE_SWAP_HEAD HEAD=%d ACE=%d SLOT=%d   (re-run swap)'
                        % (head, ace_index, slot),
                    'RESUME                            (continue the print)',
                ],
                code=3,
            )
            return

        swap_temp = self._get_swap_temp(head)

        self.log_always(self._t('msg.swap_start',
            head=self._disp(head), ace=self._disp(ace_index), slot=self._disp(slot),
            temp=swap_temp))

        swap_start_ts = time.monotonic()
        unload_start_ts = None
        unload_end_ts = None
        load_start_ts = None
        load_end_ts = None
        swap_status = 'ok'
        prev_source = self._head_source.get(head)
        prev_ace_src = prev_source['ace_index'] if prev_source else None
        prev_slot_src = prev_source['slot'] if prev_source else None

        self._swap_in_progress = True
        self._swap_phase = 'unload'
        self._resume_wipe_deadline = 0.
        self._ace_event(
            'swap_imminent', head=head, ace=ace_index, slot=slot,
            from_ace=(prev_ace_src if prev_ace_src is not None else -1),
            from_slot=(prev_slot_src if prev_slot_src is not None else -1))

        fa_prev_auto = self._auto_feed_enabled
        fa_prev_context = self._fa_context
        self._auto_feed_enabled = False
        self._fa_context = 'idle'
        self._fa_trace('gate CLOSE for swap unload (was auto=%s context=%s)' % (
            fa_prev_auto, fa_prev_context))

        try:

            gcode_move = self.printer.lookup_object('gcode_move')
            saved_pos = self.toolhead.get_position()[:3]
            saved_speed = gcode_move.speed
            saved_absolute = gcode_move.absolute_coord
            saved_e_base = gcode_move.base_position[3]
            saved_e_last = gcode_move.last_position[3]
            logging.info('[multiACE] Swap: saved pos X=%.2f Y=%.2f Z=%.2f (pre-T-switch)' % (
                saved_pos[0], saved_pos[1], saved_pos[2]))

            self._fa_log.info(
                '[swap-trace] ENTRY head=%d ace=%d slot=%d '
                'saved_e_base=%.3f saved_e_last=%.3f '
                'abs_extrude=%s anti_ooze=%.2f'
                % (head, ace_index, slot, saved_e_base, saved_e_last,
                   gcode_move.absolute_extrude,
                   anti_ooze))

            orig_ext_name = self.toolhead.get_extruder().get_name()
            target_ext = 'extruder' if head == 0 else 'extruder%d' % head
            switched_head = (orig_ext_name != target_ext)
            self._swap_saved_pos = saved_pos
            self._swap_orig_ext_name = orig_ext_name
            self._swap_switched_head = switched_head
            if switched_head:
                logging.info('[multiACE] Swap: switching to %s (was %s)' % (target_ext, orig_ext_name))
                self.gcode.run_script_from_command('T%d A0' % head)
                self.toolhead.wait_moves()

            saved_heater_target = 0
            try:
                extruder_obj = self.toolhead.get_extruder()
                if extruder_obj is not None:
                    saved_heater_target = int(extruder_obj.get_heater().target_temp)
            except Exception:
                pass
            logging.info('[multiACE] Swap: saved heater=%d (swap head)' % saved_heater_target)
            self._swap_probe_ref_temp = saved_heater_target

            prev_ace = self._active_device_index
            if self._feed_assist_per_ace.get(prev_ace, -1) != -1:
                self._disarm_fa_for(prev_ace)

            self.gcode.run_script_from_command('G91')
            self.gcode.run_script_from_command('G1 Z2 F600')
            self.gcode.run_script_from_command('G90')
            self.toolhead.wait_moves()

            self.gcode.run_script_from_command('M83')

            sensor_obj = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            sensor_present = (sensor_obj is not None and
                              sensor_obj.get_status(0)['filament_detected'])
            bg_empty = head in getattr(self, '_bg_left_empty', ())
            empty_head = ((not sensor_present) and (prev_source is None)) or bg_empty

            if empty_head:
                if bg_empty:
                    self._bg_left_empty.discard(head)
                    staged = getattr(self, '_bg_staged', {}).get(head)
                    if (staged is not None
                            and (int(staged[0]) != int(ace_index)
                                 or int(staged[1]) != int(slot))):
                        raise gcmd.error(
                            '[multiACE] head %d has filament of ACE %d / '
                            'Slot %d STAGED at the sensor but the swap '
                            'targets ACE %d / Slot %d - unload the head '
                            'first (display/web), then retry'
                            % (self._disp(head), self._disp(staged[0]),
                               self._disp(staged[1]), self._disp(ace_index),
                               self._disp(slot)))
                logging.info(
                    '[multiACE] Swap: head %d is empty '
                    '(sensor=%s, head_source=%s, bg_left_empty=%s) - skipping '
                    'unload, proceeding directly to load'
                    % (head, sensor_present, prev_source is not None, bg_empty))
                unload_start_ts = time.monotonic()
                unload_end_ts = unload_start_ts
            else:

                logging.info('[multiACE] Swap: delegating unload to ACE_UNLOAD_HEAD')
                unload_start_ts = time.monotonic()
                if prev_source:
                    _src_ace = int(prev_source.get('ace_index',
                                                   self._active_device_index))
                    _src_slot = int(prev_source.get('slot', head))
                else:
                    _src_ace = self._active_device_index
                    _src_slot = head
                swap_rl = self.get_swap_retract_length(_src_ace, _src_slot)
                try:
                    if swap_rl > 0:
                        self.gcode.run_script_from_command(
                            'ACE_UNLOAD_HEAD HEAD=%d RETRACT_LENGTH=%d KEEP_HEAT=%d' % (
                                head, swap_rl, swap_temp))
                        logging.info('[multiACE] Swap: unload done (retract %dmm, heat held @ %d)' % (
                            swap_rl, swap_temp))
                    else:
                        self.gcode.run_script_from_command(
                            'ACE_UNLOAD_HEAD HEAD=%d KEEP_HEAT=%d' % (head, swap_temp))
                        logging.info('[multiACE] Swap: unload done (per-ACE retract_length, heat held @ %d)' % swap_temp)
                except Exception:
                    swap_status = 'unload_failed'
                    raise
                unload_end_ts = time.monotonic()

                if not self._last_unload_ok:

                    swap_status = 'unload_failed'
                    self._swap_back_to_orig_for_pause(
                        switched_head, orig_ext_name)
                    self._restore_pos_for_pause(saved_pos)
                    _uA, _uS = self._disp(_src_ace), self._disp(_src_slot)
                    _lA, _lS = self._disp(ace_index), self._disp(slot)
                    self._pause_for_recovery(
                        gcmd,
                        detail_msg=self._t('msg.pause_swap_unload_jam',
                            head=self._disp(head), ua=_uA, us=_uS, la=_lA, ls=_lS),
                        recovery_steps=[
                            'Unload ACE%d / Slot%d' % (_uA, _uS),
                            'Load ACE%d / Slot%d' % (_lA, _lS),
                            'RESUME                            (continue the print)',
                        ],
                    )
                    return

            if ace_index != self._active_device_index:
                logging.info(self._t('msg.swap_switching_ace',
                    ace=self._disp(ace_index)))
                if not self._switch_ace_for_head_target(ace_index):
                    raise gcmd.error('[multiACE] Failed to connect to ACE %d' % ace_index)

            if self.gate_status[slot] != GATE_AVAILABLE:

                swap_status = 'slot_empty'
                self._swap_back_to_orig_for_pause(
                    switched_head, orig_ext_name)
                self._restore_pos_for_pause(saved_pos)
                self._pause_for_recovery(
                    gcmd,
                    detail_msg=self._t('msg.pause_swap_slot_empty_post',
                        head=self._disp(head), ace=self._disp(ace_index),
                        slot=self._disp(slot)),
                    recovery_steps=[
                        'Load filament into ACE %d / Slot %d'
                            % (self._disp(ace_index), self._disp(slot)),
                        'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d   (load head)'
                            % (head, ace_index, slot),
                        'RESUME                            (continue the print)',
                    ],
                    code=3,
                )
                return

            self._swap_phase = 'load'
            logging.info('[multiACE] Swap: delegating load to ACE_LOAD_HEAD (ACE %d / Slot %d)' % (ace_index, slot))
            load_start_ts = time.monotonic()
            try:
                self.gcode.run_script_from_command(
                    'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d' % (head, ace_index, slot))
            except Exception as load_e:
                swap_status = 'load_failed'
                logging.info(
                    '[multiACE] Swap LOAD raised before completion: %s '
                    '(routing to swap_back+pos_restore+pause)' % load_e)
                self._swap_back_to_orig_for_pause(
                    switched_head, orig_ext_name)
                self._restore_pos_for_pause(saved_pos)
                _detail, _steps = self._load_slip_details(
                    head, ace_index, slot)
                self._pause_for_recovery(
                    gcmd, detail_msg=_detail, recovery_steps=_steps)
                return
            load_end_ts = time.monotonic()

            if not self._last_load_ok:
                swap_status = 'load_failed'
                self._swap_back_to_orig_for_pause(
                    switched_head, orig_ext_name)
                self._restore_pos_for_pause(saved_pos)
                _detail, _steps = self._load_slip_details(
                    head, ace_index, slot)
                self._pause_for_recovery(
                    gcmd, detail_msg=_detail, recovery_steps=_steps)
                return

            logging.info('[multiACE] Swap: load done')
            self._swap_phase = 'flush'

            self._auto_feed_enabled = True
            self._fa_context = fa_prev_context if fa_prev_context in ('print', 'load') else 'print'
            try:
                self._arm_fa_for(ace_index, slot)
                self.wait_ace_ready()
                self._v2_schedule_fa_rearm(
                    ace_index, slot, 'post-load-verify', delay=0.20)
                self._fa_trace('gate RE-OPEN for post-load wipe (context=%s) on ACE %d slot %d' % (
                    self._fa_context, ace_index, slot))
            except Exception as fa_e:
                logging.info('[multiACE] post-load FA re-enable failed: %s' % fa_e)

            wipe_temp = saved_heater_target if saved_heater_target >= 170 else swap_temp
            self.gcode.run_script_from_command('M109 S%d' % wipe_temp)
            self.gcode.run_script_from_command('ROUGHLY_CLEAN_NOZZLE_WITH_DISCARD')
            self.toolhead.wait_moves()

            self._fa_log.info(
                '[swap-trace] POST_LOAD last_pos=%.3f delta_from_entry=%+.3f'
                % (gcode_move.last_position[3],
                   gcode_move.last_position[3] - saved_e_last))
            self.gcode.run_script_from_command('G91')
            if anti_ooze > 0:
                self.gcode.run_script_from_command(
                    'G1 E-%.2f F1800' % anti_ooze)
            self.gcode.run_script_from_command('G90')
            self.toolhead.wait_moves()
            self._fa_log.info(
                '[swap-trace] POST_ANTI_OOZE_RETRACT last_pos=%.3f anti_ooze=%.2f'
                % (gcode_move.last_position[3], anti_ooze))

            if wipe_temp != saved_heater_target:
                self.gcode.run_script_from_command('M104 S%d' % saved_heater_target)
                if saved_heater_target >= 190:
                    self.gcode.run_script_from_command('M109 S%d' % saved_heater_target)
            logging.info('[multiACE] Swap: heater target restored=%d (wipe was %d)'
                         % (saved_heater_target, wipe_temp))

            if self.swap_post_retract_wipe:
                self.gcode.run_script_from_command(
                    'INNER_DISCARD_FILAMENT_BASE_DISCARD')
                self.toolhead.wait_moves()

            if switched_head:
                orig_head = 0 if orig_ext_name == 'extruder' else int(
                    orig_ext_name.replace('extruder', ''))
                logging.info('[multiACE] Swap: switching back to %s' % orig_ext_name)
                self.gcode.run_script_from_command('T%d A0' % orig_head)
                self.toolhead.wait_moves()

            e_diff = gcode_move.last_position[3] - saved_e_last
            gcode_move.base_position[3] = saved_e_base + e_diff
            self._fa_log.info(
                '[swap-trace] E_DIFF_ADJUST last_pos=%.3f saved_e_last=%.3f '
                'e_diff=%+.3f new_base=%.3f'
                % (gcode_move.last_position[3], saved_e_last,
                   e_diff, gcode_move.base_position[3]))

            self.gcode.run_script_from_command('G90')
            self.gcode.run_script_from_command('G0 Z%.3f F600' % (saved_pos[2] + 3.0))
            self.gcode.run_script_from_command('G0 Y%.3f F12000' % saved_pos[1])
            self.gcode.run_script_from_command('G0 X%.3f F12000' % saved_pos[0])
            self.gcode.run_script_from_command('G0 Z%.3f F600' % (saved_pos[2] + 2.0))
            self.toolhead.wait_moves()

            if saved_absolute:
                self.gcode.run_script_from_command('G90')

            self.gcode.run_script_from_command('G1 F%d' % (saved_speed * 60))
            self._fa_log.info(
                '[swap-trace] EXIT last_pos=%.3f base=%.3f '
                'slicer_view_e=%.3f (= last_pos - base)'
                % (gcode_move.last_position[3],
                   gcode_move.base_position[3],
                   gcode_move.last_position[3] - gcode_move.base_position[3]))

            logging.info('[multiACE] Swap: restored pos X=%.2f Y=%.2f Z=%.2f (+2mm travel hop)' % (
                saved_pos[0], saved_pos[1], saved_pos[2]))

            self._swap_phase = 'done'
            self._last_swap_result = {
                'head': head, 'ace': ace_index, 'slot': slot,
                'status': 'ok', 'ts': self.reactor.monotonic(),
            }
            self._ace_event('slot_ready', head=head, ace=ace_index, slot=slot)
            self._ace_event('swap_done', head=head, ace=ace_index, slot=slot,
                            status='ok')

            self.log_always(self._t('msg.swap_complete',
                head=self._disp(head), ace=self._disp(ace_index), slot=self._disp(slot)))
        finally:
            self._swap_in_progress = False
            self._swap_saved_pos = None

            if self._swap_phase != 'done':
                swap_fail_status = (swap_status
                                    if swap_status != 'ok' else 'error')
                self._last_swap_result = {
                    'head': head, 'ace': ace_index, 'slot': slot,
                    'status': swap_fail_status,
                    'ts': self.reactor.monotonic(),
                }
                self._ace_event('swap_failed', head=head, ace=ace_index,
                                slot=slot, status=swap_fail_status)
            self._swap_phase = 'idle'

            self._auto_feed_enabled = fa_prev_auto
            self._fa_context = fa_prev_context

            if fa_prev_auto:
                try:
                    active_ext = self.toolhead.get_extruder().get_name()
                    active_head = (0 if active_ext == 'extruder'
                                   else int(active_ext.replace('extruder', '')))
                    active_source = self._head_source.get(active_head)
                    if active_source is not None:
                        self._arm_fa_for(
                            active_source['ace_index'], active_source['slot'])
                    else:
                        logging.info(
                            '[multiACE] post-swap FA: active head %d has no head_source, skipping start' % active_head)
                except Exception as e:
                    logging.info('[multiACE] post-swap FA start failed: %s' % e)
            self._fa_trace('gate restored (context=%s auto=%s) after ACE_SWAP_HEAD'
                           % (fa_prev_context, fa_prev_auto))
            self._audit_state('SWAP_HEAD', {'head': head, 'ace': ace_index, 'slot': slot})

            def _dur_ms(start, end):
                if start is None or end is None:
                    return None
                return int((end - start) * 1000)
            swap_end_ts = time.monotonic()
            self._telemetry('SWAP_SUMMARY', {
                'head': head,
                'from_ace': prev_ace_src,
                'from_slot': prev_slot_src,
                'to_ace': ace_index,
                'to_slot': slot,
                'status': swap_status,
                'total_ms': _dur_ms(swap_start_ts, swap_end_ts),
                'unload_ms': _dur_ms(unload_start_ts, unload_end_ts),
                'load_ms': _dur_ms(load_start_ts, load_end_ts),
                'context': fa_prev_context,
            })

    def _switch_ace_for_head_target(self, ace_index):
        if ace_index == self._active_device_index:
            self._audit_state('SWITCH_TARGET_NOOP', {
                'target_ace': ace_index, 'reason': 'already_active'})
            return True
        if ace_index < 0 or ace_index >= len(self._ace_devices):
            self._audit_state('SWITCH_TARGET_FAILED', {
                'target_ace': ace_index, 'reason': 'ace_out_of_range'})
            return False

        if not self._connected_per_ace.get(ace_index, False):
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if self._connected_per_ace.get(ace_index, False):
                    break
                self.reactor.pause(self.reactor.monotonic() + 0.2)
            if not self._connected_per_ace.get(ace_index, False):
                self._audit_state('SWITCH_TARGET_FAILED', {
                    'target_ace': ace_index, 'reason': 'not_connected'})
                return False

        self._set_active_idx(ace_index)
        self._audit_state('SWITCH_TARGET', {'target_ace': ace_index})
        return True

    cmd_ACE_HEAD_STATUS_help = '[multiACE] Show active ACE, detected devices, and head-to-ACE/slot mapping'
    def cmd_ACE_HEAD_STATUS(self, gcmd):

        try:
            ace_mtime = os.path.getmtime(os.path.abspath(__file__))
            from datetime import datetime
            ts = datetime.fromtimestamp(ace_mtime).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            ts = 'unknown'
        self.log_always(self._t('msg.version_file',
            version=MULTIACE_VERSION, codename=MULTIACE_CODENAME,
            build=MULTIACE_BUILD_TAG, ts=ts))

        actual_bundle = self._compute_bundle_sha1()
        expected_bundle = MULTIACE_BUNDLE_SHA1
        marker = 'MATCH' if expected_bundle == actual_bundle else 'MISMATCH'
        self.log_always(self._t('msg.bundle_status',
            expected=expected_bundle, actual=actual_bundle, marker=marker))

        _bg = self.printer.lookup_object('ace_bg_swap', None)
        if _bg is not None:
            _bg_heads = sorted(getattr(_bg, 'enabled_heads', []) or [])
            self.log_always('[multiACE] bg-swap %s: enabled heads %s'
                            % (getattr(_bg, 'version', '?'),
                               _bg_heads if _bg_heads else 'NONE'))
        else:
            self.log_always('[multiACE] bg-swap: not loaded '
                            '(no [ace_bg_swap] section)')

        s = self._usb_stats
        uptime_min = (time.monotonic() - s['start_time']) / 60.0
        self.log_always(self._t('msg.usb_stats_summary',
            uptime=uptime_min,
            errno5=s['errno5_total'], recovered=s['errno5_recovered'],
            lost=s['errno5_unrecovered'], cascades=s['cascades'],
            connects=s['connects'], disconnects=s['disconnects']))

        device_count = len(self._ace_devices)
        if device_count == 0:
            self.log_always(self._t('msg.no_ace_devices_detected'))
            return
        self.log_always(self._t('msg.active_ace_of',
            active=self._disp(self._active_device_index), count=device_count))

        for i, device in enumerate(self._ace_devices):
            marker = ' << ACTIVE' if i == self._active_device_index else ''
            protocol_cls = self._ace_path_protocol.get(device)
            proto_name = protocol_cls.NAME if protocol_cls else '?'
            model, firmware = self._ace_models.get(i, ('?', '?'))
            self.log_always(self._t('msg.ace_list_line',
                ace=self._disp(i), proto=proto_name, device=device,
                model=model, firmware=firmware, marker=marker))
            ds = (self._info_per_ace.get(i, {}) or {}).get('dryer_status', {}) or {}
            d_status = ds.get('status', '?')
            if d_status not in ('stop', 'free', '?'):
                self.log_always(
                    '    dryer: %s (target %s C, remain %s min)' % (
                        d_status, ds.get('target_temp', '?'),
                        ds.get('remain_time', '?')))
            else:
                self.log_always('    dryer: %s' % d_status)

        self.log_always(self._t('msg.head_source_mapping'))
        any_loaded = False
        for head in range(4):
            source = self._head_source[head]
            if source:
                any_loaded = True
                self.log_always(self._t('msg.head_mapping_line',
                    head=self._disp(head),
                    ace=self._disp(source['ace_index']),
                    slot=self._disp(source['slot']),
                    brand=source.get('brand', ''),
                    type=source.get('type', ''),
                    color=source.get('color', '')))
            else:
                self.log_always(self._t('msg.head_mapping_empty', head=self._disp(head)))
        if not any_loaded:
            self.log_always(self._t('msg.head_mapping_none'))

    def _v2_resolve_ace(self, gcmd):
        idx = gcmd.get_int('ACE', -1)
        if idx < 0:

            active = self._active_device_index
            active_proto = self._protocols.get(active)
            if (active_proto is not None
                    and getattr(active_proto, 'NAME', None) == 'v2'):
                idx = active
        if idx < 0:

            for i, proto in self._protocols.items():
                if proto is not None and getattr(proto, 'NAME', None) == 'v2':
                    idx = i
                    break
        if idx < 0:
            raise gcmd.error('No V2 ACE detected - connect device or pass ACE=<idx>')
        proto = self._protocols.get(idx)
        if proto is None or getattr(proto, 'NAME', None) != 'v2':
            raise gcmd.error('ACE %d is not a V2 device' % idx)
        return idx

    def _v2_dispatch_and_wait(self, gcmd, idx, method, params, timeout=3.0):
        captured = {'response': None, 'done': False}

        def cb(self, response):
            captured['response'] = response
            captured['done'] = True

        try:
            self.send_request_to(idx, {
                'method': method, 'params': params,
            }, cb)
        except Exception as e:
            raise gcmd.error('V2 dispatch failed: %s' % e)

        reactor = self.printer.get_reactor()
        deadline = reactor.monotonic() + timeout
        while not captured['done'] and reactor.monotonic() < deadline:
            reactor.pause(reactor.monotonic() + 0.05)

        if not captured['done']:
            gcmd.respond_info(self._t('msg.v2_response_timeout',
                method=method, timeout=timeout))
            return None
        resp = captured['response']
        try:
            text = json.dumps(resp, default=str, sort_keys=True)
        except Exception:
            text = repr(resp)
        gcmd.respond_info(self._t('msg.v2_response_text',
            method=method, text=text))
        return resp

    cmd_A_DISCOVER_help = '[multiACE] V2 cmd 0 DISCOVER_DEVICE. Usage: A_DISCOVER [ACE=0]'
    def cmd_A_DISCOVER(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'discover_device', {})

    cmd_A_INFO_help = '[multiACE] V2 cmd 7 GET_INFO. Usage: A_INFO [ACE=0]'
    def cmd_A_INFO(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_info', {})

    cmd_A_STATUS_help = '[multiACE] V2 cmd 6 GET_STATUS. Usage: A_STATUS [ACE=0]'
    def cmd_A_STATUS(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_status', {})

    cmd_A_TEMP_help = '[multiACE] V2 cmd 64 GET_TEMP. Usage: A_TEMP [ACE=0]'
    def cmd_A_TEMP(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_temp', {})

    cmd_A_FEEDINFO_help = '[multiACE] V2 cmd 76 GET_FEED_INFO. Usage: A_FEEDINFO [ACE=0]'
    def cmd_A_FEEDINFO(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_feed_info', {})

    cmd_A_KEYSTATE_help = '[multiACE] V2 cmd 73 GET_KEY_STATE. Usage: A_KEYSTATE [ACE=0]'
    def cmd_A_KEYSTATE(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_key_state', {})

    cmd_A_FILAMENT_help = '[multiACE] V2 cmd 13 GET_FILAMENT_INFO (vendor-named; may return cached value). Usage: A_FILAMENT [ACE=0] [SLOT=0]'
    def cmd_A_FILAMENT(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        self._v2_dispatch_and_wait(gcmd, idx, 'get_filament_info', {'index': slot})

    cmd_A_FILAMENT_IDENTIFY_help = '[multiACE] V2 cmd 68 FILAMENT_IDENTIFY (suspected live RFID scan). Usage: A_FILAMENT_IDENTIFY [ACE=0] [SLOT=0]'
    def cmd_A_FILAMENT_IDENTIFY(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        self._v2_dispatch_and_wait(gcmd, idx, 'filament_identify', {'index': slot})

    cmd_A_RFID_TEST_help = '[multiACE] V2 cmd 69 RFID_TEST. Usage: A_RFID_TEST [ACE=0] [ENABLE=1]'
    def cmd_A_RFID_TEST(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        enable = bool(gcmd.get_int('ENABLE', 1))
        self._v2_dispatch_and_wait(gcmd, idx, 'rfid_test', {'enable': enable})

    cmd_A_RFID_help = '[multiACE] V2 cmd 14 SET_RFID_ENABLE. Usage: A_RFID [ACE=0] [SLOT=0] [ENABLE=1]'
    def cmd_A_RFID(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        enable = bool(gcmd.get_int('ENABLE', 1))
        self._v2_dispatch_and_wait(gcmd, idx, 'set_rfid_enable',
                                   {'index': slot, 'enable': enable})

    cmd_A_FEED_help = '[multiACE] V2 cmd 8 FEED_OR_ROLLBACK. Usage: A_FEED [ACE=0] SLOT=0 [SPEED=100] [LENGTH=200] [MODE=0]  (mode 0=feed, 1=rollback, 2=assist, 3=rollback_assist)'
    def cmd_A_FEED(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        speed = gcmd.get_int('SPEED', 100)
        length = gcmd.get_int('LENGTH', 200)
        mode = gcmd.get_int('MODE', 0)
        self._v2_dispatch_and_wait(gcmd, idx, 'feed_or_rollback_raw', {
            'index': slot, 'speed': speed, 'length': length, 'mode': mode,
        })

    cmd_A_ROLLBACK_help = '[multiACE] V2 cmd 8 FEED_OR_ROLLBACK mode=1. Usage: A_ROLLBACK [ACE=0] SLOT=0 [SPEED=50] [LENGTH=100]'
    def cmd_A_ROLLBACK(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        speed = gcmd.get_int('SPEED', 50)
        length = gcmd.get_int('LENGTH', 100)
        self._v2_dispatch_and_wait(gcmd, idx, 'feed_or_rollback_raw', {
            'index': slot, 'speed': speed, 'length': length, 'mode': 1,
        })

    cmd_A_STOP_help = '[multiACE] V2 cmd 9 STOP_FEED_OR_ROLLBACK. Usage: A_STOP [ACE=0] SLOT=0'
    def cmd_A_STOP(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        self._v2_dispatch_and_wait(gcmd, idx, 'stop_feed_assist',
                                   {'index': slot})

    cmd_A_SPEED_help = '[multiACE] V2 cmd 10 UPDATE_SPEED. Usage: A_SPEED [ACE=0] SLOT=0 SPEED=100'
    def cmd_A_SPEED(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        slot = gcmd.get_int('SLOT', 0)
        speed = gcmd.get_int('SPEED')
        self._v2_dispatch_and_wait(gcmd, idx, 'update_feeding_speed',
                                   {'index': slot, 'speed': speed})

    cmd_A_DRY_help = '[multiACE] V2 cmd 11 DRYING. Usage: A_DRY [ACE=0] [TEMP=50] [DURATION=120] [AUTO_ROLL=1]'
    def cmd_A_DRY(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        temp = gcmd.get_int('TEMP', 50)
        duration = gcmd.get_int('DURATION', 120)
        auto_roll = bool(gcmd.get_int('AUTO_ROLL', 1))
        self._v2_dispatch_and_wait(gcmd, idx, 'drying_raw', {
            'temp': temp, 'duration': duration, 'auto_roll': auto_roll,
        })

    cmd_A_DRYSTOP_help = '[multiACE] V2 cmd 11 DRYING (stop). Usage: A_DRYSTOP [ACE=0]'
    def cmd_A_DRYSTOP(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        self._v2_dispatch_and_wait(gcmd, idx, 'drying_stop', {})

    cmd_A_DRYTEMP_help = '[multiACE] V2 cmd 12 SET_DRY_TEMP. Usage: A_DRYTEMP [ACE=0] TEMP=50'
    def cmd_A_DRYTEMP(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        temp = gcmd.get_int('TEMP')
        self._v2_dispatch_and_wait(gcmd, idx, 'set_dry_temp', {'temp': temp})

    cmd_A_FAN_help = '[multiACE] V2 cmd 71 SET_FAN. Usage: A_FAN [ACE=0] [SPEED=0] [FAN1=0] [FAN2=0]'
    def cmd_A_FAN(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        speed = gcmd.get_int('SPEED', 0)
        fan1 = bool(gcmd.get_int('FAN1', 0))
        fan2 = bool(gcmd.get_int('FAN2', 0))
        self._v2_dispatch_and_wait(gcmd, idx, 'set_fan_raw', {
            'speed': speed, 'fan1': fan1, 'fan2': fan2,
        })

    cmd_A_VALVE_help = '[multiACE] V2 cmd 66 SET_VALVE. Usage: A_VALVE [ACE=0] [V1=0] [V2=0]'
    def cmd_A_VALVE(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        v1 = bool(gcmd.get_int('V1', 0))
        v2 = bool(gcmd.get_int('V2', 0))
        self._v2_dispatch_and_wait(gcmd, idx, 'set_valve', {'v1': v1, 'v2': v2})

    cmd_A_FEEDCHECK_help = '[multiACE] V2 cmd 19 SET_FEED_CHECK. Usage: A_FEEDCHECK [ACE=0] [CHECK=254] [ERROR=254]  (default 254/254 = disabled; hakimio table: 100/90 gklib, 200/185 recommended, 200/196 aggressive, 254/254 disabled)'
    def cmd_A_FEEDCHECK(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        check_len = gcmd.get_int('CHECK', 254)
        error_len = gcmd.get_int('ERROR', 254)
        self._v2_dispatch_and_wait(gcmd, idx, 'set_feed_check', {
            'check_length': check_len, 'error_length': error_len,
        })

    cmd_A_RAW_help = '[multiACE] V2 raw cmd. Usage: A_RAW [ACE=0] CMD=<id> [HEX=<protobuf hex>]'
    def cmd_A_RAW(self, gcmd):
        idx = self._v2_resolve_ace(gcmd)
        cmd_id = gcmd.get_int('CMD')
        hex_payload = gcmd.get('HEX', '')
        self._v2_dispatch_and_wait(gcmd, idx, 'raw', {
            'cmd': cmd_id, 'hex': hex_payload,
        })

    cmd_ACE_DWELL_TEST_help = '[multiACE] Test V2 mode=3 routing with varying dwells between stop and mode=3. Usage: ACE_DWELL_TEST [ACE=2] [SLOT=2] [DWELLS=50,100,250,500,1000,2000]'
    def cmd_ACE_DWELL_TEST(self, gcmd):
        idx = gcmd.get_int('ACE', 2)
        slot = gcmd.get_int('SLOT', 2)
        dwells_str = gcmd.get('DWELLS', '50,100,250,500,1000,2000')
        try:
            dwells = [int(x.strip()) for x in dwells_str.split(',')
                      if x.strip()]
        except ValueError:
            raise gcmd.error(
                '[ACE_DWELL_TEST] DWELLS must be comma-separated ints (ms)')
        if not dwells:
            raise gcmd.error('[ACE_DWELL_TEST] no dwell values')
        proto = self._protocols.get(idx)
        if proto is None or getattr(proto, 'NAME', None) != 'v2':
            raise gcmd.error(
                '[ACE_DWELL_TEST] ACE %d not present or not V2' % idx)
        if not (0 <= slot <= 3):
            raise gcmd.error('[ACE_DWELL_TEST] SLOT must be 0..3')

        def _noop_cb(self, response):
            pass

        def _read_slot_status(target):
            info = self._info_per_ace.get(idx) or {}
            for s in info.get('slots') or []:
                if s.get('index') == target:
                    return s.get('slot_status', '?')
            return '?'

        def _snapshot_all():
            info = self._info_per_ace.get(idx) or {}
            parts = []
            for s_idx in range(4):
                st = '?'
                for s in info.get('slots') or []:
                    if s.get('index') == s_idx:
                        st = s.get('slot_status', '?')
                        break
                parts.append('%d=%s' % (s_idx, st))
            return ' '.join(parts)

        def _info(msg):
            gcmd.respond_info('[ACE_DWELL_TEST] ' + msg)
            self._fa_log.info('[dwell-test] ' + msg)

        _info('start ACE=%d SLOT=%d dwells=%s ms' % (idx, slot, dwells))
        _info('baseline: %s' % _snapshot_all())

        results = []
        for dwell_ms in dwells:
            _info('--- DWELL=%d ms ---' % dwell_ms)

            self.send_request_to(idx, {
                'method': 'start_feed_assist',
                'params': {'index': slot, 'speed': 10},
            }, _noop_cb)
            self.wait_ace_ready()
            self.dwell(1.0)
            s_after_start = _read_slot_status(slot)
            _info('  after start_feed_assist: slot %d=%s | %s' % (
                slot, s_after_start, _snapshot_all()))

            self.send_request_to(idx, {
                'method': 'stop_feed_assist',
                'params': {'index': slot},
            }, _noop_cb)
            self.wait_ace_ready()

            self.dwell(dwell_ms / 1000.0)
            s_after_dwell = _read_slot_status(slot)
            _info('  after stop+dwell(%d ms): slot %d=%s | %s' % (
                dwell_ms, slot, s_after_dwell, _snapshot_all()))

            self.send_request_to(idx, {
                'method': 'feed_or_rollback_raw',
                'params': {'index': slot, 'speed': 10,
                           'length': 0, 'mode': 3},
            }, _noop_cb)
            self.wait_ace_ready()
            self.dwell(0.7)

            snap = _snapshot_all()
            target_status = _read_slot_status(slot)
            slot0_status = _read_slot_status(0)
            if target_status == 'rollback_assisting':
                verdict = 'OK (slot %d -> rollback_assisting)' % slot
                ok = True
            elif slot0_status == 'rollback_assisting' and slot != 0:
                verdict = ('MISROUTED (slot 0 got it, slot %d=%s)'
                           % (slot, target_status))
                ok = False
            else:
                verdict = ('UNKNOWN (slot %d=%s slot 0=%s)'
                           % (slot, target_status, slot0_status))
                ok = False
            _info('  after mode=3: %s | %s' % (verdict, snap))
            results.append((dwell_ms, ok, verdict))

            for s_idx in range(4):
                self.send_request_to(idx, {
                    'method': 'stop_feed_assist',
                    'params': {'index': s_idx},
                }, _noop_cb)
            self.wait_ace_ready()
            self.dwell(2.0)
            _info('  cleanup done: %s' % _snapshot_all())

        _info('=== SUMMARY ===')
        for dwell_ms, ok, verdict in results:
            _info('  dwell=%4d ms : %s' % (dwell_ms, verdict))
        ok_count = sum(1 for _, ok, _ in results if ok)
        _info('=== %d/%d dwells routed correctly ===' % (
            ok_count, len(results)))

    cmd_ACE_MULTI_SLOT_TEST_help = '[multiACE] Test V2 multi-slot FA + concurrent transport (background-preload scenario). Usage: ACE_MULTI_SLOT_TEST [ACE=2] [FA_SLOT=2] [XPORT_SLOT=0] [XPORT_LEN=30] [XPORT_SPEED=20]'
    def cmd_ACE_MULTI_SLOT_TEST(self, gcmd):
        idx = gcmd.get_int('ACE', 2)
        fa_slot = gcmd.get_int('FA_SLOT', 2)
        xport_slot = gcmd.get_int('XPORT_SLOT', 0)
        xport_len = gcmd.get_int('XPORT_LEN', 30)
        xport_speed = gcmd.get_int('XPORT_SPEED', 20)

        proto = self._protocols.get(idx)
        if proto is None or getattr(proto, 'NAME', None) != 'v2':
            raise gcmd.error(
                '[ACE_MULTI_SLOT_TEST] ACE %d not present or not V2' % idx)
        if not (0 <= fa_slot <= 3):
            raise gcmd.error('[ACE_MULTI_SLOT_TEST] FA_SLOT must be 0..3')
        if not (0 <= xport_slot <= 3):
            raise gcmd.error('[ACE_MULTI_SLOT_TEST] XPORT_SLOT must be 0..3')
        if fa_slot == xport_slot:
            raise gcmd.error(
                '[ACE_MULTI_SLOT_TEST] FA_SLOT and XPORT_SLOT must differ')
        if xport_len < 1:
            raise gcmd.error('[ACE_MULTI_SLOT_TEST] XPORT_LEN must be >= 1')
        if xport_speed < 1:
            raise gcmd.error('[ACE_MULTI_SLOT_TEST] XPORT_SPEED must be >= 1')

        def _noop_cb(self, response):
            pass

        def _slot_status(target):
            info = self._info_per_ace.get(idx) or {}
            for s in info.get('slots') or []:
                if s.get('index') == target:
                    return s.get('slot_status', '?')
            return '?'

        def _snapshot_all():
            info = self._info_per_ace.get(idx) or {}
            parts = []
            for s_idx in range(4):
                st = '?'
                for s in info.get('slots') or []:
                    if s.get('index') == s_idx:
                        st = s.get('slot_status', '?')
                        break
                parts.append('%d=%s' % (s_idx, st))
            return ' '.join(parts)

        def _info(msg):
            gcmd.respond_info('[ACE_MULTI_SLOT_TEST] ' + msg)
            self._fa_log.info('[multi-test] ' + msg)

        _info('start ACE=%d FA_SLOT=%d XPORT_SLOT=%d XPORT_LEN=%d mm @%d mm/s'
              % (idx, fa_slot, xport_slot, xport_len, xport_speed))
        _info('baseline: %s' % _snapshot_all())

        _info('--- step 1: start_feed_assist slot=%d ---' % fa_slot)
        self.send_request_to(idx, {
            'method': 'start_feed_assist',
            'params': {'index': fa_slot, 'speed': 10},
        }, _noop_cb)
        self.dwell(1.5)
        fa_status_1 = _slot_status(fa_slot)
        _info('  after arm FA: slot %d=%s | %s'
              % (fa_slot, fa_status_1, _snapshot_all()))
        if fa_status_1 != 'assisting':
            _info('!! FA arm did not reach `assisting` - aborting test')
            for s_idx in range(4):
                self.send_request_to(idx, {
                    'method': 'stop_feed_assist',
                    'params': {'index': s_idx},
                }, _noop_cb)
            return

        _info('--- step 2: feed_filament slot=%d length=%d ---'
              % (xport_slot, xport_len))
        self.send_request_to(idx, {
            'method': 'feed_filament',
            'params': {'index': xport_slot,
                       'length': xport_len,
                       'speed': xport_speed},
        }, _noop_cb)

        self.dwell(0.6)
        fa_status_2a = _slot_status(fa_slot)
        xport_status_2a = _slot_status(xport_slot)
        _info('  during transport (t+0.6s): FA slot %d=%s, XPORT slot %d=%s | %s'
              % (fa_slot, fa_status_2a, xport_slot, xport_status_2a,
                 _snapshot_all()))

        transport_time = xport_len / float(max(1, xport_speed))
        self.dwell(transport_time + 1.5)
        fa_status_2b = _slot_status(fa_slot)
        xport_status_2b = _slot_status(xport_slot)
        _info('  after transport (t+%.1fs total): FA slot %d=%s, XPORT slot %d=%s | %s'
              % (0.6 + transport_time + 1.5, fa_slot, fa_status_2b,
                 xport_slot, xport_status_2b, _snapshot_all()))

        _info('--- step 3: start_feed_assist slot=%d (FA slot %d still armed) ---'
              % (xport_slot, fa_slot))
        self.send_request_to(idx, {
            'method': 'start_feed_assist',
            'params': {'index': xport_slot, 'speed': 10},
        }, _noop_cb)
        self.dwell(1.5)
        fa_status_3 = _slot_status(fa_slot)
        xport_status_3 = _slot_status(xport_slot)
        both_armed = (fa_status_3 == 'assisting'
                      and xport_status_3 == 'assisting')
        _info('  after arm both: FA slot %d=%s, XPORT slot %d=%s | %s'
              % (fa_slot, fa_status_3, xport_slot, xport_status_3,
                 _snapshot_all()))

        _info('=== VERDICT ===')
        _info('  FA-survives-transport (slot %d stayed assisting during slot %d feed): %s'
              % (fa_slot, xport_slot, 'YES' if fa_status_2a == 'assisting' else 'NO'))
        _info('  Concurrent transport+FA (slot %d=feeding while slot %d=assisting): %s'
              % (xport_slot, fa_slot,
                 'YES' if (xport_status_2a == 'feeding'
                           and fa_status_2a == 'assisting') else 'NO'))
        _info('  Both-armed-simultaneously (slot %d + slot %d both assisting): %s'
              % (fa_slot, xport_slot, 'YES' if both_armed else 'NO'))

        _info('--- cleanup ---')
        for s_idx in range(4):
            self.send_request_to(idx, {
                'method': 'stop_feed_assist',
                'params': {'index': s_idx},
            }, _noop_cb)
        self.dwell(2.0)
        _info('cleanup done: %s' % _snapshot_all())

    cmd_ACE_CLEAR_HEADS_help = '[multiACE] Clear head-to-ACE/slot mapping and display info. Usage: ACE_CLEAR_HEADS [HEAD=0]'
    def cmd_ACE_CLEAR_HEADS(self, gcmd):
        head = gcmd.get_int('HEAD', -1)
        if head >= 0:
            if head > 3:
                raise gcmd.error('[multiACE] HEAD must be 0-3')
            self._head_source[head] = None
            self._clear_filament_display(head)
            self.log_always(self._t('msg.cleared_head_mapping', head=self._disp(head)))
        else:
            self._head_source = {0: None, 1: None, 2: None, 3: None}
            for h in range(4):
                self._clear_filament_display(h)
            self.log_always(self._t('msg.cleared_all_head_mappings'))
        self._save_head_source()
        self._audit_state('CLEAR_HEADS', {'head': head})
        self._sync_ptc_to_active_ace()

    def _push_slot_rfid_to_extruder(self, head):
        if not self.head_uses_ace(head):
            return
        try:
            slots = self._info.get('slots', [{}] * 4)
            if head < 0 or head >= len(slots):
                return
            si = slots[head]
            if si.get('rfid') != 2:
                return

            ov = self._override_for(self._active_device_index, head)
            if ov is not None:
                push_type    = ov.get('material') or si.get('type', 'PLA')
                push_color   = self._override_color_to_rgba(ov.get('color', ''))
                push_brand   = ov.get('brand') or si.get('brand', 'Generic')
                push_subtype = ov.get('subtype', '') or ''
            else:
                push_type    = si.get('type', 'PLA')
                push_color   = self.rgb2hex(*si.get('color', (0, 0, 0)))
                push_brand   = si.get('brand', 'Generic')
                push_subtype = si.get('subtype', '')
            self._expect_ptc_push(head, push_type, push_color, push_brand, push_subtype)
            self.gcode.run_script_from_command(
                'SET_PRINT_FILAMENT_CONFIG '
                'CONFIG_EXTRUDER=%d '
                'FILAMENT_TYPE="%s" '
                'FILAMENT_COLOR_RGBA=%s '
                'VENDOR="%s" '
                'FILAMENT_SUBTYPE="%s"' % (
                    head, push_type, push_color, push_brand, push_subtype))
        except Exception as e:
            logging.info(
                '[multiACE] _push_slot_rfid_to_extruder(%d) failed: %s' % (head, e))

    def _clear_filament_display(self, head):
        try:
            self._expect_ptc_push(head, '', '00000000', '', '')
            self.gcode.run_script_from_command(
                'SET_PRINT_FILAMENT_CONFIG '
                'CONFIG_EXTRUDER=%d '
                'FILAMENT_TYPE="" '
                'FILAMENT_COLOR_RGBA=00000000 '
                'VENDOR="" '
                'FILAMENT_SUBTYPE=""' % head)
        except Exception:
            pass

    cmd_ACE_UNLOAD_ALL_HEADS_help = '[multiACE] Unload all toolheads that have filament loaded'
    def cmd_ACE_UNLOAD_ALL_HEADS(self, gcmd):

        if self._feed_assist_index != -1:
            self._disable_feed_assist()
            self.wait_ace_ready()

        unloaded_any = False
        for head in range(4):
            if self.head_is_manual(head):
                continue
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            if not sensor or not sensor.get_status(0)['filament_detected']:
                continue

            source = self._head_source.get(head)
            if source and source['ace_index'] != self._active_device_index:
                logging.info(self._t('msg.switching_ace_for_retract',
                    ace=self._disp(source['ace_index']), head=self._disp(head)))
                switched = False
                for attempt in range(5):
                    if self._switch_ace_for_head_target(source['ace_index']):
                        switched = True
                        break
                    self.log_always(self._t('msg.ace_not_reachable_attempt',
                        ace=self._disp(source['ace_index']),
                        attempt=attempt + 1))
                    time.sleep(1.0)
                if not switched:
                    self.log_error(self._t('msg.ace_failed_after_retries',
                        ace=self._disp(source['ace_index']), head=self._disp(head)))
                    continue

            self.log_always(self._t('msg.unloading_head_only', head=self._disp(head)))
            module, channel = self.EXTRUDER_MAP[head]

            self._audit_state('UNLOAD_ALL_STEP', {
                'head': head,
                'active_device': self._active_device_index,
                'expected_ace': source['ace_index'] if source else None,
                'expected_slot': source['slot'] if source else None,
            })

            try:
                self.gcode.run_script_from_command(
                    "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=prepare" % (module, channel, head))
                self.gcode.run_script_from_command(
                    "FEED_AUTO MODULE=%s CHANNEL=%d EXTRUDER=%d UNLOAD=1 STAGE=doing" % (module, channel, head))
            except Exception as e:
                self.log_always(self._t('msg.unload_head_failed_warn',
                    head=self._disp(head), error=str(e)))

            machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
            if machine_state_manager is not None:
                self._machine_state_after_feed_op()

            still = sensor.get_status(0)['filament_detected']
            if still:
                self.log_always(self._t('msg.unload_head_failed_warn',
                    head=self._disp(head),
                    error='toolhead sensor still detects filament - '
                          'keeping head_source for a retry'))
            else:
                self._head_source[head] = None
                self._push_slot_rfid_to_extruder(head)
                unloaded_any = True

        if unloaded_any:
            self._save_head_source()

            if self._active_device_index != 0 and len(self._ace_devices) > 0:
                logging.info(self._t('msg.switching_back_ace0'))
                self._switch_ace_for_head_target(0)

            self._push_rfid_info()
            self._sync_ptc_to_active_ace()
            self.log_always(self._t('msg.all_heads_unloaded'))
        else:
            self.log_always(self._t('msg.no_filament_in_any_head'))

        cleared = []
        for h in range(4):
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % h, None)
            detected = sensor and sensor.get_status(0)['filament_detected']
            if not detected and self._head_source.get(h) is not None:
                self._head_source[h] = None
                cleared.append(h)
        if cleared:
            self._save_head_source()
            self._sync_ptc_to_active_ace()
            self._push_rfid_info()
            logging.info(self._t('msg.cleared_stale_head_source',
                heads=', '.join('T%d' % h for h in cleared)))

        self._audit_state('UNLOAD_ALL')

    def cmd_ACE_TEST_CANCEL(self, gcmd):
        self._test_cancel = True
        self.log_always(self._t('msg.test_cancel_requested'))

    cmd_ACE_DRY_help = '[multiACE] Start drying on ACE. Usage: ACE_DRY ACE=0 [TEMP=] [DURATION=]'
    def cmd_ACE_DRY(self, gcmd):

        ace_idx = gcmd.get_int('ACE')
        if ace_idx < 0 or ace_idx >= len(self._ace_devices):
            self.log_always(self._t('msg.ace_not_available',
                ace=self._disp(ace_idx)))
            return
        temp = gcmd.get_int('TEMP', self.ace_dryer_temp.get(ace_idx, self.dryer_temp))
        duration = gcmd.get_int('DURATION', self.ace_dryer_duration.get(ace_idx, self.dryer_duration))
        self._wait_homing_clear()
        self.gcode.run_script_from_command('ACE_SWITCH TARGET=%d' % ace_idx)
        self.gcode.run_script_from_command('ACE_START_DRYING TEMP=%d DURATION=%d' % (temp, duration))
        self.log_always(self._t('msg.drying_ace_at',
            ace=self._disp(ace_idx), temp=temp, duration=duration))

    cmd_ACE_RUN_MODE_SWITCH_help = '[multiACE] Switch mode: normal (stock), single (one ACE), multi (multi-ACE)'
    def cmd_ACE_RUN_MODE_SWITCH(self, gcmd):
        mode = gcmd.get('MODE', '').lower()
        if mode not in ('normal', 'single', 'multi', 'head'):
            raise gcmd.error('[multiACE] Invalid mode: %s. Use normal, multi, or head.' % mode)
        if mode == 'single':
            mode = 'multi'

        legacy_head = (gcmd.get_int('HEAD', None, minval=0, maxval=3)
                       if mode == 'head' else None)

        current = self._ace_mode

        if mode in ('multi', 'head') and current in ('multi', 'head'):
            self.gcode.run_script_from_command(
                "SAVE_VARIABLE VARIABLE=ace__mode VALUE=\"'%s'\"" % mode)
            self._ace_mode = mode
            if mode == 'head':
                if legacy_head is not None:
                    self._ace_head = legacy_head
                    for h in range(4):
                        self.head_feeder[h] = (h != legacy_head)
                    self._save_head_feeder()
                for h in range(4):
                    if self.head_is_feeder(h) and not self.head_is_manual(h):
                        self._clear_filament_display(h)
            self._restore_head_source()
            self._ensure_extruder_change_handler()
            self.log_always(self._t('msg.switched_to_mode', mode=mode.upper()))
            try:
                self._push_rfid_info()
            except Exception:
                pass
            return

        save_vars = self.printer.lookup_object('save_variables')
        vars_path = save_vars.filename
        script_dir = os.path.dirname(os.path.abspath(vars_path))
        script = os.path.join(script_dir, 'ace_mode_switch.sh')
        if not os.path.exists(script):
            raise gcmd.error('[multiACE] Mode switch script not found: %s' % script)

        file_mode = 'normal' if mode == 'normal' else 'ace'

        self.log_always(self._t('msg.running_mode_switch', mode=mode.upper()))

        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=ace__mode VALUE=\"'%s'\"" % mode)
        if mode == 'head' and legacy_head is not None:
            self._ace_head = legacy_head
            for h in range(4):
                self.head_feeder[h] = (h != legacy_head)
            self._save_head_feeder()

        try:
            import subprocess
            result = subprocess.run(['bash', script, file_mode],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30)
            if result.returncode != 0:
                raise gcmd.error(
                    '[multiACE] Mode switch script failed (rc=%d): %s' % (
                        result.returncode, result.stderr.decode('utf-8', 'replace')))
        except subprocess.TimeoutExpired:
            raise gcmd.error('[multiACE] Mode switch script timed out after 30s')
        except Exception as e:
            raise gcmd.error('[multiACE] Failed to run mode switch script: %s' % str(e))

        self.gcode.run_script_from_command(
            'RAISE_EXCEPTION ID=6666 INDEX=6 CODE=6 MESSAGE="[multiACE] Switched to %s mode. Please reboot!" ONESHOT=0 LEVEL=2' % mode.upper())

        raise gcmd.error(
            '[multiACE] Switched to %s mode. Please reboot the printer to activate!' % mode.upper())

    _UPDATE_SCRIPT = '/home/lava/multiace_update.sh'

    def _run_update_script(self, gcmd, sub_args, timeout):
        if not os.path.isfile(self._UPDATE_SCRIPT):
            raise gcmd.error(
                '[multiACE] Updater script not found at %s - re-run '
                'install_multiace.sh from your repo to install it.'
                % self._UPDATE_SCRIPT)
        cmd = ['bash', self._UPDATE_SCRIPT] + sub_args

        env = os.environ.copy()
        env['MULTIACE_UPDATE_REPO'] = self._update_repo
        env['MULTIACE_UPDATE_PRERELEASE'] = '1' if self._update_prerelease else '0'
        env['MULTIACE_UPDATE_URL_BASE'] = self._update_url_base
        try:
            import subprocess
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise gcmd.error(
                '[multiACE] Updater timed out after %ds' % timeout)
        except Exception as e:
            raise gcmd.error('[multiACE] Updater failed to launch: %s' % e)
        out = (result.stdout or b'').decode('utf-8', 'replace').rstrip()
        for line in out.splitlines():
            self.log_always('[update] %s' % line)
        if result.returncode != 0:
            raise gcmd.error(
                '[multiACE] Updater exited with rc=%d (see log above)'
                % result.returncode)

    def cmd_ACE_UPDATE_CHECK(self, gcmd):
        self._run_update_script(gcmd, ['check'], timeout=30)

    def cmd_ACE_UPDATE_APPLY(self, gcmd):
        force = gcmd.get_int('FORCE', 0)
        sub = ['apply']
        if force:
            sub.append('--force')
        self._run_update_script(gcmd, sub, timeout=600)

    cmd_ACE_LIST_help = 'List all detected ACE devices (up to 4)'

    def cmd_ACE_LIST(self, gcmd):
        if not self._ace_devices:
            self.log_always(self._t('msg.no_ace_devices_detected'))
            return

        self.log_always(self._t('msg.found_n_aces', count=len(self._ace_devices)))
        for i, device in enumerate(self._ace_devices):
            active = ' << ACTIVE' if i == self._active_device_index else ''
            self.log_always(self._t('msg.ace_list_simple',
                ace=self._disp(i), device=device, active=active))

    cmd_ACE_USB_STATS_help = '[multiACE] Show USB connection statistics'
    def cmd_ACE_USB_STATS(self, gcmd):
        s = self._usb_stats
        uptime = time.monotonic() - s['start_time']
        hours = uptime / 3600
        retry_rate = (s['retries'] / s['scans'] * 100) if s['scans'] > 0 else 0
        self.log_always(self._t('msg.usb_stats_header', hours=hours))
        self.log_always(self._t('msg.usb_stats_scans',
            scans=s['scans'], retries=s['retries'], rate=retry_rate))
        self.log_always(self._t('msg.usb_stats_connects',
            connects=s['connects'], failures=s['connect_failures'],
            disconnects=s['disconnects']))

    cmd_ACE_DEBUG_help = '[multiACE] Toggle state audit + telemetry + wiggle logging. Usage: ACE_DEBUG [ENABLE=0|1]'
    def cmd_ACE_DEBUG(self, gcmd):
        enable = gcmd.get_int('ENABLE', -1)
        if enable == -1:
            state = 'enabled' if self._state_debug_enabled else 'disabled'
            self.log_always(self._t('msg.state_debug_status', state=state))
            return
        self._state_debug_enabled = bool(enable)
        self._apply_log_levels()
        state = 'enabled' if self._state_debug_enabled else 'disabled'
        self.log_always(self._t('msg.state_debug_set', state=state))
        self._state_log.info('STATE_DEBUG %s', state)

    cmd_ACE_USB_DEBUG_help = '[multiACE] Toggle USB logging. Usage: ACE_USB_DEBUG [ENABLE=0|1]'
    def cmd_ACE_USB_DEBUG(self, gcmd):
        enable = gcmd.get_int('ENABLE', -1)
        if enable == -1:
            state = 'enabled' if self._usb_debug_enabled else 'disabled'
            self.log_always(self._t('msg.usb_debug_status', state=state))
            return
        self._usb_debug_enabled = bool(enable)
        self._apply_log_levels()
        state = 'enabled' if self._usb_debug_enabled else 'disabled'
        self.log_always(self._t('msg.usb_debug_set', state=state))

    def _file_sha1_short(self, path):
        """Short sha1 of a file on disk - used by ACE_HEAD_STATUS to let
        the user verify each deployed file matches the repo version.
        Returns 'missing' if the file doesn't exist, 'err' on read error."""
        try:
            if not os.path.isfile(path):
                return 'missing'
            h = hashlib.sha1()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()[:7]
        except Exception:
            return 'err'

    def _compute_bundle_sha1(self):
        """Short sha1 computed over the concatenated byte contents of the
        non-ace.py deploy files, in a fixed order that must match the
        BUNDLE_FILES order in multiace/tools/git-hooks/post-commit.

        ACE_HEAD_STATUS compares this runtime value against the baked-in
        MULTIACE_BUNDLE_SHA1 (set by the hook at commit time). Mismatch
        means at least one of the bundled deploy files is stale on disk.

        ace.cfg is intentionally excluded: install_multiace.sh merges
        user values from the existing cfg into the shipped defaults
        (default behavior) or leaves the file untouched (--keep-config),
        so per-install ace.cfg legitimately diverges from the repo
        version. Including it here would produce a false MISMATCH on
        every healthy deploy.
        """
        extras_dir = os.path.dirname(os.path.abspath(__file__))
        kinematics_dir = os.path.join(os.path.dirname(extras_dir), 'kinematics')
        bundle_paths = [
            os.path.join(extras_dir, 'filament_feed.py'),
            os.path.join(extras_dir, 'filament_switch_sensor.py'),
            os.path.join(kinematics_dir, 'extruder.py'),
        ]
        h = hashlib.sha1()
        for p in bundle_paths:
            try:
                with open(p, 'rb') as f:
                    for chunk in iter(lambda: f.read(65536), b''):
                        h.update(chunk)
            except Exception:
                h.update(b'<missing:' + p.encode() + b'>')
        return h.hexdigest()[:7]

    def _read_wheel_counts(self, module, channel):

        try:
            feed = self.printer.lookup_object('filament_feed %s' % module, None)
            if feed is None:
                return None
            return {
                'a': feed.wheel[channel].get_counts(),
                'b': feed.wheel_2[channel].get_counts(),
            }
        except Exception as e:
            logging.info('[multiACE] wheel count read failed: %s', str(e))
            return None

    def _wheel_delta(self, before, after):

        if before is None or after is None:
            return None
        return {
            'a': after['a'] - before['a'],
            'b': after['b'] - before['b'],
        }

    cmd_ACE_SEQ_help = '[multiACE] Run scripted load/unload sequence. PLAN: 0:1=load HEAD:ACE, A0=all from ACE, U=unload all, U0=unload head. UNLOAD=0|1 (default 1) runs final ACE_UNLOAD_ALL_HEADS.'
    def cmd_ACE_SEQ(self, gcmd):

        plan_str = gcmd.get('PLAN', '')
        do_unload = gcmd.get_int('UNLOAD', 1)

        was_debug = self._state_debug_enabled
        self._state_debug_enabled = True
        self._state_log.info('SEQ_START plan="%s" unload=%d', plan_str, do_unload)
        try:
            hs_dump = json.dumps({str(h): self._head_source[h] for h in range(4)})
        except Exception:
            hs_dump = str(self._head_source)
        self._state_log.info('SEQ_START head_source=%s active_device=%d',
                             hs_dump, self._active_device_index)
        self._audit_state('SEQ_START', {'plan': plan_str, 'unload': do_unload})

        steps = []
        if plan_str:
            for item in plan_str.split(','):
                item = item.strip()
                if not item:
                    continue
                if item == 'U':
                    steps.append({'action': 'UNLOAD_ALL'})
                elif item.startswith('U') and item[1:].isdigit():
                    steps.append({'action': 'UNLOAD', 'head': int(item[1:])})
                elif item.startswith('A') and item[1:].isdigit():
                    ace = int(item[1:])
                    for h in range(4):
                        steps.append({'action': 'LOAD', 'head': h, 'ace': ace})
                elif ':' in item:
                    parts = item.split(':')
                    if len(parts) == 2:
                        steps.append({'action': 'LOAD', 'head': int(parts[0]), 'ace': int(parts[1])})
                    else:
                        raise gcmd.error('[multiACE] Invalid PLAN item: %s' % item)
                else:
                    raise gcmd.error('[multiACE] Invalid PLAN item: %s (use HEAD:ACE, A0, U, U0)' % item)
        else:
            self._refresh_ace_devices('seq')
            for i in range(min(len(self._ace_devices), 4)):
                steps.append({'action': 'LOAD', 'head': i, 'ace': i})

        self.log_always(self._t('msg.seq_start',
            steps=len(steps), unload=('yes' if do_unload else 'no')))

        results = []
        step_nr = 0
        for step in steps:
            step_nr += 1
            action = step['action']

            if action == 'LOAD':
                head = step['head']
                ace = step['ace']
                self.log_always(self._t('msg.test_step_load',
                    step=step_nr, total=len(steps),
                    head=self._disp(head), ace=self._disp(ace), slot=self._disp(head)))
                try:
                    self.gcode.run_script_from_command(
                        'ACE_LOAD_HEAD HEAD=%d ACE=%d SLOT=%d' % (head, ace, head))
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % head, None)
                    detected = sensor and sensor.get_status(0)['filament_detected']
                    src = self._head_source.get(head)
                    if detected and src is not None:
                        results.append({'step': step_nr, 'action': 'LOAD', 'status': 'PASS', 'head': head, 'ace': ace})
                        self.log_always(self._t('msg.test_step_load_pass', step=step_nr))
                    else:
                        reason = []
                        if not detected:
                            reason.append('sensor=no_filament')
                        if src is None:
                            reason.append('mapping=missing')
                        results.append({'step': step_nr, 'action': 'LOAD', 'status': 'FAIL',
                                        'head': head, 'ace': ace, 'reason': ', '.join(reason)})
                        self.log_always(self._t('msg.test_step_fail_reasons', step=step_nr, reason=', '.join(reason)))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'LOAD', 'status': 'ERROR',
                                    'head': head, 'ace': ace, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'UNLOAD':
                head = step['head']
                self.log_always(self._t('msg.test_step_unload',
                    step=step_nr, total=len(steps), head=self._disp(head)))
                try:
                    self.gcode.run_script_from_command('ACE_UNLOAD_HEAD HEAD=%d' % head)
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % head, None)
                    still_loaded = sensor and sensor.get_status(0)['filament_detected']
                    if not still_loaded:
                        results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'PASS', 'head': head})
                        self.log_always(self._t('msg.test_step_unload_pass', step=step_nr))
                    else:
                        results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'FAIL',
                                        'head': head, 'reason': 'filament still detected'})
                        self.log_always(self._t('msg.test_step_unload_fail', step=step_nr))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'UNLOAD', 'status': 'ERROR',
                                    'head': head, 'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

            elif action == 'UNLOAD_ALL':
                self.log_always(self._t('msg.test_step_unload_all',
                    step=step_nr, total=len(steps)))
                try:
                    self.gcode.run_script_from_command('ACE_UNLOAD_ALL_HEADS')
                    all_clear = True
                    for h in range(4):
                        sensor = self.printer.lookup_object(
                            'filament_motion_sensor e%d_filament' % h, None)
                        if sensor and sensor.get_status(0)['filament_detected']:
                            all_clear = False
                    if all_clear:
                        results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'PASS'})
                        self.log_always(self._t('msg.test_step_unload_all_pass', step=step_nr))
                    else:
                        results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'FAIL',
                                        'reason': 'filament still detected'})
                        self.log_always(self._t('msg.test_step_unload_fail', step=step_nr))
                except Exception as e:
                    results.append({'step': step_nr, 'action': 'UNLOAD_ALL', 'status': 'ERROR',
                                    'reason': str(e)})
                    self.log_always(self._t('msg.test_step_error', step=step_nr, error=str(e)))
                self.gcode.run_script_from_command('ACE_HEAD_STATUS')

        if do_unload:
            self.log_always(self._t('msg.test_final_unload_all'))
            try:
                self.gcode.run_script_from_command('ACE_UNLOAD_ALL_HEADS')
                all_clear = True
                for h in range(4):
                    sensor = self.printer.lookup_object(
                        'filament_motion_sensor e%d_filament' % h, None)
                    if sensor and sensor.get_status(0)['filament_detected']:
                        all_clear = False
                if all_clear:
                    results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'PASS'})
                    self.log_always(self._t('msg.test_final_pass'))
                else:
                    results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'FAIL',
                                    'reason': 'filament still detected'})
                    self.log_always(self._t('msg.test_final_fail'))
            except Exception as e:
                results.append({'step': 'final', 'action': 'UNLOAD_ALL', 'status': 'ERROR',
                                'reason': str(e)})
                self.log_always(self._t('msg.test_final_error', error=str(e)))

        passed = sum(1 for r in results if r['status'] == 'PASS')
        failed = sum(1 for r in results if r['status'] == 'FAIL')
        errors = sum(1 for r in results if r['status'] == 'ERROR')
        total = len(results)
        self.log_always(self._t('msg.seq_complete',
            passed=passed, total=total, failed=failed, errors=errors))

        result_json = json.dumps(results, default=str)
        self._state_log.info('SEQ_RESULT %s', result_json)

        gcmd.respond_info(self._t('msg.seq_result', json=result_json))
        self._state_debug_enabled = was_debug

    cmd_ACE_PRELOAD_help = '[multiACE] Preload heads from a UI-built plan. Same syntax as ACE_SEQ but UNLOAD defaults to 0 (no final unload).'
    def cmd_ACE_PRELOAD(self, gcmd):

        plan_str = gcmd.get('PLAN', '')
        do_unload = gcmd.get_int('UNLOAD', 0)
        if not plan_str:
            raise gcmd.error('[multiACE] ACE_PRELOAD requires a PLAN parameter')
        self.gcode.run_script_from_command(
            'ACE_SEQ PLAN=%s UNLOAD=%d' % (plan_str, do_unload))

    cmd_MACE_LOG_help = '[multiACE] Emit MSG to klippy.log (diagnostic tracepoint for macros).'
    def cmd_MACE_LOG(self, gcmd):
        msg = gcmd.get('MSG', '')
        logging.info('[mace_log] %s', msg)

    cmd_ACE_FA_TEST_help = (
        '[multiACE] Stress-test FA stop+start across slots without a print. '
        'Usage: ACE_FA_TEST [ACE=0] [SCENARIO=cycle|pingpong|burst|matrix] '
        '[SLOTS=0,1,2,3] [DELAY=0.5] [REPEATS=2] [INTER=0] '
        '[RETRIES=0] [RETRY_DELAY=0.2]'
    )
    def cmd_ACE_FA_TEST(self, gcmd):
        ace_idx = gcmd.get_int('ACE', 0, minval=0)
        scenario = gcmd.get('SCENARIO', 'cycle').lower()
        slots_str = gcmd.get('SLOTS', '0,1,2,3')
        delay = gcmd.get_float('DELAY', 0.5, minval=0.05)
        repeats = gcmd.get_int('REPEATS', 2, minval=1, maxval=200)
        inter = gcmd.get_float('INTER', 0.0, minval=0.0)
        retries = gcmd.get_int('RETRIES', 0, minval=0, maxval=100)
        retry_delay = gcmd.get_float('RETRY_DELAY', 0.2, minval=0.05)

        try:
            slots = [int(s.strip()) for s in slots_str.split(',') if s.strip()]
        except ValueError:
            raise gcmd.error('[ACE_FA_TEST] invalid SLOTS=%r' % slots_str)
        for s in slots:
            if not (0 <= s <= 3):
                raise gcmd.error('[ACE_FA_TEST] slot %d out of range 0..3' % s)

        if ace_idx >= len(self._ace_devices) or not self._connected_per_ace.get(ace_idx, False):
            raise gcmd.error('[ACE_FA_TEST] ACE %d not connected' % ace_idx)

        steps = []
        if scenario == 'cycle':
            seq = list(slots) * repeats
            prev = None
            for s in seq:
                if prev is not None:
                    steps.append(('stop', prev))
                steps.append(('start', s))
                prev = s
            if prev is not None:
                steps.append(('stop', prev))
        elif scenario == 'pingpong':
            if len(slots) < 2:
                raise gcmd.error('[ACE_FA_TEST] pingpong needs at least 2 slots')
            seq = []
            for r in range(repeats):
                for s in slots:
                    seq.append(s)
            prev = None
            for s in seq:
                if prev is not None:
                    steps.append(('stop', prev))
                steps.append(('start', s))
                prev = s
            if prev is not None:
                steps.append(('stop', prev))
        elif scenario == 'burst':
            for s in slots:
                for _ in range(repeats):
                    steps.append(('start', s))
                    steps.append(('stop', s))
        elif scenario == 'matrix':
            for r in range(repeats):
                for f in slots:
                    for t in slots:
                        if t == f:
                            continue
                        steps.append(('start', f))
                        steps.append(('stop', f))
                        steps.append(('start', t))
                        steps.append(('stop', t))
        else:
            raise gcmd.error('[ACE_FA_TEST] unknown SCENARIO=%s (use cycle|pingpong|burst|matrix)' % scenario)

        results = {}
        retry_counts = {}

        def is_forbidden(response):
            if not response:
                return False
            msg = response.get('msg', '') or ''
            return msg.lower() == 'forbidden'

        def is_success(response):
            if not response:
                return False
            code = response.get('code', 0)
            msg = response.get('msg', '') or ''

            return code == 0 and (msg.lower() == 'success' or msg == '')

        def make_callback(step_idx, action, slot, attempt):
            def cb(self=None, response=None, **kw):
                code = response.get('code', 0) if response else None
                msg = response.get('msg', '') if response else ''
                results.setdefault(step_idx, []).append((attempt, action, slot, code, msg))
                logging.info(
                    '[ACE_FA_TEST] RESP step=%d attempt=%d %s slot=%d code=%s msg=%s'
                    % (step_idx, attempt, action, slot, code, msg))

                if action == 'start' and is_forbidden(response) and attempt < retries:
                    next_attempt = attempt + 1
                    retry_counts[step_idx] = next_attempt
                    def retry_send(eventtime):
                        try:
                            self.send_request_to(ace_idx,
                                {"method": "start_feed_assist", "params": {"index": slot}},
                                make_callback(step_idx, action, slot, next_attempt))
                            logging.info(
                                '[ACE_FA_TEST] RETRY step=%d attempt=%d %s slot=%d (after FORBIDDEN)'
                                % (step_idx, next_attempt, action, slot))
                        except Exception as e:
                            logging.info(
                                '[ACE_FA_TEST] RETRY step=%d attempt=%d %s slot=%d failed: %s'
                                % (step_idx, next_attempt, action, slot, e))
                        return self.reactor.NEVER
                    self.reactor.register_timer(
                        retry_send, self.reactor.monotonic() + retry_delay)
            return cb

        gcmd.respond_info(self._t('msg.fa_test_running',
            ace=self._disp(ace_idx), scenario=scenario, slots=slots,
            delay=delay, repeats=repeats, steps=len(steps), inter=inter,
            retries=retries, retry_delay=retry_delay))

        start_t = self.reactor.monotonic()
        for i, (action, slot) in enumerate(steps):
            t = start_t + (i + 1) * delay + i * inter

            def make_step(step_idx, action, slot):
                method = 'start_feed_assist' if action == 'start' else 'stop_feed_assist'
                def fire(eventtime):
                    try:
                        self.send_request_to(ace_idx,
                            {"method": method, "params": {"index": slot}},
                            make_callback(step_idx, action, slot, 0))
                        logging.info('[ACE_FA_TEST] SENT step=%d attempt=0 %s slot=%d' % (step_idx, action, slot))
                    except Exception as e:
                        logging.info('[ACE_FA_TEST] SEND step=%d %s slot=%d failed: %s' % (step_idx, action, slot, e))
                    return self.reactor.NEVER
                return fire

            self.reactor.register_timer(make_step(i, action, slot), t)

        retry_budget = retries * retry_delay if retries else 0.0
        summary_t = (start_t + (len(steps) + 1) * delay + len(steps) * inter
                     + retry_budget + 1.0)

        def summary(eventtime):
            sent = len(steps)
            recv_steps = len(results)
            no_ack_total = sent - recv_steps
            start_steps = [(i, a, s) for i, (a, s) in enumerate(steps) if a == 'start']
            attempts_hist = {}
            failed = []
            no_ack_starts = []
            for i, _, slot in start_steps:
                attempts = results.get(i, [])
                if not attempts:
                    no_ack_starts.append((i, slot))
                    continue
                final = attempts[-1]
                final_msg = (final[4] or '').lower()
                n_attempts = len(attempts)
                if final_msg == 'success':
                    attempts_hist[n_attempts] = attempts_hist.get(n_attempts, 0) + 1
                else:
                    failed.append((i, slot, n_attempts, final_msg or 'empty'))

            n_starts = len(start_steps)
            n_ok = sum(attempts_hist.values())
            max_att = max(attempts_hist.keys()) if attempts_hist else 0

            self.log_always(self._t('msg.fa_test_done',
                starts=n_starts, ok=n_ok, failed=len(failed),
                no_ack=len(no_ack_starts)))
            if attempts_hist:
                hist_str = '  '.join(
                    '%dx=%d' % (k, attempts_hist[k])
                    for k in sorted(attempts_hist.keys()))
                self.log_always(self._t('msg.fa_test_attempts',
                    hist=hist_str, max=max_att))
            if failed:
                kind = ('FORBIDDEN' if any(f[3] == 'forbidden' for f in failed)
                        else 'non-success')
                self.log_always(self._t('msg.fa_test_failed_header', kind=kind))
                for step_i, slot, n_att, msg in failed[:10]:
                    self.log_always(self._t('msg.fa_test_failed_line',
                        step=step_i, slot=self._disp(slot),
                        attempts=n_att, msg=msg))
                if len(failed) > 10:
                    self.log_always(self._t('msg.fa_test_more',
                        count=len(failed) - 10))
            if no_ack_starts:
                self.log_always(self._t('msg.fa_test_no_ack_header'))
                for step_i, slot in no_ack_starts[:10]:
                    self.log_always(self._t('msg.fa_test_no_ack_line',
                        step=step_i, slot=self._disp(slot)))
            return self.reactor.NEVER

        self.reactor.register_timer(summary, summary_t)

    def _audit_state(self, action, params=None):

        if not self._state_debug_enabled:
            return
        try:

            state = {
                'action': action,
                'params': params or {},
                'active_device': self._active_device_index,
                'device_count': len(self._ace_devices),
                'connected': self._connected,
                'serial': self.serial_id,
                'mode': getattr(self, '_ace_mode', 'unknown'),
                'swap_in_progress': self._swap_in_progress,
                'auto_feed': self._auto_feed_enabled,
                'fa_context': self._fa_context,
                'feed_assist': self._feed_assist_index,
                'gate_status': self.gate_status[:],
                'head_source': {},
            }
            for h in range(4):
                src = self._head_source.get(h)
                state['head_source'][h] = {
                    'ace': src['ace_index'], 'slot': src['slot'],
                    'type': src.get('type', ''), 'color': src.get('color', '')
                } if src else None

            sensors = {}
            for h in range(4):
                sensor = self.printer.lookup_object(
                    'filament_motion_sensor e%d_filament' % h, None)
                sensors[h] = sensor.get_status(0)['filament_detected'] if sensor else None
            state['sensors'] = sensors

            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc:
                ptc_status = ptc.get_status()
                ptc_info = {}
                for h in range(4):
                    ptc_info[h] = {
                        'type': ptc_status.get('filament_type', [''] * 4)[h],
                        'color': ptc_status.get('filament_color', [''] * 4)[h],
                        'vendor': ptc_status.get('filament_vendor', [''] * 4)[h],
                    }
                state['print_task_config'] = ptc_info

            self._state_log.info('STATE %s', json.dumps(state, default=str))

            warnings = []
            if action == 'LOAD_HEAD':
                head = params.get('head')
                if head is not None:
                    src = self._head_source.get(head)
                    if src is None:
                        warnings.append('head_source[%d] is None after LOAD' % head)
                    if sensors.get(head) is False:
                        warnings.append('sensor[%d] not detecting filament after LOAD' % head)
            elif action == 'UNLOAD_HEAD':
                head = params.get('head')
                if head is not None:
                    src = self._head_source.get(head)
                    if src is not None:
                        warnings.append('head_source[%d] still set after UNLOAD' % head)
            elif action == 'SWITCH':
                target = params.get('target')
                if target is not None and self._active_device_index != target:
                    warnings.append('active_device=%d but target was %d' % (self._active_device_index, target))
                if not self._connected:
                    warnings.append('not connected after SWITCH')
            elif action == 'CLEAR_HEADS':
                head = params.get('head', -1)
                if head >= 0:
                    if self._head_source.get(head) is not None:
                        warnings.append('head_source[%d] not cleared' % head)
                else:
                    for h in range(4):
                        if self._head_source.get(h) is not None:
                            warnings.append('head_source[%d] not cleared' % h)
            elif action == 'UNLOAD_ALL':
                for h in range(4):
                    if sensors.get(h) is True:
                        warnings.append('sensor[%d] still detecting after UNLOAD_ALL' % h)

            if warnings:
                warn_msg = '[multiACE] STATE WARNINGS after %s: %s' % (action, '; '.join(warnings))
                self._state_log.warning(warn_msg)
                logging.warning(warn_msg)
        except Exception as e:
            self._state_log.error('STATE audit error: %s', str(e))

    def _telemetry(self, event, data):
        try:
            self._telemetry_log.info('%s %s', event, json.dumps(data, default=str))
        except Exception as e:
            logging.info('[multiACE] telemetry %s failed: %s' % (event, e))

    def get_status(self, eventtime=None):

        aces = []
        for i in range(len(self._ace_devices)):
            info = self._info_per_ace.get(i, {}) or {}
            slots_out = []
            for n, s in enumerate(info.get('slots', []) or []):
                if not isinstance(s, dict):
                    continue
                slots_out.append({
                    'index':    s.get('index', n),
                    'status':   s.get('status', ''),
                    'sku':      s.get('sku', ''),
                    'material': s.get('type', ''),
                    'subtype':  s.get('subtype', ''),
                    'rfid':     s.get('rfid', 0),
                    'brand':    s.get('brand', ''),
                    'color':    s.get('color', [0, 0, 0]),
                })
            protocol = self._protocols.get(i)
            aces.append({
                'idx':          i,
                'connected':    self._connected_per_ace.get(i, False),
                'protocol':     getattr(protocol, 'NAME', '') if protocol else '',
                'status':       info.get('status', 'unknown'),
                'temp':         info.get('temp', 0),

                'humidity':     info.get('humidity'),
                'dryer_status': info.get('dryer_status', {}),
                'gate_status':  self._gate_status_per_ace.get(i, []),
                'feed_assist':  self._feed_assist_per_ace.get(i, -1),
                'slots':        slots_out,
            })
        ace_heads_now = [h for h in range(4) if self.head_uses_ace(h)]
        return {
            'api_version': ACE_API_VERSION,
            'status': self._info['status'],
            'temp': self._info['temp'],
            'dryer_status': self._info['dryer_status'],
            'gate_status': self.gate_status,
            'active_device': self._active_device_index,
            'device_count': len(self._ace_devices),
            'ace_head': (ace_heads_now[0] if len(ace_heads_now) == 1
                         else getattr(self, '_ace_head', 3)),
            'ace_heads': ace_heads_now,
            'mode': getattr(self, '_ace_mode', 'normal'),
            'pickup_cleaning': getattr(self, '_pickup_cleaning', False),
            'swap_phase': self._swap_phase,
            'last_swap_result': self._last_swap_result,
            'event_seq': self._event_seq,
            'head_source': {str(k): v for k, v in self._head_source.items()},
            'head_manual': {str(h): bool(self.head_manual.get(h, False))
                            for h in range(4)},
            'head_feeder': {str(h): bool(self.head_feeder.get(h, False))
                            for h in range(4)},
            'head_ace': {str(h): int(self.head_ace.get(h, h))
                         for h in range(4)},
            'swap_in_progress': self._swap_in_progress,
            'aces': aces,
        }

def load_config(config):
    return MultiAce(config)
