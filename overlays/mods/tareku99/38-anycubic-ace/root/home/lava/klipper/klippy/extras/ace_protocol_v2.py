import json
import logging
import os
import struct

from .ace_protocol import AceProtocol

V2_VENDOR_ID = '1a86'
V2_PRODUCT_IDS = ('55d3',)

PREAMBLE = b'\xFF\xAA'
END_MARKER = 0xFE
FLAG_REQUEST = 0x00
FLAG_RESPONSE = 0x80
HEADER_LEN = 7
TRAILER_LEN = 3
MIN_FRAME_LEN = HEADER_LEN + TRAILER_LEN
MAX_PAYLOAD_LEN = 100

class Cmd:
    DISCOVER_DEVICE = 0
    ASSIGN_DEVICE_ID = 1
    IAP_VERSION = 5
    GET_STATUS = 6
    GET_INFO = 7
    FEED_OR_ROLLBACK = 8
    STOP_FEED_OR_ROLLBACK = 9
    UPDATE_SPEED = 10
    DRYING = 11
    SET_DRY_TEMP = 12
    GET_FILAMENT_INFO = 13
    SET_RFID_ENABLE = 14
    SET_FEED_CHECK = 19
    GET_TEMP = 64
    SET_DRY_POWER = 65
    SET_VALVE = 66
    FILAMENT_IDENTIFY = 68
    RFID_TEST = 69
    SET_FAN = 71
    GET_KEY_STATE = 73
    GET_FEED_INFO = 76

WORK_STATES = {0: 'init', 1: 'ready', 2: 'busy', 3: 'upgrade'}
SLOT_STATES = {
    0: 'ready', 1: 'feeding', 2: 'rollback', 3: 'assisting',
    4: 'rollback_assisting', 5: 'preloading', 6: 'upgrading',
    129: 'feed_error', 130: 'rollback_error', 131: 'assist_error',
    132: 'preload_error', 133: 'stuck_error', 134: 'tangled_error',
    135: 'motor_error',
}
FILAMENT_STATES = {0: 'empty', 1: 'unknown', 2: 'identified', 3: 'identifying'}
DRY_STATES = {0: 'stop', 1: 'starting', 2: 'keeping',
              3: 'stopping', 4: 'ptc_error', 5: 'ntc_error'}

