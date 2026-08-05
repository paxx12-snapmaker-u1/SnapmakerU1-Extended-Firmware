
import logging

TOKEN_MOVE = 'move'
TOKEN_PAUSE = 'pause'
TOKEN_TEMP = 'temp'
TOKEN_WAITTEMP = 'waittemp'
TOKEN_FAN = 'fan'

MAX_MOVE_MM = 200.
MAX_FEEDRATE = 6000.
MAX_PAUSE_MS = 60000.
MAX_TEMP = 350.
MIN_WAITTEMP = 100.
MIN_MOVE_TEMP = 175.

def parse_table(raw):
    """Parse a table string into a token list:
    ('move', mm, feedrate) / ('pause', seconds) / ('temp', C) /
    ('waittemp', C) / ('fan', n).
    Raises ValueError with a user-readable message on any bad token."""
    tokens = []
    net = 0.
    unload_temp = None
    last_waittemp = None
    for part in str(raw).replace('\n', ',').split(','):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if low.startswith('pause:'):
            ms = float(low.split(':', 1)[1])
            if not 0 <= ms <= MAX_PAUSE_MS:
                raise ValueError('pause out of range (0-%d ms): %r'
                                 % (int(MAX_PAUSE_MS), part))
            tokens.append((TOKEN_PAUSE, ms / 1000.))
        elif low.startswith('temp:'):
            c = float(low.split(':', 1)[1])
            if not 0 <= c <= MAX_TEMP:
                raise ValueError('temp out of range (0-%d C): %r'
                                 % (int(MAX_TEMP), part))
            tokens.append((TOKEN_TEMP, c))
        elif low.startswith('waittemp:'):
            c = float(low.split(':', 1)[1])
            if not MIN_WAITTEMP <= c <= MAX_TEMP:
                raise ValueError('waittemp out of range (%d-%d C): %r'
                                 % (int(MIN_WAITTEMP), int(MAX_TEMP), part))
            tokens.append((TOKEN_WAITTEMP, c))
            last_waittemp = c
        elif low.startswith('fan:'):
            v = int(float(low.split(':', 1)[1]))
            if not 0 <= v <= 255:
                raise ValueError('fan out of range (0-255): %r' % part)
            tokens.append((TOKEN_FAN, v))
        elif low.startswith('unloadtemp:'):
            c = float(low.split(':', 1)[1])
            if not MIN_MOVE_TEMP <= c <= MAX_TEMP:
                raise ValueError('unloadtemp out of range (%d-%d C): %r'
                                 % (int(MIN_MOVE_TEMP), int(MAX_TEMP), part))
            unload_temp = c
        elif '@' in part:
            mm_s, f_s = part.split('@', 1)
            try:
                mm = float(mm_s)
                feedrate = float(f_s)
            except ValueError:
                raise ValueError('bad move token %r (expected '
                                 '<mm>@<feedrate>, comma-separated)' % part)
            if abs(mm) > MAX_MOVE_MM:
                raise ValueError('move too long (max %d mm): %r'
                                 % (int(MAX_MOVE_MM), part))
            if not 0 < feedrate <= MAX_FEEDRATE:
                raise ValueError('feedrate out of range (1-%d mm/min): %r'
                                 % (int(MAX_FEEDRATE), part))
            if last_waittemp is not None and last_waittemp < MIN_MOVE_TEMP:
                raise ValueError(
                    'move %r after waittemp:%d - Klipper refuses extruder '
                    'moves below min_extrude_temp (170); use waittemp >= '
                    '%d before further moves (true cold pulls below that '
                    'are not possible on the inline path)'
                    % (part, int(last_waittemp), int(MIN_MOVE_TEMP)))
            net += mm
            tokens.append((TOKEN_MOVE, mm, feedrate))
        else:
            raise ValueError("unrecognised token %r (expected mm@feedrate, "
                             "pause:ms, temp:C, waittemp:C, fan:0-255 "
                             "or unloadtemp:C)"
                             % part)
    if tokens and not any(t[0] == TOKEN_MOVE for t in tokens):
        raise ValueError('table has no moves')
    if not tokens and unload_temp is None:
        raise ValueError('table has no moves')
    if net > 0.:
        raise ValueError('table pushes a NET %+.1f mm - the tip must end '
                         'retracted out of the melt zone' % net)
    return tokens, unload_temp

