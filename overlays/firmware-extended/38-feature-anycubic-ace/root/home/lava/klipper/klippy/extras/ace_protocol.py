# ACE Pro JSON serial protocol helpers.
# Stateless: no serial, no threading, no Klipper dependencies.
import json
import struct

PREAMBLE = b"\xff\xaa"
END_MARKER = 0xFE
MAX_PAYLOAD = 1024


def calc_crc(data):
    """CRC-CCITT over raw bytes (ACE Pro frame integrity check)."""
    crc = 0xFFFF
    for byte in data:
        b_crc = byte
        b_crc ^= crc & 0xFF
        b_crc ^= (b_crc & 0x0F) << 4
        crc = ((b_crc << 8) | (crc >> 8)) ^ (b_crc >> 4) ^ (b_crc << 3)
    return crc & 0xFFFF


def encode_json_frame(payload_bytes):
    """Build an ACE Pro JSON frame.

    Returns (frame_bytes, crc).  Caller must retry with a new id if
    crc == 0xAAFF (collision with the PREAMBLE bytes in little-endian).
    Raises ValueError if payload_bytes > MAX_PAYLOAD.
    """
    if len(payload_bytes) > MAX_PAYLOAD:
        raise ValueError("ACE payload too large (%d > %d)" % (
            len(payload_bytes), MAX_PAYLOAD))
    crc = calc_crc(payload_bytes)
    data = PREAMBLE
    data += struct.pack("<H", len(payload_bytes))
    data += payload_bytes
    data += struct.pack("<H", crc)
    data += bytes([END_MARKER])
    return data, crc


def parse_json_frames(buf):
    """Generator: yield complete JSON response dicts from a bytearray buffer.

    Consumed bytes are removed from *buf* in place so the caller can
    keep appending new data and calling this in a loop.
    """
    while len(buf) >= 7:
        start = buf.find(PREAMBLE)
        if start < 0:
            if buf and buf[-1] == 0xFF:
                del buf[:-1]
            else:
                buf.clear()
            break
        if start > 0:
            del buf[:start]
        if len(buf) < 4:
            break
        payload_len = struct.unpack("<H", buf[2:4])[0]
        if payload_len > 2048:
            del buf[:2]
            continue
        total_len = 4 + payload_len + 2 + 1
        if len(buf) < total_len:
            break
        payload = bytes(buf[4:4 + payload_len])
        del buf[:total_len]
        try:
            yield json.loads(payload.decode("utf-8"))
        except Exception:
            continue