def crc16_kermit(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF

def pb_varint(value):
    r = bytearray()
    while value > 0x7F:
        r.append((value & 0x7F) | 0x80)
        value >>= 7
    r.append(value & 0x7F)
    return bytes(r)

def pb_uint32(field, value):
    return pb_varint((field << 3) | 0) + pb_varint(int(value))

def pb_bool(field, value):
    return pb_varint((field << 3) | 0) + pb_varint(1 if value else 0)

def pb_decode_varint(data, pos):
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    return result, pos

def pb_decode(data):
    fields, pos = {}, 0
    while pos < len(data):
        tag, pos = pb_decode_varint(data, pos)
        fnum, wtype = tag >> 3, tag & 7
        if wtype == 0:
            val, pos = pb_decode_varint(data, pos)
        elif wtype == 1:
            if pos + 8 > len(data):
                break
            val = struct.unpack_from('<d', data, pos)[0]
            pos += 8
        elif wtype == 2:
            ln, pos = pb_decode_varint(data, pos)
            val = bytes(data[pos:pos + ln])
            pos += ln
        elif wtype == 5:
            if pos + 4 > len(data):
                break
            val = struct.unpack_from('<f', data, pos)[0]
            pos += 4
        else:
            break
        fields.setdefault(fnum, []).append((wtype, val))
    return fields

def _fval(fields, num, default=0):
    return fields.get(num, [(0, default)])[0][1]

def _fstr(fields, num, default=''):
    val = fields.get(num, [(2, b'')])[0][1]
    if isinstance(val, bytes):
        try:
            return val.decode('utf-8')
        except UnicodeDecodeError:
            return val.hex()
    return default

FEED_MODE_FEED = 0
FEED_MODE_ROLLBACK = 1
FEED_MODE_ASSIST = 2
FEED_MODE_ROLLBACK_ASSIST = 3

class AceProtocolV2(AceProtocol):
    NAME = 'v2'
    DEFAULT_BAUD = 230400
    EXTRA_USB_IDS = ()
    SERIAL_KWARGS = {
        'timeout': 0.1,
    }

    @classmethod
    def _scan_v2_serial_paths(cls):
        ace_devices = []
        by_path_dir = '/dev/serial/by-path/'
        if not os.path.exists(by_path_dir):
            return ace_devices
        for entry in sorted(os.listdir(by_path_dir)):
            full_path = os.path.join(by_path_dir, entry)
            real_dev = os.path.basename(os.path.realpath(full_path))
            vendor, product = cls._read_usb_ids(real_dev)
            if vendor is None:
                continue
            if (vendor == V2_VENDOR_ID and product in V2_PRODUCT_IDS) \
                    or (vendor, product) in cls.EXTRA_USB_IDS:
                ace_devices.append(full_path)
        return ace_devices

    @classmethod
    def discover(cls):

        return cls._scan_v2_serial_paths()

    @classmethod
    def open_transport(cls, path, baud, **kwargs):

        import serial
        return serial.Serial(
            port=path,
            baudrate=cls.DEFAULT_BAUD,
            **cls.SERIAL_KWARGS,
        )

    def encode_request(self, request, next_id=None):
        seq = int(request.get('id', 1)) & 0xFFFF
        method = request.get('method', '')
        params = request.get('params', {}) or {}
        cmd, payload = self._method_to_v2(method, params)
        if len(payload) > MAX_PAYLOAD_LEN:
            raise ValueError(
                'V2 payload exceeds %d bytes for method %s' % (MAX_PAYLOAD_LEN, method))
        inner = bytearray([
            FLAG_REQUEST,
            seq & 0xFF, (seq >> 8) & 0xFF,
            cmd & 0xFF,
            len(payload) & 0xFF,
        ])
        inner.extend(payload)
        crc = crc16_kermit(bytes(inner))
        return bytes(PREAMBLE) + bytes(inner) + bytes([crc & 0xFF, (crc >> 8) & 0xFF, END_MARKER])

    def decode_frames(self, buffer):
        results = []
        while len(buffer) >= MIN_FRAME_LEN:
            start = buffer.find(PREAMBLE)
            if start < 0:
                if buffer.endswith(b'\xFF'):
                    del buffer[:-1]
                else:
                    del buffer[:]
                break
            if start > 0:
                del buffer[:start]
            if len(buffer) < HEADER_LEN:
                break
            payload_len = buffer[6]
            if payload_len > MAX_PAYLOAD_LEN:
                del buffer[:2]
                continue
            total_len = HEADER_LEN + payload_len + TRAILER_LEN
            if len(buffer) < total_len:
                break
            end_marker = buffer[total_len - 1]
            if end_marker != END_MARKER:
                del buffer[:2]
                continue
            inner = bytes(buffer[2:HEADER_LEN + payload_len])
            crc_in_frame = buffer[HEADER_LEN + payload_len] | (buffer[HEADER_LEN + payload_len + 1] << 8)
            crc_calc = crc16_kermit(inner)
            if crc_in_frame != crc_calc:
                logging.info('[multiACE] V2 CRC mismatch (calc=%04x frame=%04x), dropping frame',
                             crc_calc, crc_in_frame)
                del buffer[:total_len]
                continue
            flags = buffer[2]
            seq = buffer[3] | (buffer[4] << 8)
            cmd = buffer[5]
            payload = bytes(buffer[HEADER_LEN:HEADER_LEN + payload_len])
            del buffer[:total_len]
            if not (flags & FLAG_RESPONSE):
                continue
            ret = self._v2_response_to_v1(cmd, seq, payload)
            results.append(ret)
        return results

    def initial_handshake_requests(self):
        return [
            {'method': 'discover_device'},
            {'method': 'get_info'},
        ]

    def make_default_info(self):
        return {
            'status': 'ready',
            'dryer_status': {
                'status': 'stop',
                'target_temp': 0,
                'duration': 0,
                'remain_time': 0,
            },
            'temp': 0,
            'enable_rfid': 1,
            'fan_speed': 7000,
            'feed_assist_count': 0,
            'cont_assist_time': 0.0,
            'slots': [
                {'index': i, 'status': 'empty', 'sku': '', 'type': '',
                 'rfid': 0, 'brand': '', 'color': [0, 0, 0]}
                for i in range(4)
            ],
        }

    def _method_to_v2(self, method, params):
        if method == 'get_info':
            return Cmd.GET_INFO, b''
        if method == 'get_status':
            return Cmd.GET_STATUS, b''
        if method == 'discover_device':
            return Cmd.DISCOVER_DEVICE, b''
        if method == 'start_feed_assist':
            slot = int(params.get('index', 0))

            speed = int(params.get('speed', 10))
            payload = (pb_uint32(1, slot) + pb_uint32(2, speed)
                       + pb_uint32(3, 0) + pb_uint32(4, FEED_MODE_ASSIST))
            return Cmd.FEED_OR_ROLLBACK, payload
        if method == 'stop_feed_assist':
            slot = int(params.get('index', 0))
            return Cmd.STOP_FEED_OR_ROLLBACK, pb_uint32(1, slot)
        if method == 'feed_filament':
            slot = int(params.get('index', 0))
            length = int(params.get('length', 0))
            speed = int(params.get('speed', 50))
            payload = (pb_uint32(1, slot) + pb_uint32(2, speed)
                       + pb_uint32(3, length) + pb_uint32(4, FEED_MODE_FEED))
            return Cmd.FEED_OR_ROLLBACK, payload
        if method == 'unwind_filament':
            slot = int(params.get('index', 0))
            length = int(params.get('length', 0))
            speed = int(params.get('speed', 50))
            payload = (pb_uint32(1, slot) + pb_uint32(2, speed)
                       + pb_uint32(3, length) + pb_uint32(4, FEED_MODE_ROLLBACK))
            return Cmd.FEED_OR_ROLLBACK, payload
        if method == 'stop_feed_filament':
            slot = int(params.get('index', 0))
            return Cmd.STOP_FEED_OR_ROLLBACK, pb_uint32(1, slot)
        if method == 'update_feeding_speed':
            slot = int(params.get('index', 0))
            speed = int(params.get('speed', 50))
            return Cmd.UPDATE_SPEED, pb_uint32(1, slot) + pb_uint32(2, speed)
        if method == 'get_filament_info':
            slot = int(params.get('index', 0))
            return Cmd.GET_FILAMENT_INFO, pb_uint32(1, slot)
        if method == 'drying':
            temp = int(params.get('temp', 50))
            duration = int(params.get('duration', 0))
            payload = pb_uint32(1, temp) + pb_uint32(2, duration) + pb_bool(3, True)
            return Cmd.DRYING, payload
        if method == 'drying_stop':
            return Cmd.DRYING, pb_uint32(1, 0) + pb_uint32(2, 0)
        if method == 'set_fan_speed':
            speed = int(params.get('speed', 0))
            payload = (pb_uint32(1, speed)
                       + pb_bool(2, speed > 0) + pb_bool(3, speed > 0))
            return Cmd.SET_FAN, payload

        if method == 'feed_or_rollback_raw':
            slot = int(params.get('index', 0))
            speed = int(params.get('speed', 0))
            length = int(params.get('length', 0))
            mode = int(params.get('mode', 0))
            payload = (pb_uint32(1, slot) + pb_uint32(2, speed)
                       + pb_uint32(3, length) + pb_uint32(4, mode))
            return Cmd.FEED_OR_ROLLBACK, payload
        if method == 'get_temp':
            return Cmd.GET_TEMP, b''
        if method == 'get_feed_info':
            return Cmd.GET_FEED_INFO, b''
        if method == 'get_key_state':
            return Cmd.GET_KEY_STATE, b''
        if method == 'set_rfid_enable':
            slot = int(params.get('index', 0))
            enable = bool(params.get('enable', True))
            return Cmd.SET_RFID_ENABLE, pb_uint32(1, slot) + pb_bool(2, enable)
        if method == 'filament_identify':

            slot = int(params.get('index', 0))
            return Cmd.FILAMENT_IDENTIFY, pb_uint32(1, slot)
        if method == 'rfid_test':

            enable = bool(params.get('enable', True))
            return Cmd.RFID_TEST, pb_bool(1, enable)
        if method == 'set_dry_temp':
            temp = int(params.get('temp', 50))
            return Cmd.SET_DRY_TEMP, pb_uint32(1, temp)
        if method == 'set_valve':
            v1 = bool(params.get('v1', False))
            v2 = bool(params.get('v2', False))
            return Cmd.SET_VALVE, pb_bool(1, v1) + pb_bool(2, v2)
        if method == 'set_feed_check':
            check_len = int(params.get('check_length', 254))
            error_len = int(params.get('error_length', 254))
            return Cmd.SET_FEED_CHECK, (pb_uint32(1, check_len)
                                        + pb_uint32(2, error_len))
        if method == 'set_fan_raw':
            speed = int(params.get('speed', 0))
            f1 = bool(params.get('fan1', False))
            f2 = bool(params.get('fan2', False))
            return Cmd.SET_FAN, (pb_uint32(1, speed)
                                 + pb_bool(2, f1) + pb_bool(3, f2))
        if method == 'drying_raw':
            temp = int(params.get('temp', 50))
            duration = int(params.get('duration', 120))
            auto_roll = bool(params.get('auto_roll', True))
            return Cmd.DRYING, (pb_uint32(1, temp) + pb_uint32(2, duration)
                                + pb_bool(3, auto_roll))
        if method == 'assign_device_id':
            return Cmd.ASSIGN_DEVICE_ID, (
                pb_uint32(1, int(params.get('uid1', 0)))
                + pb_uint32(2, int(params.get('uid2', 0)))
                + pb_uint32(3, int(params.get('uid3', 0)))
                + pb_uint32(4, int(params.get('device_id', 1))))
        if method == 'raw':
            cmd_id = int(params.get('cmd', 0))
            hex_payload = params.get('hex', '') or ''
            try:
                payload = bytes.fromhex(hex_payload) if hex_payload else b''
            except ValueError:
                logging.info('[multiACE] V2 raw: invalid hex %r', hex_payload)
                payload = b''
            return cmd_id, payload
        logging.info('[multiACE] V2: unknown V1 method %r, falling back to GET_STATUS', method)
        return Cmd.GET_STATUS, b''

    def _v2_response_to_v1(self, cmd, seq, payload):
        ret = {'id': seq, 'code': 0, 'msg': 'success', 'result': {}}
        if not payload:
            return ret
        try:
            fields = pb_decode(payload)
        except Exception as e:
            logging.info('[multiACE] V2 protobuf decode failure cmd=%d: %s', cmd, e)
            return ret
        if cmd == Cmd.DISCOVER_DEVICE:
            ret['result'] = {
                'uid1': _fval(fields, 1),
                'uid2': _fval(fields, 2),
                'uid3': _fval(fields, 3),
            }
        elif cmd == Cmd.GET_INFO:
            version = _fstr(fields, 1, '')
            boot = _fstr(fields, 2, '')
            ret['result'] = {
                'model': 'ACE 2 Pro',
                'firmware': version,
                'boot_version': boot,
            }
        elif cmd == Cmd.GET_STATUS:
            ret['result'] = self._decode_status(fields)
        elif cmd == Cmd.GET_TEMP:
            ret['result'] = {
                'box1_temp': _fval(fields, 1, 0.0),
                'box2_temp': _fval(fields, 2, 0.0),
                'ptc1_temp': _fval(fields, 3, 0.0),
                'ptc2_temp': _fval(fields, 4, 0.0),
                'env_temp': _fval(fields, 5, 0.0),
                'env_humidity': _fval(fields, 6, 0.0),
            }
        elif cmd in (Cmd.GET_FILAMENT_INFO, Cmd.FILAMENT_IDENTIFY):
            sku = _fstr(fields, 3, '')
            ftype = _fstr(fields, 4, '')
            color = [0, 0, 0]
            for wtype, color_payload in fields.get(5, []):
                if wtype != 2:
                    continue
                csub = pb_decode(color_payload)
                rgba = _fval(csub, 1, 0)

                color = [(rgba >> 24) & 0xFF,
                         (rgba >> 16) & 0xFF,
                         (rgba >> 8) & 0xFF]
                break
            ret['result'] = {
                'index': _fval(fields, 1, 0),
                'sku': sku,
                'type': ftype,
                'brand': '',
                'color': color,
                'rfid': 2 if ftype else 0,
            }
        elif cmd == Cmd.GET_FEED_INFO:

            slots = []
            for wtype, fi_payload in fields.get(1, []):
                if wtype != 2:
                    continue
                fi = pb_decode(fi_payload)
                slots.append({
                    'index': len(slots),
                    'steps': _fval(fi, 1, 0),
                    'length': _fval(fi, 2, 0),
                    'decoder': _fval(fi, 3, 0),
                })
            ret['result'] = {'feed_info': slots}
        elif cmd == Cmd.GET_KEY_STATE:

            ret['result'] = {'fields': {
                str(k): _fval(fields, k, 0) for k in fields
            }}
        else:
            code = _fval(fields, 1, 0)
            if isinstance(code, int) and code != 0:
                ret['code'] = code
                ret['msg'] = 'error_%d' % code
        return ret

    def _decode_status(self, fields):
        slots = []
        for wtype, slot_payload in fields.get(9, []):
            if wtype != 2:
                continue
            sub = pb_decode(slot_payload)
            slot_state = SLOT_STATES.get(_fval(sub, 1, 0), 'unknown')
            filament_state = FILAMENT_STATES.get(_fval(sub, 2, 0), 'empty')

            slots.append({
                'index': len(slots),
                'status': filament_state if filament_state != 'identified' else 'ready',
                'slot_status': slot_state,
                'sku': '', 'type': '',
                'rfid': 2 if filament_state == 'identified' else 0,
                'brand': '',
                'color': [0, 0, 0],
            })
        while len(slots) < 4:
            slots.append({
                'index': len(slots),
                'status': 'empty', 'slot_status': 'unknown',
                'sku': '', 'type': '', 'rfid': 0, 'brand': '',
                'color': [0, 0, 0],
            })

        dry_status = {'status': 'stop', 'target_temp': 0,
                      'duration': 0, 'remain_time': 0}
        for wtype, dry_payload in fields.get(2, []):
            if wtype != 2:
                continue
            dsub = pb_decode(dry_payload)
            dry_status = {
                'status': DRY_STATES.get(_fval(dsub, 1, 0), 'stop'),
                'target_temp': _fval(dsub, 2, 0),
                'duration': _fval(dsub, 3, 0),
                'remain_time': _fval(dsub, 4, 0),
            }
            break

        any_busy = any(
            s.get('slot_status') in ('feeding', 'rollback', 'preloading')
            for s in slots)
        return {
            'status': 'busy' if any_busy else 'ready',
            'dryer_status': dry_status,
            'temp': _fval(fields, 3, 0),
            'humidity': _fval(fields, 4, 0),
            'enable_rfid': 1 if _fval(fields, 5, 0) else 0,
            'fan_speed': 0,
            'feed_assist_count': _fval(fields, 7, 0),
            'cont_assist_time': float(_fval(fields, 8, 0)),
            'slots': slots,
        }
