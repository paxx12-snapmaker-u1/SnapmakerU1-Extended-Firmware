# Adapted from hakimio/U1-Ace ace2_protocol.py.
# ACE 2 Pro transport: CH343 USB-UART, 230400 baud, protobuf payloads.
import struct

PREAMBLE = b"\xff\xaa"
END_MARKER = 0xFE
FLAG_REQUEST = 0x00

CMD_DISCOVER_DEVICE = 0
CMD_ASSIGN_DEVICE_ID = 1
CMD_GET_STATUS = 6
CMD_GET_INFO = 7
CMD_FEED_OR_ROLLBACK = 8
CMD_STOP_FEED_OR_ROLLBACK = 9
CMD_UPDATE_SPEED = 10
CMD_DRYING = 11
CMD_GET_FILAMENT_INFO = 13
CMD_SET_FEED_CHECK = 19
CMD_GET_TEMP = 64

FEED_MODE_FEED = 0
FEED_MODE_ROLLBACK = 1
FEED_MODE_FEED_ASSIST = 2
FEED_MODE_UNWIND_ASSIST = 3

SLOT_READY = 0
SLOT_FEEDING = 1
SLOT_ROLLBACK = 2
SLOT_ASSISTING = 3
SLOT_ROLLBACK_ASSISTING = 4
SLOT_PRELOADING = 5
SLOT_UPGRADING = 6
SLOT_FEED_ERROR = 129

SLOT_ERROR_STATUS_BY_RAW = {
    SLOT_FEED_ERROR: "feed_error",
    130: "rollback_error",
    131: "assist_error",
    132: "preload_error",
    133: "stuck",
    134: "tangled",
    135: "motor_error",
}

FILAMENT_EMPTY = 0
FILAMENT_IDENTIFIED = 2

DRY_STATE_NAMES = {
    0: "free",
    1: "starting",
    2: "keeping",
    3: "stopping",
    4: "ptc_error",
    5: "ntc_error",
}


