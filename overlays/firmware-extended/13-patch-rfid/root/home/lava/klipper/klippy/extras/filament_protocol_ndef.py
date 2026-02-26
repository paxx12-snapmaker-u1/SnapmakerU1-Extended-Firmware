import copy
import io
import json
import logging
from . import filament_protocol

NDEF_OK = 0
NDEF_ERR = -1
NDEF_PARAMETER_ERR = -2
NDEF_NOT_FOUND_ERR = -3

def xxd_dump(data, max_lines=16):
    if isinstance(data, list):
        data = bytes(data)
    if not isinstance(data, (bytes, bytearray)):
        return ""

    lines = []
    for i in range(0, min(len(data), max_lines * 16), 16):
        hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
        lines.append(f'{i:08x}: {hex_part:<48}  {ascii_part}')

    if len(data) > max_lines * 16:
        lines.append(f'... ({len(data)} bytes total)')

    return '\n'.join(lines)

def ndef_parse(data_buf):
    if None == data_buf or isinstance(data_buf, (list, bytes, bytearray)) == False:
        return NDEF_PARAMETER_ERR, [], []

    try:
        data = bytes(data_buf) if isinstance(data_buf, list) else data_buf

        card_uid = []
        if len(data) >= 8:
            card_uid = [data[0], data[1], data[2], data[4], data[5], data[6], data[7]]

        logging.info("NDEF RFID data:")
        logging.info("\n" + xxd_dump(data))

        data_io = io.BytesIO(data)

        start_offset = 0
        if len(data) > 12 and data[0] != 0xE1:
            for i in range(min(16, len(data) - 4)):
                if data[i] == 0xE1 and (data[i+1] == 0x10 or data[i+1] == 0x11 or data[i+1] == 0x40):
                    start_offset = i
                    break

        if start_offset > 0:
            data_io.seek(start_offset)

        cc = data_io.read(4)
        if len(cc) < 4 or cc[0] != 0xE1:
            return NDEF_PARAMETER_ERR, []

        records = []

        while True:
            base_tlv = data_io.read(2)
            if len(base_tlv) < 2:
                break

            tag = base_tlv[0]
            if tag == 0xFE:
                break

            tlv_len = base_tlv[1]
            if tlv_len == 0xFF:
                ext_len = data_io.read(2)
                if len(ext_len) < 2:
                    break
                tlv_len = (ext_len[0] << 8) | ext_len[1]

            if tag == 0x03:
                ndef_data = data_io.read(tlv_len)
                ndef_offset = 0

                while ndef_offset < len(ndef_data) - 2:
                    header = ndef_data[ndef_offset]
                    ndef_offset += 1

                    tnf = header & 0x07
                    sr_flag = (header >> 4) & 0x01
                    il_flag = (header >> 3) & 0x01

                    type_len = ndef_data[ndef_offset]
                    ndef_offset += 1

                    if sr_flag:
                        payload_len = ndef_data[ndef_offset]
                        ndef_offset += 1
                    else:
                        if ndef_offset + 4 > len(ndef_data):
                            break
                        payload_len = (ndef_data[ndef_offset] << 24) | (ndef_data[ndef_offset + 1] << 16) | (ndef_data[ndef_offset + 2] << 8) | ndef_data[ndef_offset + 3]
                        ndef_offset += 4

                    id_len = 0
                    if il_flag:
                        id_len = ndef_data[ndef_offset]
                        ndef_offset += 1

                    if ndef_offset + type_len + id_len + payload_len > len(ndef_data):
                        break

                    mime_type = ndef_data[ndef_offset:ndef_offset + type_len].decode('ascii', errors='ignore')
                    ndef_offset += type_len

                    if id_len > 0:
                        ndef_offset += id_len

                    payload = bytes(ndef_data[ndef_offset:ndef_offset + payload_len])
                    ndef_offset += payload_len

                    if tnf == 0x02:
                        records.append({'mime_type': mime_type, 'payload': payload})
                        logging.info(f"NDEF record found: mime_type='{mime_type}', payload_len={len(payload)}")
            else:
                data_io.seek(tlv_len, 1)

        if not records:
            return NDEF_NOT_FOUND_ERR, [], card_uid

        return NDEF_OK, records, card_uid

    except Exception as e:
        logging.exception("NDEF parsing failed: %s", str(e))
        return NDEF_ERR, [], []

def parse_color_hex(value):
    try:
        hex_str = str(value)
        if hex_str.startswith('#'):
            hex_str = hex_str[1:]
        return int(hex_str, 16)
    except (ValueError, TypeError):
        return 0xFFFFFF