class AceTipform:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.mode = config.get('mode', 'stock').strip().lower()
        if self.mode not in ('stock', 'custom'):
            raise config.error(
                "[ace_tipform] mode must be 'stock' or 'custom' (got %r)"
                % self.mode)
        self.tables = {}
        self.unload_temps = {}
        for opt in config.get_prefix_options(''):
            if opt == 'mode':
                continue
            raw = config.get(opt)
            try:
                _toks, _utemp = parse_table(raw)
                if _toks:
                    self.tables[opt.strip().lower()] = _toks
                if _utemp is not None:
                    self.unload_temps[opt.strip().lower()] = _utemp
            except ValueError as e:
                logging.error('[multiACE] ace_tipform: table %r DISABLED '
                              '(falls back to stock): %s' % (opt, e))
        logging.info('[multiACE] ace_tipform: mode=%s tables=%s '
                     'unload_temps=%s'
                     % (self.mode, sorted(self.tables.keys()) or 'none',
                        sorted(self.unload_temps.keys()) or 'none'))
        if self.unload_temps and self.mode != 'custom':
            logging.error(
                "[multiACE] ace_tipform: unloadtemp set for %s but mode is "
                "'stock' - IGNORED. Set 'mode: custom' to activate "
                "(choreography stays stock for param-only tables)."
                % sorted(self.unload_temps.keys()))

    def table_for(self, material, vendor=None, soft=False):
        """The custom table for a (vendor, material), or None = use the stock
        path (INNER macro inline / built-in table in the bg engine).

        Precedence: '<vendor>_<material>' -> '<material>' -> 'soft' ->
        'default'. The vendor key is CONSTRUCTED and checked for membership
        (never parsed back) - a missing/empty/generic vendor simply doesn't
        match and falls through to the plain material, so no vendor
        canonicalisation is needed (the fallback is the safety net). The
        join is '_' (not a space): a space is invalid in a Klipper config
        option name and the web editor's key validator forbids it. The
        vendor part itself is lowercased and its internal spaces collapsed
        to '_' so 'Prusament PLA' and a vendor field 'Prusa Research' both
        land on a stable key."""
        if self.mode != 'custom':
            return None
        mat = (material or '').strip().lower()
        ven = '_'.join((vendor or '').strip().lower().split())
        if mat and ven and ven not in ('none', 'generic'):
            vkey = '%s_%s' % (ven, mat)
            if vkey in self.tables:
                return self.tables[vkey]
        if mat and mat in self.tables:
            return self.tables[mat]
        if soft and 'soft' in self.tables:
            return self.tables['soft']
        return self.tables.get('default')

    def unload_temp_for(self, material, vendor=None, soft=False):
        """The custom unload temp for a (vendor, material), or None = fall
        through to the filament-DB chain (get_unload_temp -> load temp).
        Same precedence + key normalisation as table_for, same mode gate:
        the whole section is inert in mode: stock (a param-only table
        just means stock CHOREOGRAPHY at the custom temp)."""
        if self.mode != 'custom':
            return None
        mat = (material or '').strip().lower()
        ven = '_'.join((vendor or '').strip().lower().split())
        if mat and ven and ven not in ('none', 'generic'):
            vkey = '%s_%s' % (ven, mat)
            if vkey in self.unload_temps:
                return self.unload_temps[vkey]
        if mat and mat in self.unload_temps:
            return self.unload_temps[mat]
        if soft and 'soft' in self.unload_temps:
            return self.unload_temps['soft']
        return self.unload_temps.get('default')

    def get_status(self, eventtime):
        return {
            'mode': self.mode,
            'tables': sorted(set(self.tables.keys())
                             | set(self.unload_temps.keys())),
            'unload_temps': dict(self.unload_temps),
        }

def load_config(config):
    return AceTipform(config)