def crc16_kermit(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc & 0xFFFF


def pb_varint(value):
    out = bytearray()
    value = int(value)
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def pb_uint32(field, value):
    return pb_varint((field << 3) | 0) + pb_varint(int(value) & 0xFFFFFFFF)


def pb_bool(field, value):
    return pb_varint((field << 3) | 0) + pb_varint(1 if value else 0)


def _decode_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
    return result, pos


def pb_decode(data):
    fields = {}
    pos = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        field = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            value, pos = _decode_varint(data, pos)
        elif wire_type == 1:
            if pos + 8 > len(data):
                break
            value = struct.unpack_from("<d", data, pos)[0]
            pos += 8
        elif wire_type == 2:
            size, pos = _decode_varint(data, pos)
            if pos + size > len(data):
                break
            value = bytes(data[pos:pos + size])
            pos += size
        elif wire_type == 5:
            if pos + 4 > len(data):
                break
            value = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        else:
            break
        fields.setdefault(field, []).append((wire_type, value))
    return fields


def pb_first(fields, number, default=0):
    values = fields.get(number)
    if not values:
        return default
    return values[0][1]


def pb_first_str(fields, number, default=""):
    value = pb_first(fields, number, default)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore")
    return value


def build_packet(cmd, payload=b"", seq=1, flags=FLAG_REQUEST):
    payload = payload[:100]
    inner = bytearray([
        flags & 0xFF,
        seq & 0xFF,
        (seq >> 8) & 0xFF,
        cmd & 0xFF,
        len(payload) & 0xFF,
    ])
    inner.extend(payload)
    crc = crc16_kermit(bytes(inner))
    return PREAMBLE + bytes(inner) + bytes([crc & 0xFF, (crc >> 8) & 0xFF, END_MARKER])


def parse_stream(buffer):
    packets = []
    while True:
        if len(buffer) < 10:
            break
        start = buffer.find(PREAMBLE)
        if start < 0:
            del buffer[:-1]
            break
        if start > 0:
            del buffer[:start]
            continue
        payload_len = buffer[6]
        total = 2 + 5 + payload_len + 2 + 1
        if payload_len > 100:
            del buffer[:2]
            continue
        if len(buffer) < total:
            break
        if buffer[total - 1] != END_MARKER:
            del buffer[:2]
            continue
        inner = bytes(buffer[2:7 + payload_len])
        expected_crc = buffer[7 + payload_len] | (buffer[8 + payload_len] << 8)
        if expected_crc != crc16_kermit(inner):
            del buffer[:2]
            continue
        packets.append({
            "flags": buffer[2],
            "seq": buffer[3] | (buffer[4] << 8),
            "cmd": buffer[5],
            "is_resp": bool(buffer[2] & 0x80),
            "payload": bytes(buffer[7:7 + payload_len]),
        })
        del buffer[:total]
    return packets, buffer


def _slot_status_to_v1(slot_state, filament_state):
    if filament_state == FILAMENT_EMPTY:
        return "empty"
    if slot_state == SLOT_FEEDING:
        return "feeding"
    if slot_state == SLOT_PRELOADING:
        return "preload"
    if slot_state == SLOT_ROLLBACK:
        return "unwinding"
    if slot_state in (SLOT_ASSISTING, SLOT_ROLLBACK_ASSISTING):
        return "assisting"
    err = SLOT_ERROR_STATUS_BY_RAW.get(slot_state)
    if err is not None:
        return err
    if slot_state >= SLOT_FEED_ERROR:
        return "error"
    return "ready"


def _decode_status(payload):
    fields = pb_decode(payload)
    slots = []
    for _, slot_data in fields.get(9, []):
        slot = pb_decode(slot_data)
        slot_state = pb_first(slot, 1, 0)
        filament_state = pb_first(slot, 2, 0)
        slots.append({
            "status": _slot_status_to_v1(slot_state, filament_state),
            "rfid": 2 if filament_state == FILAMENT_IDENTIFIED else (
                0 if filament_state == FILAMENT_EMPTY else 1
            ),
        })
    result = {
        "status": "ready",
        "slots": slots,
        "temp": pb_first(fields, 3, 0),
        "humidity": pb_first(fields, 4, 0),
        "feed_assist_count": pb_first(fields, 7, 0),
    }
    if 2 in fields:
        dryer = pb_decode(fields[2][0][1])
        state = pb_first(dryer, 1, 0)
        result["dryer_status"] = {
            "status": DRY_STATE_NAMES.get(state, "free"),
            "target_temp": pb_first(dryer, 2, 0),
            "duration": pb_first(dryer, 3, 0),
            "remain_time": pb_first(dryer, 4, 0),
        }
    return {"result": result}


def _decode_info(payload):
    fields = pb_decode(payload)
    return {"result": {
        "model": "Anycubic ACE 2 Pro",
        "firmware": pb_first_str(fields, 1, ""),
        "version": pb_first_str(fields, 1, ""),
        "boot_version": pb_first_str(fields, 2, ""),
    }}


def _decode_generic(payload):
    fields = pb_decode(payload)
    code = pb_first(fields, 1, 0)
    msg = pb_first_str(fields, 2, "")
    if code and not msg:
        msg = "code %s" % (code,)
    return {"code": code, "msg": msg}


def _decode_temp(payload):
    fields = pb_decode(payload)

    def flt(number):
        values = fields.get(number)
        if not values:
            return 0.0
        return float(values[0][1])

    return {"result": {
        "box1_temp": flt(1),
        "box2_temp": flt(2),
        "ptc1_temp": flt(3),
        "ptc2_temp": flt(4),
        "env_temp": flt(5),
        "env_humidity": flt(6),
    }}


def _decode_filament_info(payload):
    fields = pb_decode(payload)
    colors = []
    primary_rgb = [0, 0, 0]
    for i, (_, color_data) in enumerate(fields.get(5, [])):
        color = pb_decode(color_data)
        rgba = pb_first(color, 1, 0) & 0xFFFFFFFF
        rgb = [(rgba >> 24) & 0xFF, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF]
        colors.append(rgb + [rgba & 0xFF])
        if i == 0:
            primary_rgb = rgb
    return {"result": {
        "index": pb_first(fields, 1, 0),
        "sku": pb_first_str(fields, 3, ""),
        "type": pb_first_str(fields, 4, ""),
        "brand": "",
        "color": primary_rgb,
        "colors": colors,
        "diameter": float(pb_first(fields, 8, 175)) / 100.0,
        "total": pb_first(fields, 9, 0),
        "remainder": pb_first(fields, 11, 0),
        "code": pb_first(fields, 12, 0),
        "rfid": 2,
    }}


def _decode_discover(payload):
    fields = pb_decode(payload)
    return {"result": {
        "uid1": pb_first(fields, 1, 0),
        "uid2": pb_first(fields, 2, 0),
        "uid3": pb_first(fields, 3, 0),
    }}


def encode_v1_request(request):
    method = request.get("method")
    params = request.get("params") or {}

    if method == "discover_device":
        return CMD_DISCOVER_DEVICE, b"", _decode_discover
    if method == "get_status":
        return CMD_GET_STATUS, b"", _decode_status
    if method == "get_info":
        return CMD_GET_INFO, b"", _decode_info
    if method == "get_temp":
        return CMD_GET_TEMP, b"", _decode_temp
    if method == "get_filament_info":
        return CMD_GET_FILAMENT_INFO, pb_uint32(1, params.get("index", 0)), _decode_filament_info
    if method == "feed_filament":
        payload = (
            pb_uint32(1, params.get("index", 0))
            + pb_uint32(2, params.get("speed", 50))
            + pb_uint32(3, params.get("length", 0))
            + pb_uint32(4, FEED_MODE_FEED)
        )
        return CMD_FEED_OR_ROLLBACK, payload, _decode_generic
    if method == "unwind_filament":
        payload = (
            pb_uint32(1, params.get("index", 0))
            + pb_uint32(2, params.get("speed", 25))
            + pb_uint32(3, params.get("length", 0))
            + pb_uint32(4, FEED_MODE_ROLLBACK)
        )
        return CMD_FEED_OR_ROLLBACK, payload, _decode_generic
    if method == "start_feed_assist":
        payload = (
            pb_uint32(1, params.get("index", 0))
            + pb_uint32(2, 10)
            + pb_uint32(3, 0)
            + pb_uint32(4, FEED_MODE_FEED_ASSIST)
        )
        return CMD_FEED_OR_ROLLBACK, payload, _decode_generic
    if method == "unwind_assist":
        payload = (
            pb_uint32(1, params.get("index", 0))
            + pb_uint32(2, 0)
            + pb_uint32(3, 0)
            + pb_uint32(4, FEED_MODE_UNWIND_ASSIST)
        )
        return CMD_FEED_OR_ROLLBACK, payload, _decode_generic
    if method in ("stop_feed_assist", "stop_feed_filament"):
        return CMD_STOP_FEED_OR_ROLLBACK, pb_uint32(1, params.get("index", 0)), _decode_generic
    if method == "update_speed":
        payload = pb_uint32(1, params.get("index", 0)) + pb_uint32(2, params.get("speed", 50))
        return CMD_UPDATE_SPEED, payload, _decode_generic
    if method == "drying":
        payload = (
            pb_uint32(1, params.get("temp", 0))
            + pb_uint32(2, params.get("duration", 0))
            + pb_bool(3, params.get("auto_roll", True))
        )
        return CMD_DRYING, payload, _decode_generic
    if method == "drying_stop":
        return CMD_DRYING, pb_uint32(1, 0) + pb_uint32(2, 0), _decode_generic
    if method == "set_feed_check":
        payload = (
            pb_uint32(1, params.get("check_len", 100))
            + pb_uint32(2, params.get("error_len", 90))
        )
        return CMD_SET_FEED_CHECK, payload, _decode_generic
    return None


