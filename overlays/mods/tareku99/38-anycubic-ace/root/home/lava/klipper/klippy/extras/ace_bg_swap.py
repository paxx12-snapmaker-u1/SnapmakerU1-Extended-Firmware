
import logging
import chelper
from . import force_move

BG_SWAP_VERSION = 'v0.9'

BG_LOAD_GRIP_SEAT = 60.
BG_LOAD_GRIP_SPEED = 5.
BG_LOAD_PRESS_V2 = 50.
BG_LOAD_PRESS_V1 = 30.
BG_LOAD_PRESS_SPEED = 20.
BG_LOAD_PRESS_RETRIES = 3
BG_LOAD_PRESS_RETRY_DELAY = 2.0
BG_LOAD_PRIME_SPEED = 4.
BG_LOAD_PRIME_EXTRA = 40.
BG_LOAD_RETRACT_SPEED = 25.
BG_FEED_MIN_MOVE = 100
BG_FEED_FA_RESCUE = 20.
COLD_PULL_NORMAL = [
    (57.0, 400.),
    (3.0, 1500.),
    (-27.0, 2700.),
    (-5.5, 40.),
    (-37.5, 1500.),
]
COLD_PULL_SOFT = [
    (5.0, 600.),
    (-27.0, 2700.),
    (-5.5, 40.),
    (-37.5, 1500.),
]
CHOREO_ACCEL = 300.
HEAT_TIMEOUT = 240.
HEAT_HYST = 4.0
ACE_UNWIND_SPEED_FALLBACK = 80
MOVE_SETTLE = 0.30
BG_UNLOAD_DECODER_DIAG = True
BG_UNLOAD_PROBE_RETRACT = 150.
BG_UNLOAD_STALL_FRAC = 0.3

SCHEDULE_DELAY = 0.250
SCHEDULE_EPS = 0.050
MAX_DISTANCE = 200.
MAX_VELOCITY = 60.
MAX_ACCEL = 2000.