def openspool_parse_payload(payload, card_uid=[]):
    if None == payload or not isinstance(payload, (bytes, bytearray)):
        logging.error("OpenSpool payload parsing failed: Invalid payload parameter")
        return filament_protocol.FILAMENT_PROTO_PARAMETER_ERR, None

    try:
        payload_str = payload.decode('utf-8')
        logging.info(f"OpenSpool JSON payload: {payload_str}")

        data = json.loads(payload_str)

        if not isinstance(data, dict):
            logging.error(f"OpenSpool payload parsing failed: JSON data is not a dict, got {type(data)}")
            return filament_protocol.FILAMENT_PROTO_ERR, None

        if data.get('protocol') != 'openspool':
            logging.error(f"OpenSpool payload parsing failed: Invalid protocol '{data.get('protocol')}', expected 'openspool'")
            return filament_protocol.FILAMENT_PROTO_ERR, None

        info = copy.copy(filament_protocol.FILAMENT_INFO_STRUCT)
        info['VERSION'] = 1
        info['VENDOR'] = data.get('brand', 'Generic')
        info['MANUFACTURER'] = data.get('brand', 'Generic')

        info['MAIN_TYPE'] = data.get('type', 'PLA').upper()
        info['SUB_TYPE'] = data.get('subtype', 'Basic')
        info['TRAY'] = 0

        info['COLOR_NUMS'] = 1
        info['RGB_1'] = parse_color_hex(data.get('color_hex', 'FFFFFF'))

        additional_color_hexes = list(data.get('additional_color_hexes') or [])
        for hex_color in additional_color_hexes[:5]:
            idx = info['COLOR_NUMS'] + 1
            info['COLOR_NUMS'] = idx
            info[f'RGB_{idx}'] = parse_color_hex(hex_color)

        for i in range(info['COLOR_NUMS'] + 1, 6):
            info[f'RGB_{i}'] = 0

        try:
            info['ALPHA'] = max(0x00, min(0xFF, int(data.get('alpha'))))
        except (ValueError, TypeError):
            info['ALPHA'] = 0xFF

        info['ARGB_COLOR'] = info['ALPHA'] << 24 | info['RGB_1']

        try:
            info['DIAMETER'] = int(float(data.get('diameter', 1.75)) * 100)
        except (ValueError, TypeError):
            info['DIAMETER'] = 175
        try:
            info['WEIGHT'] = int(data.get('weight', 0))
        except (ValueError, TypeError):
            info['WEIGHT'] = 0
        info['LENGTH'] = 0
        info['DRYING_TEMP'] = 0
        info['DRYING_TIME'] = 0

        try:
            min_temp = int(data.get('min_temp', 0))
            max_temp = int(data.get('max_temp', 0))
            info['HOTEND_MIN_TEMP'] = min_temp
            info['HOTEND_MAX_TEMP'] = max_temp
        except (ValueError, TypeError):
            info['HOTEND_MIN_TEMP'] = 0
            info['HOTEND_MAX_TEMP'] = 0

        try:
            bed_min_temp = int(data.get('bed_min_temp', 0))
            bed_max_temp = int(data.get('bed_max_temp', 0))
            info['BED_TEMP'] = bed_min_temp if bed_min_temp > 0 else bed_max_temp
        except (ValueError, TypeError):
            info['BED_TEMP'] = 0

        info['BED_TYPE'] = 0
        info['FIRST_LAYER_TEMP'] = info['HOTEND_MIN_TEMP']
        info['OTHER_LAYER_TEMP'] = info['HOTEND_MIN_TEMP']

        info['SKU'] = 0
        info['MF_DATE'] = '19700101'
        info['RSA_KEY_VERSION'] = 0
        info['OFFICIAL'] = True
        info['CARD_UID'] = card_uid

        try:
            info['SPOOL_ID'] = int(data.get('spool_id', 0))
        except (ValueError, TypeError):
            info['SPOOL_ID'] = 0

        return filament_protocol.FILAMENT_PROTO_OK, info

    except json.JSONDecodeError as e:
        logging.exception("OpenSpool payload parsing failed: Invalid JSON: %s", str(e))
        return filament_protocol.FILAMENT_PROTO_ERR, None
    except Exception as e:
        logging.exception("OpenSpool payload parsing failed: %s", str(e))
        return filament_protocol.FILAMENT_PROTO_ERR, None

def ndef_proto_data_parse(data_buf):
    error, records, card_uid = ndef_parse(data_buf)

    if error != NDEF_OK:
        logging.error(f"NDEF parse failed: NDEF parsing error (code: {error})")
        return filament_protocol.FILAMENT_PROTO_ERR, None

    if not records:
        logging.error("NDEF parse failed: No records found")
        return filament_protocol.FILAMENT_PROTO_ERR, None

    for record in records:
        mime_type = record['mime_type']
        payload = record['payload']

        if mime_type == 'application/json':
            logging.info(f"Detected OpenSpool format, parsing payload ({len(payload)} bytes)")
            error_code, info = openspool_parse_payload(payload, card_uid)
            if error_code != filament_protocol.FILAMENT_PROTO_OK:
                logging.error(f"OpenSpool parse failed: Payload parsing error (code: {error_code})")
                continue
            else:
                logging.info(f"OpenSpool parse success: vendor={info.get('VENDOR')}, type={info.get('MAIN_TYPE')}")
                return error_code, info

        else:
            logging.warning(f"Skipping unsupported MIME type '{mime_type}'")

    logging.error("NDEF parse failed: No supported records found")
    return filament_protocol.FILAMENT_PROTO_SIGN_CHECK_ERR, None

if __name__ == '__main__':
    import sys
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(description='Parse NDEF data from file')
    parser.add_argument('file', help='File containing NDEF data')
    args = parser.parse_args()

    try:
        with open(args.file, 'rb') as f:
            data = f.read()

        error_code, info = ndef_proto_data_parse(data)

        if error_code == filament_protocol.FILAMENT_PROTO_OK:
            print(info)
        else:
            print(f"Error: {error_code}")
            sys.exit(1)

    except FileNotFoundError:
        print(f"Error: File '{args.file}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
