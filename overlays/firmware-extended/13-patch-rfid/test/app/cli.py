# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

import os
import sys
import argparse
import logging
import json

from . import filament_protocol
from . import filament_protocol_ndef

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(description='Parse NDEF data or JSON payload from file')
    parser.add_argument('file', help='File containing NDEF data or OpenSpool JSON payload')
    args = parser.parse_args()

    try:
        with open(args.file, 'rb') as f:
            data = f.read()

        if args.file.endswith('.json'):
            try:
                json.loads(data.decode('utf-8'))
                logging.info("Detected JSON file, parsing as OpenSpool payload")
                error_code, info = filament_protocol_ndef.openspool_parse_payload(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                logging.info("JSON parsing failed, treating as binary NDEF data")
                error_code, info = filament_protocol_ndef.ndef_proto_data_parse(data)
        else:
            error_code, info = filament_protocol_ndef.ndef_proto_data_parse(data)

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