class AceBgSwap:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        heads_raw = config.get('heads', '')
        self.pick_gate = config.getboolean('pick_gate', True)
        self.load_enabled = config.getboolean('load_enabled', True)
        self.enabled_heads = set()
        for tok in heads_raw.replace(',', ' ').split():
            try:
                h = int(tok)
                if 0 <= h <= 3:
                    self.enabled_heads.add(h)
            except ValueError:
                pass
        save_vars = self.printer.lookup_object('save_variables', None)
        if save_vars is not None:
            saved = save_vars.allVariables.get('ace__bg_heads', None)
            if isinstance(saved, (list, tuple)):
                restored = set()
                for h in saved:
                    try:
                        h = int(h)
                        if 0 <= h <= 3:
                            restored.add(h)
                    except (TypeError, ValueError):
                        pass
                self.enabled_heads = restored
                logging.info('[multiACE] [bg-unload] enabled heads restored '
                             'from ace__bg_heads: %s'
                             % (sorted(restored) or 'NONE'))
        self.version = BG_SWAP_VERSION
        self.state = {}
        self._busy = set()
        ffi_main, ffi_lib = chelper.get_ffi()
        self.trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
        self.trapq_append = ffi_lib.trapq_append
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves
        self.stepper_kinematics = ffi_main.gc(
            ffi_lib.cartesian_stepper_alloc(b'x'), ffi_lib.free)
        self._last_end = {}
        self.gcode.register_command('ACE_BG_UNLOAD', self.cmd_ACE_BG_UNLOAD,
                                    desc=self.cmd_ACE_BG_UNLOAD_help)
        self.gcode.register_command('ACE_BG_STATUS', self.cmd_ACE_BG_STATUS,
                                    desc=self.cmd_ACE_BG_STATUS_help)
        self.gcode.register_command('ACE_BG_MOVE', self.cmd_ACE_BG_MOVE,
                                    desc=self.cmd_ACE_BG_MOVE_help)
        self.gcode.register_command('ACE_BG_SET_HEAD', self.cmd_ACE_BG_SET_HEAD,
                                    desc=self.cmd_ACE_BG_SET_HEAD_help)
        self.gcode.register_command('ACE_BG_SWAP', self.cmd_ACE_BG_SWAP,
                                    desc=self.cmd_ACE_BG_SWAP_help)

    def get_status(self, eventtime):
        return {
            'version': self.version,
            'enabled_heads': sorted(self.enabled_heads),
            'busy': sorted(self._busy),
            'state': {str(h): str(v) for h, v in self.state.items()},
        }

    cmd_ACE_BG_SET_HEAD_help = (
        '[multiACE] Declare a head bg-swap capable (= its dock is OPEN below,'
        ' the cold-pull purges through it). ACE_BG_SET_HEAD HEAD=n ENABLE=0|1'
        ' - persisted (ace__bg_heads), overrides the [ace_bg_swap] heads'
        ' config default.')
    def cmd_ACE_BG_SET_HEAD(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        enable = bool(gcmd.get_int('ENABLE', minval=0, maxval=1))
        if head in self._busy:
            raise gcmd.error('[bg-unload] head %d has a RUNNING bg operation'
                             ' - toggle after it finishes' % self._dh(head))
        if enable:
            self.enabled_heads.add(head)
        else:
            self.enabled_heads.discard(head)
        self.gcode.run_script_from_command(
            "SAVE_VARIABLE VARIABLE=ace__bg_heads VALUE=%s"
            % str(sorted(self.enabled_heads)).replace(' ', ''))
        self._say('head %d bg-swap %s (enabled heads now %s)'
                  % (self._dh(head), 'ENABLED - dock must be OPEN below' if enable
                     else 'disabled',
                     [self._dh(h) for h in sorted(self.enabled_heads)]
                     or 'NONE'))

    def is_busy(self, head):
        return head in self._busy

    def _say(self, msg):
        logging.info('[multiACE] [bg-unload] %s' % msg)
        try:
            self.gcode.respond_raw('// [bg-unload] %s' % msg)
        except Exception:
            pass

    def _ext_name(self, head):
        return 'extruder' if head == 0 else 'extruder%d' % head

    def _dh(self, idx):
        ace = self.printer.lookup_object('ace', None)
        try:
            if ace is not None:
                return ace._disp(idx)
        except Exception:
            pass
        return idx

    def _pause(self, seconds):
        self.reactor.pause(self.reactor.monotonic() + seconds)

    def _wait_move(self, toolhead, end):
        while toolhead.mcu.estimated_print_time(
                self.reactor.monotonic()) < end + MOVE_SETTLE:
            self._pause(0.10)

    def _ace_send(self, ace, ace_idx, request):
        """Send one request and return its RESPONSE dict (or None on
        timeout). The first HW run showed why the code must be checked:
        an unwind sent while the previous rollback still ran was ACCEPTED
        on the wire but not executed - 'done' alone is no truth."""
        done = [None]
        def _cb(self, response):
            try:
                done[0] = response if response is not None else {}
            except Exception:
                pass
        ace.send_request_to(ace_idx, request, _cb)
        deadline = self.reactor.monotonic() + 5.0
        while done[0] is None and self.reactor.monotonic() < deadline:
            self._pause(0.05)
        return done[0]

    def _resp_rejected(self, resp):
        """True when a motor command was NOT accepted. The ACE Pro (V1)
        rejects a feed/unwind while the previous one still runs with
        code=0 msg=FORBIDDEN (HW 2026-07-11: the bg bulk unwind 1179 was
        FORBIDDEN 2.4s after the short-150 and the engine believed the
        code=0 -> 'unload half done' with the filament never pulled).
        Treat FORBIDDEN as a busy rejection - retry, never success."""
        if not resp:
            return True
        if resp.get('code', -1) != 0:
            return True
        return str(resp.get('msg', '')).strip().upper() == 'FORBIDDEN'

    def _fa_on(self, ace, ace_idx, slot, retries=3, backoff=1.0):
        """Arm feed-assist with a busy-rejection backoff. Right after a
        stop_feed_filament the V1 is still in its feed wind-down and
        rejects start_feed_assist with code=0 msg=FORBIDDEN (HW
        2026-07-11: 2 attempts 50ms apart, both FORBIDDEN, FA never
        armed although the log said 'FA ON'). 50ms is useless against a
        motor wind-down - retry on a ~1s backoff instead. Updates the
        host FA cache ONLY on a real accept (FORBIDDEN carries code=0,
        a bare code check would stamp a stale cache). Returns True when
        armed."""
        for attempt in range(retries):
            resp = self._ace_send(ace, ace_idx, {
                'method': 'start_feed_assist', 'params': {'index': slot}})
            if not self._resp_rejected(resp):
                ace._feed_assist_per_ace[ace_idx] = slot
                return True
            if attempt < retries - 1:
                self._pause(backoff)
        return False

    def _check_docked(self, toolhead, ext, head):
        if toolhead.get_extruder() is ext:
            raise RuntimeError('head %d was PICKED mid-sequence - aborted, '
                               'inline paths take over' % self._dh(head))

    def _slot_status(self, ace, ace_idx, slot):
        try:
            st = ace._v2_get_slot_status(ace_idx, slot)
            if st:
                return str(st)
        except Exception:
            pass
        info = ace._info_per_ace.get(ace_idx) or {}
        slots = info.get('slots') or []
        if slot < len(slots) and isinstance(slots[slot], dict):
            return str(slots[slot].get('status'))
        return ''

    def _wait_rollback_done(self, ace, ace_idx, slot, length, speed):
        """Device-truth pacing (HW 2026-07-06: a fixed timer declared the
        1879mm bulk retract done while the device had dropped it): wait for
        the slot to ENTER a moving state (heartbeat latency ~1-2s), then for
        it to LEAVE it. Returns the last seen status.

        Deliberately NO pick-interlock in here (v0.7, Dirk): a pick during
        the bulk retract must NOT cancel the rollback - the V2 rollback is
        fixed-length (S33, no slot sensor), a cancel strands a partially
        retracted filament and the inline re-retract then over-pulls it out
        of the ACE gears. The pick itself is only carriage motion (filament
        already out of the toolhead here) and the arrival ACE_SWAP_HEAD
        WAITS on is_busy via ace._wait_bg_op. Do not re-add an abort."""
        moving = ('rollback', 'feeding')
        deadline = self.reactor.monotonic() + 4.0
        seen_moving = False
        while self.reactor.monotonic() < deadline:
            st = self._slot_status(ace, ace_idx, slot)
            if any(m in st for m in moving):
                seen_moving = True
                break
            self._pause(0.2)
        deadline = self.reactor.monotonic() + length / max(speed, 1) + 20.0
        while self.reactor.monotonic() < deadline:
            st = self._slot_status(ace, ace_idx, slot)
            if not any(m in st for m in moving):
                if seen_moving:
                    return st
                return 'DROPPED:%s' % st
            seen_moving = True
            self._pause(0.3)
        return 'TIMEOUT'

    def _ace_unwind(self, ace, ace_idx, slot, length, speed, wait=True,
                    retries=3):
        """Unwind with the V2 rollback-lock release, RESP-code check,
        busy-retry and optional device-truth completion wait. ace._retract
        sends stop_feed_assist UNCONDITIONALLY before every unwind ('release
        rollback-lock') - v0 skipped it when the FA cache was empty and the
        device dropped ALL unwinds (HW 2026-07-06: lamp never blinked)."""
        if not ace._is_v2_idx(ace_idx):
            for attempt in range(1, retries + 1):
                resp = self._ace_send(ace, ace_idx, {
                    'method': 'unwind_filament',
                    'params': {'index': slot, 'length': int(length),
                               'speed': int(speed)}})
                if self._resp_rejected(resp):
                    self._say('unwind %dmm attempt %d: code=%s msg=%s - retry'
                              % (length, attempt,
                                 (resp or {}).get('code', 'none'),
                                 (resp or {}).get('msg', 'timeout')))
                    self._pause(2.0)
                    continue
                if wait:
                    self._pause(length / max(speed, 1) + 2.0)
                return True
            return False
        for attempt in range(1, retries + 1):
            self._ace_send(ace, ace_idx, {
                'method': 'stop_feed_assist', 'params': {'index': slot}})
            resp = self._ace_send(ace, ace_idx, {
                'method': 'unwind_filament',
                'params': {'index': slot, 'length': int(length),
                           'speed': int(speed)}})
            if self._resp_rejected(resp):
                self._say('unwind %dmm attempt %d: code=%s msg=%s - retry'
                          % (length, attempt,
                             (resp or {}).get('code', 'none'),
                             (resp or {}).get('msg', 'timeout')))
                self._pause(2.0)
                continue
            if not wait:
                return True
            st = self._wait_rollback_done(ace, ace_idx, slot, length, speed)
            if st.startswith('DROPPED') or st == 'TIMEOUT':
                self._say('unwind %dmm attempt %d: device %s - retry'
                          % (length, attempt, st))
                self._pause(2.0)
                continue
            return True
        return False

    def _ace_feed(self, ace, ace_idx, slot, length, speed, retries=3):
        """Feed with the same discipline as _ace_unwind: V2 pre-stop
        (rollback-lock release), RESP-code check and device-truth
        completion wait ('feeding' status cycle via _wait_rollback_done);
        V1 = open-loop time pacing. Returns 'ok' | 'none' (nothing
        demonstrably moved - safe to hand over an EMPTY head) | 'partial'
        (moved but completion unconfirmed - the caller MUST retract the
        commanded length before handover, else the inline load would
        double-feed into the gears). Retries only on 'none' conditions."""
        if length <= 0:
            return 'ok'
        if not ace._is_v2_idx(ace_idx):
            for attempt in range(1, retries + 1):
                resp = self._ace_send(ace, ace_idx, {
                    'method': 'feed_filament',
                    'params': {'index': slot, 'length': int(length),
                               'speed': int(speed)}})
                if self._resp_rejected(resp):
                    self._say('feed %dmm attempt %d: code=%s msg=%s - retry'
                              % (length, attempt,
                                 (resp or {}).get('code', 'none'),
                                 (resp or {}).get('msg', 'timeout')))
                    self._pause(2.0)
                    continue
                self._pause(length / max(speed, 1) + 2.0)
                return 'ok'
            return 'none'
        for attempt in range(1, retries + 1):
            self._ace_send(ace, ace_idx, {
                'method': 'stop_feed_assist', 'params': {'index': slot}})
            resp = self._ace_send(ace, ace_idx, {
                'method': 'feed_filament',
                'params': {'index': slot, 'length': int(length),
                           'speed': int(speed)}})
            if self._resp_rejected(resp):
                self._say('feed %dmm attempt %d: code=%s msg=%s - retry'
                          % (length, attempt,
                             (resp or {}).get('code', 'none'),
                             (resp or {}).get('msg', 'timeout')))
                self._pause(2.0)
                continue
            st = self._wait_rollback_done(ace, ace_idx, slot, length, speed)
            if st.startswith('DROPPED'):
                self._say('feed %dmm attempt %d: device %s - retry'
                          % (length, attempt, st))
                self._pause(2.0)
                continue
            if st == 'TIMEOUT':
                self._say('feed %dmm: device TIMEOUT after moving - NOT '
                          'retrying (fed amount unknown)' % length)
                return 'partial'
            return 'ok'
        return 'none'

    def _ace_feed_to_gears(self, ace, ace_idx, slot, length, speed, head):
        """Feed toward the extruder and STOP at the TOOLHEAD SENSOR - the
        same stop marker the inline load uses, hardware-agnostic (V1+V2).
        HW 2026-07-10 proved the sensor fires on a PARKED head (insert event
        while parked+printing: encoder edges need no extruder motion) AND
        that the decoder-flat criterion is unreliable as a stop (false-fired
        mid-bowden at 1512 of ~1970 on a snag; FA pushed the tip the rest of
        the way 8s later). The V2 decoder is sampled as TELEMETRY only
        ('bg-feed' span log). Secondary stop: the slot leaves its moving
        state (V2 self-stops at real resistance, no grinding).
        Returns (result, span_tuple): 'ok' = sensor reached (tip at the
        toolhead sensor, just above the gears); 'none' = nothing demonstrably
        moved (safe EMPTY handover); 'partial' = moved but the sensor never
        fired (mid-bowden stop / sensor miss) - the caller must cleanup-
        retract before handover. 'stale' = the sensor already read present
        BEFORE the feed (cannot serve as a marker; nothing was sent)."""
        sensor = self.printer.lookup_object(
            'filament_motion_sensor e%d_filament' % head, None)
        def _detected():
            try:
                return bool(sensor is not None and
                            sensor.get_status(0).get('filament_detected'))
            except Exception:
                return False
        if sensor is None:
            return 'stale', (None, 0, None, None)
        if _detected():
            return 'stale', (None, 0, None, None)
        if length <= 0:
            return 'ok', (None, 0, None, None)
        is_v2 = ace._is_v2_idx(ace_idx)
        self._ace_send(ace, ace_idx, {
            'method': 'stop_feed_assist', 'params': {'index': slot}})
        resp = None
        for _try in range(3):
            resp = self._ace_send(ace, ace_idx, {
                'method': 'feed_filament',
                'params': {'index': slot, 'length': int(length),
                           'speed': int(speed)}})
            if not self._resp_rejected(resp):
                break
            self._say('feed %dmm start: code=%s msg=%s - retry'
                      % (length, (resp or {}).get('code', 'none'),
                         (resp or {}).get('msg', 'timeout')))
            self._pause(2.0)
        if self._resp_rejected(resp):
            return 'none', (None, 0, None, None)
        dmin = dmax = None
        n = 0
        arrived = False
        device_idle_since = None
        deadline = self.reactor.monotonic() + length / max(speed, 1) + 15.
        while self.reactor.monotonic() < deadline:
            if _detected():
                arrived = True
                break
            if is_v2:
                d = ace._read_decoder(ace_idx, slot)
                if d is not None:
                    n += 1
                    dmin = d if dmin is None else min(dmin, d)
                    dmax = d if dmax is None else max(dmax, d)
                st = self._slot_status(ace, ace_idx, slot) or ''
                if 'feeding' not in st and 'rollback' not in st:
                    if device_idle_since is None:
                        device_idle_since = self.reactor.monotonic()
                    elif (self.reactor.monotonic() - device_idle_since
                          >= 2.0):
                        break
                else:
                    device_idle_since = None
            self._pause(0.15)
        self._ace_send(ace, ace_idx, {
            'method': 'stop_feed_filament', 'params': {'index': slot}})
        span = (dmax - dmin) if (dmax is not None
                                 and dmin is not None) else None
        if arrived:
            return 'ok', (span, n, dmin, dmax)
        if is_v2 and (span is None or span < BG_FEED_MIN_MOVE):
            return 'none', (span, n, dmin, dmax)
        resp = self._ace_send(ace, ace_idx, {
            'method': 'start_feed_assist', 'params': {'index': slot}})
        if not self._resp_rejected(resp):
            ace._feed_assist_per_ace[ace_idx] = slot
            rescue_deadline = self.reactor.monotonic() + BG_FEED_FA_RESCUE
            while self.reactor.monotonic() < rescue_deadline:
                if _detected():
                    arrived = True
                    break
                self._pause(0.3)
        if arrived:
            return 'ok', (span, n, dmin, dmax)
        self._ace_send(ace, ace_idx, {
            'method': 'stop_feed_assist', 'params': {'index': slot}})
        ace._feed_assist_per_ace[ace_idx] = -1
        return 'partial', (span, n, dmin, dmax)

    def _gpio_diag(self, head, where):
        """[diag, log-only per S12] Sample the raw per-edge pin state
        (runout_buttun_state on the stock EncoderSensor) plus the helper
        presence state at interesting moments. Collects the HW-validation
        data for whether the raw pin can serve as the bg absence signal
        (S6 burned a control use once - never gate on this, only log)."""
        try:
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            raw = getattr(sensor, 'runout_buttun_state', None)
            det = None
            try:
                det = sensor.get_status(0).get('filament_detected')
            except Exception:
                pass
            logging.info('[multiACE] [bg-gpio] head %d %s: '
                         'runout_buttun_state=%s filament_detected=%s'
                         % (head, where, raw, det))
        except Exception:
            pass

    def _schedule_start(self, toolhead, name):
        est = toolhead.mcu.estimated_print_time(self.reactor.monotonic())
        return max(toolhead.print_time,
                   getattr(toolhead, 'step_gen_time', 0.),
                   est + SCHEDULE_DELAY,
                   self._last_end.get(name, 0.)) + SCHEDULE_EPS

    def _ensure_enabled(self, stepper_name, print_time):
        stepper_enable = self.printer.lookup_object('stepper_enable')
        enable = stepper_enable.lookup_enable(stepper_name)
        if not enable.is_motor_enabled():
            enable.motor_enable(max(print_time - 0.100, 0.))
            return True
        return False

    def queue_move(self, ext, dist, speed, accel):
        """Schedule one stall-free move on `ext`'s (parked) stepper. Returns
        (start, end, enabled_now) in print_time. Python API for the bg-swap
        sequencer; the caller is responsible for the guards (not the active
        extruder, hot enough, head stays docked)."""
        toolhead = self.printer.lookup_object('toolhead')
        stepper = ext.extruder_stepper.stepper
        name = ext.get_name() if hasattr(ext, 'get_name') else ext.name
        start = self._schedule_start(toolhead, name)
        enabled_now = self._ensure_enabled(stepper.get_name(), start)

        prev_pos = stepper.get_commanded_position()
        prev_sk = stepper.set_stepper_kinematics(self.stepper_kinematics)
        prev_trapq = stepper.set_trapq(self.trapq)
        stepper.set_position((0., 0., 0.))
        axis_r, accel_t, cruise_t, cruise_v = force_move.calc_move_time(
            dist, speed, accel)
        self.trapq_append(self.trapq, start, accel_t, cruise_t, accel_t,
                          0., 0., 0., axis_r, 0., 0.,
                          0., cruise_v, accel, 0xFFFFFFFF)
        end = start + accel_t + cruise_t + accel_t
        stepper.generate_steps(end)
        self.trapq_finalize_moves(self.trapq, end + 99999.9, end + 99999.9)
        stepper.set_trapq(prev_trapq)
        stepper.set_stepper_kinematics(prev_sk)
        stepper.set_position((prev_pos, 0., 0.))
        toolhead.note_mcu_movequeue_activity(end)
        self._last_end[name] = end
        return start, end, enabled_now

    cmd_ACE_BG_MOVE_help = (
        '[EXPERIMENTAL] Move a PARKED head\'s extruder without stalling the '
        'print. ACE_BG_MOVE HEAD=0-3 DISTANCE=<+-mm> [VELOCITY=5] [ACCEL=100] '
        '[FORCE=0]. Refuses the active extruder; FORCE=1 skips the '
        'cold-extrude check. No toolchange interlock yet - keep the head '
        'docked while it runs.')

    def cmd_ACE_BG_MOVE(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        dist = gcmd.get_float('DISTANCE',
                              minval=-MAX_DISTANCE, maxval=MAX_DISTANCE)
        speed = gcmd.get_float('VELOCITY', 5., above=0., maxval=MAX_VELOCITY)
        accel = gcmd.get_float('ACCEL', 100., above=0., maxval=MAX_ACCEL)
        force = gcmd.get_int('FORCE', 0)

        name = self._ext_name(head)
        ext = self.printer.lookup_object(name, None)
        if ext is None:
            raise gcmd.error('[bg-move] %s not configured' % name)
        toolhead = self.printer.lookup_object('toolhead')
        active = toolhead.get_extruder()
        if active is ext:
            raise gcmd.error(
                '[bg-move] head %d is the ACTIVE toolhead extruder - '
                'bg moves are for parked heads only' % self._dh(head))
        if not dist:
            gcmd.respond_info('[bg-move] DISTANCE=0 - nothing to do')
            return
        heater = ext.get_heater()
        if not force and not bool(getattr(heater, 'can_extrude', True)):
            raise gcmd.error(
                '[bg-move] %s is below min_extrude_temp - heat it first '
                '(M104 S<temp> T%d A0) or pass FORCE=1' % (name, head))

        start, end, enabled_now = self.queue_move(ext, dist, speed, accel)
        est = toolhead.mcu.estimated_print_time(self.reactor.monotonic())
        msg = ('[bg-move] %s %+0.2fmm @%.1fmm/s scheduled t+%.2fs, '
               'runs %.2fs%s' % (name, dist, speed, start - est, end - start,
                                 ' (motor enabled)' if enabled_now else ''))
        gcmd.respond_info(msg)
        logging.info('[multiACE] %s (start=%.3f end=%.3f print_time=%.3f)'
                     % (msg, start, end, toolhead.print_time))

    cmd_ACE_BG_UNLOAD_help = (
        '[EXPERIMENTAL] Unload a PARKED ACE head in the background (heat + '
        'cold-pull via bg moves + ACE retract) while printing. '
        'ACE_BG_UNLOAD HEAD=0-3 [TEMP=<feed temp>]. Requires head mode, 1:1 '
        'wiring, an OPEN dock below the head (purges ~60mm!), and the head '
        'must stay docked for the whole ~3min sequence.')

    def cmd_ACE_BG_UNLOAD(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        temp = gcmd.get_float('TEMP', 0.)
        quiet = gcmd.get_int('QUIET', 0)
        force = gcmd.get_int('FORCE', 0)

        def _refuse(msg):
            if quiet:
                self._say('skip (quiet): %s' % msg)
                return
            raise gcmd.error('[bg-unload] %s' % msg)

        if head not in self.enabled_heads and not force:
            return _refuse('head %d not bg-enabled ([ace_bg_swap] heads: - '
                           'the open-dock declaration); FORCE=1 to override'
                           % self._dh(head))

        ace = self.printer.lookup_object('ace', None)
        if ace is None:
            return _refuse('needs the [ace] section')
        if head in self._busy:
            return _refuse('head %d already running (%s)'
                           % (self._dh(head), self.state.get(head)))
        if self._busy:
            return _refuse('another bg op is running (head %s) - bg ops are '
                           'serialized (shared move queue), one at a time'
                           % ', '.join(str(self._dh(h))
                                       for h in sorted(self._busy)))
        if getattr(ace, '_ace_mode', 'multi') != 'head':
            return _refuse('v0 requires head mode (1:1 ACE per head)')
        if not ace.head_uses_ace(head):
            return _refuse('head %d is not ACE-driven' % self._dh(head))
        if getattr(ace, '_swap_in_progress', False):
            return _refuse('a swap is in progress')
        ext = self.printer.lookup_object(self._ext_name(head), None)
        toolhead = self.printer.lookup_object('toolhead')
        if ext is None:
            return _refuse('%s not configured' % self._ext_name(head))
        if toolhead.get_extruder() is ext:
            return _refuse('head %d is the ACTIVE toolhead - bg unload is for '
                           'parked heads' % self._dh(head))
        source = ace._head_source.get(head)
        if not source:
            return _refuse('head %d has no head_source - nothing to unload'
                           % self._dh(head))
        ace_idx = source.get('ace_index')
        slot = source.get('slot')
        if ace_idx is None or slot is None:
            return _refuse('head %d head_source incomplete' % self._dh(head))
        try:
            act_name = toolhead.get_extruder().get_name()
            act_head = (0 if act_name == 'extruder'
                        else int(act_name.replace('extruder', '')))
            act_src = ace._head_source.get(act_head)
            if act_src and act_src.get('ace_index') == ace_idx:
                return _refuse(
                    'ACE %d also feeds the printing head %d (1:1 wiring '
                    'violated?)' % (self._dh(ace_idx), self._dh(act_head)))
        except Exception:
            pass
        if ace._serial_failed_per_ace.get(ace_idx, False) or \
                ace._reconnecting_per_ace.get(ace_idx, False):
            return _refuse('ACE %d comms not healthy' % self._dh(ace_idx))

        if temp <= 0.:
            temp = 250.
            try:
                module, channel = ace.EXTRUDER_MAP[head]
                feed = self.printer.lookup_object(
                    'filament_feed %s' % module, None)
                if feed is not None:
                    _g = getattr(feed, '_get_filament_unload_temp',
                                 feed._get_filament_temp)
                    temp = float(_g(channel))
            except Exception:
                pass
        soft = False
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None:
                soft = bool(ptc.get_status()['filament_soft'][head])
        except Exception:
            pass

        self._busy.add(head)
        self.state[head] = 'QUEUED'
        self._say('head %d: bg unload queued [%s] (ACE %d slot %d, '
                  'temp %.0f, soft=%s) - head must stay docked, dock must '
                  'be OPEN below'
                  % (self._dh(head), BG_SWAP_VERSION, self._dh(ace_idx),
                     self._dh(slot), temp, soft))
        self.reactor.register_async_callback(
            lambda et, h=head, a=ace_idx, s=slot, t=temp, sf=soft:
                self._run_unload(h, a, s, t, sf))

    cmd_ACE_BG_SWAP_help = (
        '[EXPERIMENTAL] Background SWAP of a PARKED ACE head: unload (if '
        'loaded), then feed+grip+prime the target slot through the OPEN '
        'dock - the arrival toolchange becomes a no-op. ACE_BG_SWAP '
        'HEAD=0-3 SLOT=0-3 [ACE=n] [TEMP=] [ANTI_OOZE=] [QUIET=1] '
        '[FORCE=1]. Same requirements as ACE_BG_UNLOAD.')

    def cmd_ACE_BG_SWAP(self, gcmd):
        head = gcmd.get_int('HEAD', minval=0, maxval=3)
        slot_ld = gcmd.get_int('SLOT', minval=0, maxval=3)
        ace_ld = gcmd.get_int('ACE', -1, minval=-1, maxval=3)
        temp = gcmd.get_float('TEMP', 0.)
        quiet = gcmd.get_int('QUIET', 0)
        force = gcmd.get_int('FORCE', 0)
        anti_ooze = gcmd.get_float('ANTI_OOZE', -1.)

        def _refuse(msg):
            if quiet:
                self._say('skip (quiet): %s' % msg)
                return
            raise gcmd.error('[bg-swap] %s' % msg)

        if head not in self.enabled_heads and not force:
            return _refuse('head %d not bg-enabled ([ace_bg_swap] heads: - '
                           'the open-dock declaration); FORCE=1 to override'
                           % self._dh(head))
        ace = self.printer.lookup_object('ace', None)
        if ace is None:
            return _refuse('needs the [ace] section')
        if head in self._busy:
            return _refuse('head %d already running (%s)'
                           % (self._dh(head), self.state.get(head)))
        if self._busy:
            return _refuse('another bg op is running (head %s) - bg ops are '
                           'serialized (shared move queue), one at a time'
                           % ', '.join(str(self._dh(h))
                                       for h in sorted(self._busy)))
        if getattr(ace, '_ace_mode', 'multi') != 'head':
            return _refuse('requires head mode (1:1 ACE per head)')
        if not ace.head_uses_ace(head):
            return _refuse('head %d is not ACE-driven' % self._dh(head))
        if getattr(ace, '_swap_in_progress', False):
            return _refuse('a swap is in progress')
        ext = self.printer.lookup_object(self._ext_name(head), None)
        toolhead = self.printer.lookup_object('toolhead')
        if ext is None:
            return _refuse('%s not configured' % self._ext_name(head))
        if toolhead.get_extruder() is ext:
            return _refuse('head %d is the ACTIVE toolhead - bg swaps are '
                           'for parked heads' % self._dh(head))

        un = None
        source = ace._head_source.get(head)
        if source and source.get('ace_index') is not None \
                and source.get('slot') is not None:
            un = (source['ace_index'], source['slot'])
        if ace_ld < 0:
            if un is not None:
                ace_ld = un[0]
            else:
                try:
                    ace_ld = int(ace.head_ace_for(head))
                except Exception:
                    ace_ld = head
        if un is not None and un == (ace_ld, slot_ld):
            return _refuse('head %d already loaded from ACE %d slot %d - '
                           'nothing to do'
                           % (self._dh(head), self._dh(ace_ld),
                              self._dh(slot_ld)))
        try:
            act_name = toolhead.get_extruder().get_name()
            act_head = (0 if act_name == 'extruder'
                        else int(act_name.replace('extruder', '')))
            act_src = ace._head_source.get(act_head)
            if act_src and act_src.get('ace_index') == ace_ld:
                return _refuse(
                    'ACE %d also feeds the printing head %d (1:1 wiring '
                    'violated?)' % (self._dh(ace_ld), self._dh(act_head)))
        except Exception:
            pass
        if ace._serial_failed_per_ace.get(ace_ld, False) or \
                ace._reconnecting_per_ace.get(ace_ld, False):
            return _refuse('ACE %d comms not healthy' % self._dh(ace_ld))

        u_temp = temp
        if temp <= 0.:
            temp = 250.
            u_temp = 0.
            try:
                module, channel = ace.EXTRUDER_MAP[head]
                feed = self.printer.lookup_object(
                    'filament_feed %s' % module, None)
                if feed is not None:
                    temp = float(feed._get_filament_temp(channel))
                    _g = getattr(feed, '_get_filament_unload_temp', None)
                    if _g is not None:
                        u_temp = float(_g(channel))
            except Exception:
                pass
            if u_temp <= 0.:
                u_temp = temp
        soft = False
        try:
            ptc = self.printer.lookup_object('print_task_config', None)
            if ptc is not None:
                soft = bool(ptc.get_status()['filament_soft'][head])
        except Exception:
            pass
        if anti_ooze < 0.:
            anti_ooze = float(getattr(ace, 'swap_anti_ooze_retract', 10))

        self._busy.add(head)
        self.state[head] = 'QUEUED'
        self._say('head %d: bg SWAP queued [%s] (%s -> ACE %d slot %d, '
                  'temp %.0f, anti_ooze %.1f) - head must stay docked, '
                  'dock must be OPEN below'
                  % (self._dh(head), BG_SWAP_VERSION,
                     ('unload ACE %d slot %d' % (self._dh(un[0]),
                                                 self._dh(un[1])))
                     if un else 'head empty',
                     self._dh(ace_ld), self._dh(slot_ld), temp, anti_ooze))
        self.reactor.register_async_callback(
            lambda et, h=head, u=un, l=(ace_ld, slot_ld), t=temp, sf=soft,
                   ao=anti_ooze, ut=u_temp:
                self._run_swap(h, u, l, t, sf, ao, ut))

    cmd_ACE_BG_STATUS_help = '[EXPERIMENTAL] Show background unload states.'

    def cmd_ACE_BG_STATUS(self, gcmd):
        if not self.state:
            gcmd.respond_info('[bg-unload] no bg operations yet')
            return
        for h in sorted(self.state):
            gcmd.respond_info('[bg-unload] head %d: %s'
                              % (self._dh(h), self.state[h]))

    def _bg_dec_log(self, ace, head, slot, kind, length, span_tuple):
        """[diag] one decoder-span line for a bg retract segment, same
        'unload-dec' format as the inline unload. See BG_UNLOAD_DECODER_DIAG.
        head/slot are raw 0-based (matches the inline log)."""
        if not BG_UNLOAD_DECODER_DIAG:
            return
        fl = getattr(ace, '_feedlog', None)
        if fl is None:
            return
        try:
            _s, _n, _mn, _mx = span_tuple
            fl.info('unload-dec head=%d slot=%d kind=%s len=%d span=%s n=%s '
                    'min=%s max=%s'
                    % (head, slot, kind, int(length), _s, _n, _mn, _mx))
        except Exception:
            pass

    def _run_unload(self, head, ace_idx, slot, temp, soft):
        ace = self.printer.lookup_object('ace')
        toolhead = self.printer.lookup_object('toolhead')
        ext = self.printer.lookup_object(self._ext_name(head))
        heater = ext.get_heater()
        pheaters = self.printer.lookup_object('heaters')
        retract_done = {'v': False}
        try:
            self._unload_core(head, ace, toolhead, ext, heater, pheaters,
                              ace_idx, slot, temp, soft, retract_done)
            self.state[head] = 'DONE'
            self._say('head %d: BACKGROUND UNLOAD COMPLETE - next '
                      'ACE_SWAP_HEAD/load on this head is load-only'
                      % self._dh(head))
        except Exception as e:
            self._fail_unload(head, heater, pheaters, e, retract_done['v'])
        finally:
            self._busy.discard(head)

    def _fail_unload(self, head, heater, pheaters, e, retract_done):
        reason = str(e) or e.__class__.__name__
        self.state[head] = 'FAILED:%s' % reason
        try:
            pheaters.set_temperature(heater, 0.)
        except Exception:
            pass
        if not retract_done:
            self._say('head %d: FAILED (%s) - head_source kept, filament '
                      'state unchanged; recover with a normal display/'
                      'web unload' % (self._dh(head), reason))
        else:
            self._say('head %d: FAILED after retract (%s) - treat the '
                      'head as unloaded, check the slot'
                      % (self._dh(head), reason))
        logging.exception('[multiACE] [bg-unload] head %d failed' % head)

    def _unload_core(self, head, ace, toolhead, ext, heater, pheaters,
                     ace_idx, slot, temp, soft, retract_done):
        """Steps 0-6 of the background unload (the HW-proven v0.8 body,
        moved verbatim). Raises on abort; on normal return the head is
        verifiably empty and bookkept. retract_done is a {'v': bool} ref
        for the caller's failure messaging (past the bulk = treat the
        head as unloaded)."""
        if True:
            ace._runout_suppress_heads.add(head)

            self.state[head] = 'FA_STOP'
            armed = ace._feed_assist_per_ace.get(ace_idx, -1)
            if isinstance(armed, int) and 0 <= armed <= 3:
                self._ace_send(ace, ace_idx, {
                    'method': 'stop_feed_assist', 'params': {'index': armed}})
                ace._feed_assist_per_ace[ace_idx] = -1
                self._say('head %d: FA stopped on ACE %d slot %d'
                          % (self._dh(head), self._dh(ace_idx),
                             self._dh(armed)))

            self.state[head] = 'HEAT'
            pheaters.set_temperature(heater, temp)
            self._say('head %d: heating to %.0f' % (self._dh(head), temp))
            deadline = self.reactor.monotonic() + HEAT_TIMEOUT
            _retgt_said = False
            while True:
                self._check_docked(toolhead, ext, head)
                cur, _tgt = heater.get_temp(self.reactor.monotonic())
                if cur >= temp - HEAT_HYST:
                    break
                if _tgt < temp - HEAT_HYST:
                    if not _retgt_said:
                        self._say('head %d: heater target was reset '
                                  'externally (%.0f) - re-asserting %.0f'
                                  % (self._dh(head), _tgt, temp))
                        _retgt_said = True
                    pheaters.set_temperature(heater, temp)
                if self.reactor.monotonic() > deadline:
                    raise RuntimeError('heat timeout (%.0f/%.0f)'
                                       % (cur, temp))
                self._pause(0.5)

            self.state[head] = 'PULL'
            seq = None
            try:
                seq = ace.tipform_table_for(
                    ace._tipform_material_for(head),
                    vendor=ace._tipform_vendor_for(head), soft=bool(soft))
            except Exception:
                seq = None
            if seq is not None:
                self._say('head %d: custom tip-form table (%d tokens)'
                          % (self._dh(head), len(seq)))
            else:
                seq = [('move', d, f) for d, f in
                       (COLD_PULL_SOFT if soft else COLD_PULL_NORMAL)]
            unwind_speed = ACE_UNWIND_SPEED_FALLBACK
            try:
                unwind_speed = int(ace.get_retract_speed(ace_idx))
            except Exception:
                pass
            reclaimed = 0.
            fwd_assist = False
            _fan_warned = False
            for tok in seq:
                self._check_docked(toolhead, ext, head)
                kind = tok[0]
                if kind == 'pause':
                    self._pause(float(tok[1]))
                    continue
                if kind == 'temp':
                    pheaters.set_temperature(heater, float(tok[1]))
                    continue
                if kind == 'waittemp':
                    c = float(tok[1])
                    pheaters.set_temperature(heater, c)
                    _wt_deadline = self.reactor.monotonic() + 180.
                    while True:
                        self._check_docked(toolhead, ext, head)
                        cur, _t = heater.get_temp(self.reactor.monotonic())
                        if abs(cur - c) <= 3.:
                            break
                        if self.reactor.monotonic() > _wt_deadline:
                            self._say('head %d: waittemp:%d not reached in '
                                      '180s (at %.0f) - continuing'
                                      % (self._dh(head), int(c), cur))
                            break
                        self._pause(0.5)
                    continue
                if kind == 'fan':
                    if not _fan_warned:
                        self._say('head %d: tip-form fan: token skipped '
                                  '(not addressable on a parked head)'
                                  % self._dh(head))
                        _fan_warned = True
                    continue
                dist, feedrate = float(tok[1]), float(tok[2])
                speed = feedrate / 60.
                if dist < 0.:
                    ln = int(round(-dist))
                    if ln < 3:
                        _s, end, _en = self.queue_move(ext, dist, speed,
                                                          CHOREO_ACCEL)
                        self._wait_move(toolhead, end)
                        continue
                    if fwd_assist:
                        ace._feed_assist_per_ace[ace_idx] = -1
                        fwd_assist = False
                    ok = self._ace_unwind(ace, ace_idx, slot, ln,
                                          unwind_speed, wait=False)
                    _s, end, _en = self.queue_move(ext, dist, speed,
                                                      CHOREO_ACCEL)
                    self._wait_move(toolhead, end)
                    self._pause(max(1.0, ln / max(unwind_speed, 1) + 0.5))
                    if ok:
                        reclaimed += ln
                    else:
                        self._say('head %d: WARN unwind %dmm rejected by '
                                  'the device - bowden slack accumulating'
                                  % (self._dh(head), ln))
                else:
                    if not fwd_assist and ace._is_v2_idx(ace_idx):
                        resp = self._ace_send(ace, ace_idx, {
                            'method': 'start_feed_assist',
                            'params': {'index': slot}})
                        if not self._resp_rejected(resp):
                            ace._feed_assist_per_ace[ace_idx] = slot
                            fwd_assist = True
                    _s, end, _en = self.queue_move(ext, dist, speed,
                                                      CHOREO_ACCEL)
                    self._wait_move(toolhead, end)
            if fwd_assist:
                self._ace_send(ace, ace_idx, {
                    'method': 'stop_feed_assist', 'params': {'index': slot}})
                ace._feed_assist_per_ace[ace_idx] = -1
            self._say('head %d: cold-pull done (%d mm confirmed reclaimed '
                      'at ACE)' % (self._dh(head), int(reclaimed)))

            pheaters.set_temperature(heater, 0.)

            self.state[head] = 'RETRACT'
            try:
                _srl = int(ace.get_swap_retract_length(ace_idx, slot))
            except Exception:
                _srl = 0
            try:
                full = _srl if _srl > 0 else int(
                    ace.get_retract_length(ace_idx, slot))
            except Exception:
                full = 1950
            rest = max(0, full - int(reclaimed))
            self._check_docked(toolhead, ext, head)
            deadline = self.reactor.monotonic() + 15.0
            while self.reactor.monotonic() < deadline:
                st = self._slot_status(ace, ace_idx, slot)
                if not any(m in st for m in ('rollback', 'feeding')):
                    break
                self._pause(0.3)
            self._pause(1.0)
            if rest > 0:
                short = min(int(BG_UNLOAD_PROBE_RETRACT), rest)
                self._say('head %d: ACE %d slot %d retract %d mm (%d short + '
                          '%d rest, probe+decoder-verified) @%d'
                          % (self._dh(head), self._dh(ace_idx),
                             self._dh(slot), rest, short, rest - short,
                             unwind_speed))
                _sk = {'v': False}
                def _do_short(_o=_sk):
                    _o['v'] = self._ace_unwind(ace, ace_idx, slot, short,
                                               unwind_speed, wait=True,
                                               retries=4)
                _sps = ace._retract_with_decoder_span(ace_idx, slot, _do_short)
                self._bg_dec_log(ace, head, slot, 'bg-short', short, _sps)
                if not _sk['v']:
                    raise RuntimeError('bg short retract not confirmed by the '
                                       'device (slot %d)' % slot)
                _span = _sps[0]
                if _span is not None and _span < short * BG_UNLOAD_STALL_FRAC:
                    raise RuntimeError(
                        'bg short retract STALLED (decoder span %s < %d of '
                        '%dmm) - filament likely stuck, handing to inline'
                        % (_span, int(short * BG_UNLOAD_STALL_FRAC), short))
                self._gpio_diag(head, 'after short retract (expected clear)')
                rest2 = rest - short
                if rest2 > 0:
                    _rk = {'v': False}
                    def _do_rest(_o=_rk):
                        _o['v'] = self._ace_unwind(ace, ace_idx, slot, rest2,
                                                   unwind_speed, wait=True,
                                                   retries=4)
                    _spr = ace._retract_with_decoder_span(ace_idx, slot,
                                                          _do_rest)
                    self._bg_dec_log(ace, head, slot, 'bg-rest', rest2, _spr)
                    if not _rk['v']:
                        raise RuntimeError('bg rest retract not confirmed by '
                                           'the device (slot %d)' % slot)
            retract_done['v'] = True

            self.state[head] = 'BOOKKEEP'
            try:
                sensor = self.printer.lookup_object(
                    'filament_motion_sensor e%d_filament' % head, None)
                if sensor is not None:
                    sensor.runout_helper.note_filament_present(False, True)
            except Exception as e:
                self._say('head %d: sensor state update failed (%s) - the '
                          'next inline unload probe will clear it'
                          % (self._dh(head), e))
            ace._head_source[head] = None
            try:
                ace._bg_load_unverified.discard(head)
                getattr(ace, '_bg_prime_deficit', {}).pop(head, None)
            except Exception:
                pass
            try:
                ace._save_head_source()
                ace._push_slot_rfid_to_extruder(head)
                ace._push_rfid_info()
            except Exception:
                pass

    def _run_swap(self, head, un, ld, temp, soft, anti_ooze,
                  unload_temp=None):
        """Background SWAP: optional unload (un=(ace,slot) or None when the
        head is already empty), then feed+grip+prime of ld=(ace,slot).
        Unload failures keep the proven v0.8 semantics (_fail_unload);
        load failure semantics live in _load_core: ANY abort after filament
        movement leaves it STAGED in the path (at the sensor or mid-bowden,
        position agnostic - follow-ups are sensor-gated), a GRIP/PRIME
        abort declares the head loaded (it factually is)."""
        ace = self.printer.lookup_object('ace')
        toolhead = self.printer.lookup_object('toolhead')
        ext = self.printer.lookup_object(self._ext_name(head))
        heater = ext.get_heater()
        pheaters = self.printer.lookup_object('heaters')
        retract_done = {'v': False}
        try:
            if un is not None:
                self._unload_core(head, ace, toolhead, ext, heater,
                                  pheaters, un[0], un[1],
                                  (unload_temp or temp), soft,
                                  retract_done)
                if self.load_enabled:
                    self._say('head %d: unload half done - loading ACE %d '
                              'slot %d in the background'
                              % (self._dh(head), self._dh(ld[0]),
                                 self._dh(ld[1])))
        except Exception as e:
            self._fail_unload(head, heater, pheaters, e, retract_done['v'])
            self._busy.discard(head)
            return
        if not self.load_enabled:
            self.state[head] = 'DONE'
            self._say('head %d: %s (load_enabled=False) - the arrival '
                      'swap loads inline'
                      % (self._dh(head),
                         'BACKGROUND UNLOAD COMPLETE' if un is not None
                         else 'already empty, nothing to do'))
            self._busy.discard(head)
            return
        try:
            self._load_core(head, ace, toolhead, ext, heater, pheaters,
                            ld[0], ld[1], temp, anti_ooze)
            self.state[head] = 'DONE'
            _deficit = None
            try:
                _deficit = getattr(ace, '_bg_prime_deficit', {}).get(head)
            except Exception:
                pass
            if _deficit is not None:
                self._say('head %d: BACKGROUND SWAP handed over with a '
                          'SHORT prime (%d mm pending) - the arrival pick '
                          'tops it up before printing'
                          % (self._dh(head), int(_deficit)))
            else:
                self._say('head %d: BACKGROUND SWAP COMPLETE - ACE %d slot '
                          '%d loaded + primed; the arrival toolchange is a '
                          'no-op'
                          % (self._dh(head), self._dh(ld[0]),
                             self._dh(ld[1])))
        except Exception as e:
            reason = str(e) or e.__class__.__name__
            self.state[head] = 'FAILED:%s' % reason
            try:
                pheaters.set_temperature(heater, 0.)
            except Exception:
                pass
            self._say('head %d: LOAD FAILED (%s) - the arrival swap loads '
                      'inline' % (self._dh(head), reason))
            logging.exception('[multiACE] [bg-load] head %d failed' % head)
        finally:
            self._busy.discard(head)

    def _load_bookkeeping(self, head, ace, ace_idx, slot):
        """Mark the head loaded through the same fields the inline load
        stamps (head_source shape = filament_feed_ace FEED_ACT_LOAD finish,
        incl. the RFID identity), and set the sensor present via the
        official helper - which also LIFTS the runout suppression the
        unload half installed."""
        si = {}
        try:
            info = ace._info_per_ace.get(ace_idx) or {}
            slots = info.get('slots') or []
            if slot < len(slots) and isinstance(slots[slot], dict):
                si = slots[slot]
        except Exception:
            pass
        ace._head_source[head] = {
            'ace_index': ace_idx,
            'slot': slot,
            'type': si.get('type', 'PLA'),
            'color': ace.rgb2hex(*si.get('color', (0, 0, 0))),
            'brand': si.get('brand', 'Generic'),
        }
        try:
            ace._save_head_source()
            ace._ghost_heads.discard(head)
        except Exception:
            pass
        try:
            ace._bg_load_unverified.add(head)
        except Exception:
            pass
        try:
            sensor = self.printer.lookup_object(
                'filament_motion_sensor e%d_filament' % head, None)
            if sensor is not None:
                sensor.runout_helper.note_filament_present(True, True)
        except Exception as e:
            self._say('head %d: sensor present-flag update failed (%s)'
                      % (self._dh(head), e))
        try:
            ace._push_slot_rfid_to_extruder(head)
            ace._push_rfid_info()
        except Exception:
            pass
    def _load_core(self, head, ace, toolhead, ext, heater, pheaters,
                   ace_idx, slot, temp, anti_ooze):
        """Background LOAD of ld slot into an EMPTY parked head: sensor-
        stopped bowden feed (heat runs in parallel - the transport needs
        none), extruder grip with forward-assist, prime through the OPEN
        dock into the bin, anti-ooze end retract. Abort semantics: ANY
        abort after filament movement leaves it STAGED in the path (at the
        sensor or mid-bowden - _bg_staged / _bg_left_empty; every follow-up
        feed is sensor-gated, the same-slot arrival just continues it, a
        different-slot load is refused); grip/prime aborts bookkeep the
        head as loaded and return normally. Only 'none' (nothing moved)
        hands over a genuinely empty head."""
        st = self._slot_status(ace, ace_idx, slot)
        try:
            if ace._is_empty_status(st):
                raise RuntimeError('target slot %d is empty (%s)'
                                   % (slot, st))
        except AttributeError:
            pass

        self.state[head] = 'LD_FEED'
        pheaters.set_temperature(heater, temp)
        feed_speed = ACE_UNWIND_SPEED_FALLBACK
        try:
            feed_speed = int(ace.get_feed_speed(ace_idx))
        except Exception:
            pass
        feed_len = int(ace.get_load_length(ace_idx, slot))
        self._check_docked(toolhead, ext, head)
        self._say('head %d: ACE %d slot %d bg feed up to %d mm @%d to the '
                  'toolhead sensor (heating to %.0f in parallel)'
                  % (self._dh(head), self._dh(ace_idx), self._dh(slot),
                     feed_len, feed_speed, temp))
        _retries = 0
        try:
            _retries = int(ace.head_load_retry.get(head, ace.load_retry))
        except Exception:
            _retries = int(getattr(ace, 'load_retry', 3))
        try:
            _retry_back = int(ace.head_load_retry_retract.get(
                head, ace.load_retry_retract))
        except Exception:
            _retry_back = 50
        feed_res, _fsp = 'none', (None, 0, None, None)
        _ever_moved = False
        for _attempt in range(_retries + 1):
            if _attempt > 0:
                self._check_docked(toolhead, ext, head)
                self._say('head %d: feed retry %d/%d - %dmm back, re-push '
                          'to the sensor'
                          % (self._dh(head), _attempt, _retries, _retry_back))
                self._ace_unwind(ace, ace_idx, slot, _retry_back,
                                 feed_speed, wait=False)
                self._pause(max(1.0, _retry_back / max(feed_speed, 1) + 0.5))
            feed_res, _fsp = self._ace_feed_to_gears(
                ace, ace_idx, slot, feed_len, feed_speed, head)
            self._bg_dec_log(ace, head, slot, 'bg-feed', feed_len, _fsp)
            if feed_res in ('ok', 'stale'):
                break
            if feed_res == 'partial':
                _ever_moved = True
        if feed_res == 'stale':
            pheaters.set_temperature(heater, 0.)
            raise RuntimeError(
                'toolhead sensor of head %d reads PRESENT although the head '
                'is empty (stale latch) - display/web unload it once, then '
                'retry' % self._dh(head))
        if feed_res != 'ok':
            pheaters.set_temperature(heater, 0.)
            if feed_res == 'partial' or _ever_moved:
                try:
                    ace._bg_left_empty.add(head)
                    getattr(ace, '_bg_staged', {})[head] = (ace_idx, slot)
                except Exception:
                    pass
                self._say('head %d: feed never reached the sensor after %d '
                          'attempt(s) - filament stays in the path (STAGED, '
                          'mid-bowden); the arrival load continues it'
                          % (self._dh(head), _retries + 1))
            raise RuntimeError('feed not confirmed by the device')

        fa_armed = self._fa_on(ace, ace_idx, slot)
        self._say('head %d: fed to the toolhead sensor, FA %s'
                  % (self._dh(head), 'ON' if fa_armed else
                     'not armed (busy) - grip/pick will re-arm'))

        gripped = False
        try:
            self._check_docked(toolhead, ext, head)
            deadline = self.reactor.monotonic() + HEAT_TIMEOUT
            _retgt_said = False
            while True:
                cur, _tgt = heater.get_temp(self.reactor.monotonic())
                if cur >= temp - HEAT_HYST:
                    break
                if _tgt < temp - HEAT_HYST:
                    if not _retgt_said:
                        self._say('head %d: heater target was reset '
                                  'externally (%.0f) - re-asserting %.0f'
                                  % (self._dh(head), _tgt, temp))
                        _retgt_said = True
                    pheaters.set_temperature(heater, temp)
                if self.reactor.monotonic() > deadline:
                    raise RuntimeError('heat timeout (%.0f/%.0f)'
                                       % (cur, temp))
                self._check_docked(toolhead, ext, head)
                self._pause(0.5)

            prime_target = (float(ace.get_purge_length() or 0) or 80.) \
                + BG_LOAD_PRIME_EXTRA
            primed = 0.
            ooze_done = False

            self.state[head] = 'LD_GRIP'
            self._check_docked(toolhead, ext, head)
            press = (BG_LOAD_PRESS_V2 if ace._is_v2_idx(ace_idx)
                     else BG_LOAD_PRESS_V1)
            self._ace_send(ace, ace_idx, {
                'method': 'stop_feed_assist', 'params': {'index': slot}})
            presp = None
            for _pa in range(BG_LOAD_PRESS_RETRIES):
                presp = self._ace_send(ace, ace_idx, {
                    'method': 'feed_filament',
                    'params': {'index': slot, 'length': int(press),
                               'speed': int(BG_LOAD_PRESS_SPEED)}})
                if not self._resp_rejected(presp):
                    break
                if _pa < BG_LOAD_PRESS_RETRIES - 1:
                    pdl = (self.reactor.monotonic()
                           + BG_LOAD_PRESS_RETRY_DELAY)
                    while self.reactor.monotonic() < pdl:
                        self._check_docked(toolhead, ext, head)
                        self._pause(0.3)
            if self._resp_rejected(presp):
                self._say('head %d: nip press rejected (busy) after %d '
                          'attempts - gripping without press'
                          % (self._dh(head), BG_LOAD_PRESS_RETRIES))
            else:
                pdeadline = (self.reactor.monotonic()
                             + press / float(BG_LOAD_PRESS_SPEED) + 1.0)
                while self.reactor.monotonic() < pdeadline:
                    self._check_docked(toolhead, ext, head)
                    self._pause(0.3)
                self._ace_send(ace, ace_idx, {
                    'method': 'stop_feed_filament',
                    'params': {'index': slot}})
                self._say('head %d: tip pressed into the gear nip '
                          '(<=%d mm%s)'
                          % (self._dh(head), int(press),
                             ', attempt %d' % (_pa + 1) if _pa else ''))
            self._fa_on(ace, ace_idx, slot)
            grip = BG_LOAD_GRIP_SEAT
            seg = grip / 4.
            for _i in range(4):
                self._check_docked(toolhead, ext, head)
                _s, end, _en = self.queue_move(ext, seg, BG_LOAD_GRIP_SPEED,
                                               CHOREO_ACCEL)
                self._wait_move(toolhead, end)
            gripped = True

            self.state[head] = 'LD_PRIME'
            seg = prime_target / 4.
            for _i in range(4):
                self._check_docked(toolhead, ext, head)
                _s, end, _en = self.queue_move(ext, seg,
                                               BG_LOAD_PRIME_SPEED,
                                               CHOREO_ACCEL)
                self._wait_move(toolhead, end)
                primed += seg

            if anti_ooze > 0.:
                self._check_docked(toolhead, ext, head)
                _s, end, _en = self.queue_move(ext, -float(anti_ooze),
                                               BG_LOAD_RETRACT_SPEED,
                                               CHOREO_ACCEL)
                self._wait_move(toolhead, end)
            ooze_done = True
        except Exception as e:
            self._ace_send(ace, ace_idx, {
                'method': 'stop_feed_assist', 'params': {'index': slot}})
            ace._feed_assist_per_ace[ace_idx] = -1
            if gripped:
                deficit = max(0., prime_target - primed)
                if deficit > 0. or not ooze_done:
                    try:
                        getattr(ace, '_bg_prime_deficit', {})[head] = deficit
                    except Exception:
                        pass
                self._say('head %d: pick during grip/prime (%s) - head is '
                          'loaded (prime %d/%d mm), bookkeeping and handing '
                          'over%s'
                          % (self._dh(head),
                             str(e) or e.__class__.__name__,
                             int(primed), int(prime_target),
                             '; the arrival pick tops up the missing '
                             '%d mm' % int(deficit) if deficit > 0.
                             else ' (small blob possible)'))
                self._load_bookkeeping(head, ace, ace_idx, slot)
                return
            self._say('head %d: abort before grip (%s) - filament stays '
                      'STAGED at the toolhead sensor; the arrival/inline '
                      'load continues from there'
                      % (self._dh(head), str(e) or e.__class__.__name__))
            pheaters.set_temperature(heater, 0.)
            self._gpio_diag(head, 'staged after abort (expected present)')
            try:
                ace._bg_left_empty.add(head)
                getattr(ace, '_bg_staged', {})[head] = (ace_idx, slot)
            except Exception:
                pass
            raise RuntimeError(str(e) or e.__class__.__name__)

        self._ace_send(ace, ace_idx, {
            'method': 'stop_feed_assist', 'params': {'index': slot}})
        ace._feed_assist_per_ace[ace_idx] = -1
        try:
            if abs(float(getattr(heater, 'target_temp', 0.)) - temp) < 1.:
                pheaters.set_temperature(heater, 0.)
        except Exception:
            pheaters.set_temperature(heater, 0.)
        self._load_bookkeeping(head, ace, ace_idx, slot)

def load_config(config):
    return AceBgSwap(config)
