import os
import sys
import json

# Add klippy extras to path so we can import the modules
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.append(os.path.join(ROOT_DIR, "tmp/firmware/rootfs/home/lava/klipper"))

import klippy.extras.filament_protocol as filament_protocol
import klippy.extras.filament_protocol_ndef as filament_protocol_ndef

# Create a mock NDEF byte array containing an OpenSpool JSON payload
# that includes a spool_id.
mock_json_payload = json.dumps({
    "protocol": "openspool",
    "spool_id": 420,
    "material": "PETG",
    "color": "Blue"
})

class MockRecord:
    def __init__(self, data):
        self.data_msg = data

# m1_proto_data_parse_ndef expects a list of NDEF records and the card UID
records = [MockRecord(mock_json_payload)]
card_uid = b'\\x01\\x02\\x03\\x04'

print("--- Testing OpenSpool NDEF parsing ---")
print(f"Input JSON Payload: {mock_json_payload}")

# Call the function
status, info = filament_protocol_ndef.openspool_parse_payload(mock_json_payload.encode('utf-8'), card_uid)

print(f"Status returned: {status} (Expected: 0 / FILAMENT_PROTO_OK)")
print(f"Extracted SPOOL_ID: {info.get('SPOOL_ID')}")

if status == filament_protocol.FILAMENT_PROTO_OK and info.get('SPOOL_ID') == 420:
    print("SUCCESS: The parser successfully extracted the SPOOL_ID from the NDEF payload!")
else:
    print("FAILED: The parser did not extract the SPOOL_ID correctly.")
    sys.exit(1)
