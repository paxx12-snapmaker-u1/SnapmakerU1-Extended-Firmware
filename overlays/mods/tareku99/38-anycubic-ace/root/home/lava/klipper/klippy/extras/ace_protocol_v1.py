import json
import logging
import os
import struct

from .ace_protocol import AceProtocol

V1_VENDOR_ID = '28e9'
V1_PRODUCT_ID = '018a'

FRAME_START = b'\xFF\xAA'
FRAME_END = 0xFE
MAX_PAYLOAD_LEN = 2048
MAX_REQUEST_LEN = 1024
HEADER_LEN = 4
TRAILER_LEN = 3
MIN_FRAME_LEN = HEADER_LEN + TRAILER_LEN
CRC_COLLISION = 0xAAFF

def calc_crc(buffer):
    crc = 0xFFFF
    for byte in buffer:
        data = byte
        data ^= crc & 0xFF
        data ^= (data & 0x0F) << 4
        crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
    return crc

class AceProtocolV1(AceProtocol):
    NAME = 'v1'
    DEFAULT_BAUD = 115200
    SERIAL_KWARGS = {
        'rtscts': True,
        'exclusive': True,
        'timeout': 0,
        'write_timeout': 0,
    }

    @classmethod
    def discover(cls):
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
            if vendor == V1_VENDOR_ID and product == V1_PRODUCT_ID:
                ace_devices.append(full_path)
        return ace_devices

    def encode_request(self, request, next_id=None):
        payload = json.dumps(request).encode('utf-8')
        if len(payload) > MAX_REQUEST_LEN:
            raise ValueError(
                'request payload too large: %d bytes' % len(payload))
        crc = calc_crc(payload)
        attempts = 0
        while crc == CRC_COLLISION and attempts < 10:
            if next_id is None:
                break
            request['id'] = next_id()
            payload = json.dumps(request).encode('utf-8')
            crc = calc_crc(payload)
            attempts += 1
        data = bytes([FRAME_START[0], FRAME_START[1]])
        data += struct.pack('<H', len(payload))
        data += payload
        data += struct.pack('<H', crc)
        data += bytes([FRAME_END])
        return data

    def decode_frames(self, buffer):
        results = []
        while len(buffer) >= MIN_FRAME_LEN:
            start = buffer.find(FRAME_START)
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
            payload_len = struct.unpack('<H', bytes(buffer[2:4]))[0]
            if payload_len > MAX_PAYLOAD_LEN:
                del buffer[:2]
                continue
            total_len = HEADER_LEN + payload_len + TRAILER_LEN
            if len(buffer) < total_len:
                break
            payload = bytes(buffer[HEADER_LEN:HEADER_LEN + payload_len])
            del buffer[:total_len]
            try:
                ret = json.loads(payload.decode('utf-8'))
            except (json.decoder.JSONDecodeError, UnicodeDecodeError):
                logging.info('[multiACE] V1 invalid JSON/UTF-8 frame dropped')
                continue
            results.append(ret)
        return results

    def initial_handshake_requests(self):
        return [{'method': 'get_info'}]

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
                {'index': i, 'status': 'empty1', 'sku': '', 'type': '',
                 'rfid': 0, 'brand': '', 'color': [0, 0, 0]}
                for i in range(4)
            ],
        }
