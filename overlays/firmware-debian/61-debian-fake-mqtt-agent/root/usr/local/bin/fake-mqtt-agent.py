#!/usr/bin/env python3

import sys
import json
import signal
import time
import re

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

LATEST_FW = {
    "authDevices": "[\"8110025060100014JGVC\"]",
    "createDate": "2025-12-31T02:26:31",
    "id": 5,
    "lastModifiedBy": "software@snapmaker.com",
    "modifiedDate": "2025-12-31T02:26:31",
    "name": "U1_1.0.0.158_20251230140122_upgrade.bin",
    "note": "https://public.resource.snapmaker.com/firmware/U1/U1_1.0.0.158_20251230140122_upgrade_firmware_desc.json",
    "status": 200,
    "url": "https://public.resource.snapmaker.com/firmware/U1/U1_1.0.0.158_20251230140122_upgrade.bin",
    "version": "1.0.0.158"
}

DEVICE_NAME_FILE = "/oem/printer_data/.device_name"

client = None

class MqttAgentError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)

def log(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def publish_response(topic_suffix, payload):
    topic = f"mqtt_agent/response/{topic_suffix}"
    client.publish(topic, json.dumps(payload))
    log(f"Response to {topic}: {payload}")

def publish_notification(payload):
    topic = "mqtt_agent/notification"
    client.publish(topic, json.dumps(payload))
    log(f"Notification: {payload}")

def set_device_name(name):
    if not name:
        raise MqttAgentError(-1, "Device name is required")
    sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
    if not sanitized_name:
        raise MqttAgentError(-1, "Invalid device name")
    try:
        with open(DEVICE_NAME_FILE, "w") as f:
            f.write(sanitized_name)
        log(f"Updated device name to: {sanitized_name}")
    except Exception as e:
        log(f"Failed to write device name: {e}")
        raise MqttAgentError(-1, f"Failed to update device name: {str(e)}")

def handle_update_device_status(params):
    if "name" in params:
        name = params["name"].strip()
        set_device_name(name)
    return {"state": "success"}

def handle_set_link_mode(params):
    mode = params.get("mode", "").lower()
    if mode == "wan":
        raise MqttAgentError(-1, "WAN mode not supported, only LAN mode is available")
    return {"state": "success"}

def handle_get_fw_latest(params):
    return {
        "data": LATEST_FW,
        "state": "success"
    }

METHODS = {
    "mqtt_agent.update_device_status": handle_update_device_status,
    "mqtt_agent.set_link_mode": handle_set_link_mode,
    "mqtt_agent.get_fw_latest": handle_get_fw_latest,
}

def on_connect(c, userdata, flags, rc):
    if rc == 0:
        log("Connected to MQTT broker")
        c.subscribe("mqtt_agent/request/#")
        log("Subscribed to mqtt_agent/request/#")
    else:
        log(f"Connection failed: {rc}")

def on_message(c, userdata, msg):
    request_id = None
    channel = None
    try:
        payload = msg.payload.decode('utf-8')
        log(f"Request on {msg.topic}: {payload}")
        req = json.loads(payload)

        request_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        channel = msg.topic.split('/')[-1] if '/' in msg.topic else 'unknown'

        if method not in METHODS:
            raise MqttAgentError(-32601, f"Method not supported: {method}")

        result = METHODS[method](params)
        publish_response(channel, {
            "id": request_id,
            "jsonrpc": "2.0",
            "result": result
        })
    except MqttAgentError as e:
        if channel:
            publish_response(channel, {
                "error": {"code": e.code, "message": e.message},
                "id": request_id,
                "jsonrpc": "2.0"
            })
    except json.JSONDecodeError as e:
        log(f"Invalid JSON: {e}")
    except Exception as e:
        log(f"Error handling message: {e}")
        if channel:
            publish_response(channel, {
                "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
                "id": request_id,
                "jsonrpc": "2.0"
            })

def main():
    global client

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    def shutdown(signum, frame):
        log("Shutting down...")
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log("Connecting to localhost:1883")
    client.connect("localhost", 1883, 60)

    log("Starting MQTT loop")
    client.loop_forever()

if __name__ == '__main__':
    main()
