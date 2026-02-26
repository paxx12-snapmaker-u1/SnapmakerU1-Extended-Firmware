import os
import sys
import logging
from unittest.mock import MagicMock

logging.basicConfig(level=logging.INFO)

# Set path so python finds the moonraker package
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, os.path.join(ROOT_DIR, "tmp/firmware/rootfs/home/lava/moonraker"))

# Mock missing Moonraker dependencies from pip (tornado)
sys.modules['tornado'] = MagicMock()
sys.modules['tornado.websocket'] = MagicMock()

import moonraker.components.spoolman as spoolman_comp

class MockServer:
    def __init__(self):
        self.config = MockConfig()
    
    def get_event_loop(self):
        loop = MagicMock()
        loop.get_loop_time.return_value = 0.0
        def run_sync(coro):
            import asyncio
            asyncio.run(coro)
        loop.create_task.side_effect = run_sync
        return loop

    def get_host_info(self):
        return {"machine_id": "test_id"}

    def register_endpoint(self, *args, **kwargs): pass
    def register_event_handler(self, *args, **kwargs): pass
    def register_notification(self, *args, **kwargs): pass
    def register_remote_method(self, *args, **kwargs): pass
    def lookup_component(self, name): return MagicMock()
    def send_event(self, event, payload): pass

class MockConfig:
    def __init__(self):
        self._name = "spoolman"
    
    def get(self, key, default=None):
        if key == "server":
            return "http://localhost:7912"
        if key == "sync_rate":
            return 5
        return default
    
    def getint(self, key, default=None, minval=None):
        return default

    def get_server(self):
        return MockServer()
    
    def get_name(self):
        return self._name

    def error(self, msg):
        return Exception(msg)

# Instantiate the mocked Spoolman component
mock_spoolman = spoolman_comp.SpoolManager(MockConfig())

# Reset properties for testing that might be overwritten during init
mock_spoolman.server = MockServer()
mock_spoolman.klippy_apis = MagicMock()
mock_spoolman.database = MagicMock()
mock_spoolman.spool_history.tracker.history = MagicMock()
mock_spoolman.spool_history.tracker.history.tracking_enabled.return_value = True

# Now simulate an update
mock_status_payload = {
    "filament_detect": {
        "info": [
            {
                "SPOOL_ID": 1337,
                "VENDOR": "OpenSpool",
                "MAIN_TYPE": 1
            }
        ]
    }
}

print("--- Testing Moonraker Spoolman Component ---")
print(f"Simulating Klipper Payload: {mock_status_payload}")

# Overwrite _active_spool_id so we can assert on it later
# Note: set_active_spool uses self.spool_id 
print(f"Initial Spool ID: {mock_spoolman.spool_id}")

mock_spoolman._handle_status_update(mock_status_payload, 0.0)

print(f"Final Spool ID: {mock_spoolman.spool_id}")

if mock_spoolman.spool_id == 1337:
    print("SUCCESS: Moonraker correctly received the payload and updated the active spool!")
else:
    print("FAILED: Moonraker did not set the active spool id.")
    sys.exit(1)

print(r"\--- Testing Inactive Tool Insertion ---")
mock_status_payload_2 = {
    "filament_detect": {
        "info": [
            {
                "SPOOL_ID": 1337,
                "VENDOR": "OpenSpool",
                "MAIN_TYPE": 1
            },
            {
                "SPOOL_ID": 9,
                "VENDOR": "OpenSpool",
                "MAIN_TYPE": 1
            }
        ]
    }
}

mock_spoolman._handle_status_update(mock_status_payload_2, 0.0)
print(f"Final Spool ID after inserting ID 9 into Tool 1: {mock_spoolman.spool_id}")

if mock_spoolman.spool_id == 1337:
    print("SUCCESS: Inactive tool spool insertion successfully ignored by active spool handler!")
else:
    print("FAILED: Active spool ID was overridden by inactive tool insertion.")
    sys.exit(1)
