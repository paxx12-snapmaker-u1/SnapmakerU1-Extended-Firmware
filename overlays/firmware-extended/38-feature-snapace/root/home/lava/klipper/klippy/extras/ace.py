import json
import logging
import queue
import struct
import traceback

import serial


GATE_UNKNOWN = -1
GATE_EMPTY = 0
GATE_AVAILABLE = 1


class AceException(Exception):
    pass


class BunnyAce:
    VARS_ACE_REVISION = "ace__revision"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        self.serial_id = config.get("serial", "/dev/ttyACM0")
        self.baud = config.getint("baud", 115200)
        self.feed_speed = config.getint("feed_speed", 50, minval=1)
        self.retract_speed = config.getint("retract_speed", 50, minval=1)
        self.retract_length = config.getint("retract_length", 100, minval=1)
        self.feed_length = config.getint("feed_length", 100, minval=1)
        self.max_dryer_temperature = config.getint(
            "max_dryer_temperature", 55, minval=1
        )

        self._connected = False
        self._serial = None
        self._queue = queue.Queue()
        self._request_id = 0
        self._callback_map = {}
        self._feed_assist_index = -1
        self._connect_timer = None
        self._heartbeat_timer = None
        self._ace_dev_fd = None
        self.read_buffer = bytearray()
        self.gate_status = [GATE_UNKNOWN] * 4
        self._info = {
            "status": "disconnected",
            "dryer_status": {
                "status": "stop",
                "target_temp": 0,
                "duration": 0,
                "remain_time": 0,
            },
            "temp": 0,
            "slots": [
                {
                    "index": i,
                    "status": "empty",
                    "sku": "",
                    "type": "",
                    "rfid": 0,
                    "brand": "",
                    "color": [0, 0, 0],
                }
                for i in range(4)
            ],
        }

        self.save_variables = self.printer.lookup_object("save_variables", None)
        if self.save_variables is None:
            raise config.error("SnapAce requires a [save_variables] section")
        if self.VARS_ACE_REVISION not in self.save_variables.allVariables:
            self.save_variables.allVariables[self.VARS_ACE_REVISION] = 0

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:disconnect", self._handle_disconnect)

        self.gcode.register_command(
            "ACE_START_DRYING",
            self.cmd_ACE_START_DRYING,
            desc="Starts ACE Pro dryer",
        )
        self.gcode.register_command(
            "ACE_STOP_DRYING",
            self.cmd_ACE_STOP_DRYING,
            desc="Stops ACE Pro dryer",
        )
        self.gcode.register_command(
            "ACE_ENABLE_FEED_ASSIST",
            self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc="Enables ACE feed assist",
        )
        self.gcode.register_command(
            "ACE_DISABLE_FEED_ASSIST",
            self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc="Disables ACE feed assist",
        )
        self.gcode.register_command(
            "ACE_FEED",
            self.cmd_ACE_FEED,
            desc="Feeds filament from ACE",
        )
        self.gcode.register_command(
            "ACE_RETRACT",
            self.cmd_ACE_RETRACT,
            desc="Retracts filament back to ACE",
        )

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object("toolhead")
        logging.info("ACE: connecting to %s", self.serial_id)
        self._connect_timer = self.reactor.register_timer(
            self._connect, self.reactor.NOW
        )

    def _handle_disconnect(self):
        logging.info("ACE: closing connection to %s", self.serial_id)
        self._serial_disconnect()
        self._queue = None

    def log_always(self, msg, color=False):
        self.gcode.respond_raw(msg)

    def log_error(self, msg):
        logging.error(msg)
        self.gcode.respond_raw("!! %s" % (msg,))

    def _get_next_request_id(self):
        self._request_id += 1
        if self._request_id >= 300000:
            self._request_id = 0
        return self._request_id

    def _calc_crc(self, buffer):
        crc = 0xFFFF
        for byte in buffer:
            data = byte
            data ^= crc & 0xFF
            data ^= (data & 0x0F) << 4
            crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
        return crc

    def _connect(self, eventtime):
        try:
            self._serial = serial.Serial(
                port=self.serial_id,
                baudrate=self.baud,
                exclusive=True,
                rtscts=True,
                timeout=0,
                write_timeout=0,
            )
            self._connected = True
            self._info["status"] = "ready"
            self._request_id = 0
            self._ace_dev_fd = self.reactor.register_fd(
                self._serial.fileno(), self._reader_cb
            )
            self._heartbeat_timer = self.reactor.register_timer(
                self._periodic_heartbeat_event, self.reactor.NOW
            )
            self.send_request({"method": "get_info"}, self._handle_info_response)
            if self._feed_assist_index != -1:
                self._enable_feed_assist(self._feed_assist_index)
            logging.info("ACE: connected to %s", self.serial_id)
            return self.reactor.NEVER
        except serial.serialutil.SerialException as e:
            self._serial = None
            self._connected = False
            self._info["status"] = "disconnected"
            logging.info("ACE: connection error: %s", e)
            return eventtime + 5.0
        except Exception:
            self._serial = None
            self._connected = False
            self._info["status"] = "error"
            logging.info("ACE: connection error: %s", traceback.format_exc())
            return eventtime + 5.0

    def _serial_disconnect(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self._connected = False
        self._info["status"] = "disconnected"
        if self._heartbeat_timer is not None:
            self.reactor.unregister_timer(self._heartbeat_timer)
            self._heartbeat_timer = None
        if self._ace_dev_fd is not None:
            self.reactor.set_fd_wake(self._ace_dev_fd, False, False)
            self._ace_dev_fd = None

    def _send_request(self, request):
        if not self._connected or self._serial is None:
            raise AceException("ACE Pro is not connected")
        if "id" not in request:
            request["id"] = self._get_next_request_id()
        payload = json.dumps(request).encode("utf-8")
        if len(payload) > 1024:
            raise AceException("ACE payload too large")
        crc = self._calc_crc(payload)
        attempts = 0
        while crc == 0xAAFF and attempts < 10:
            request["id"] = self._get_next_request_id()
            payload = json.dumps(request).encode("utf-8")
            crc = self._calc_crc(payload)
            attempts += 1
        data = b"\xff\xaa"
        data += struct.pack("<H", len(payload))
        data += payload
        data += struct.pack("<H", crc)
        data += b"\xfe"
        try:
            self._serial.write(data)
        except Exception:
            self._serial_disconnect()
            raise AceException("ACE serial write failed")

    def send_request(self, request, callback):
        msg_id = self._get_next_request_id()
        request["id"] = msg_id
        self._callback_map[msg_id] = callback
        self._info["status"] = "busy"
        self._send_request(request)

    def _reader_cb(self, eventtime):
        try:
            if self._serial.in_waiting:
                self._process_data(self._serial.read(size=self._serial.in_waiting))
        except Exception:
            logging.info("ACE: read/process error: %s", traceback.format_exc())
            self._serial_disconnect()
            self._connect_timer = self.reactor.register_timer(
                self._connect, self.reactor.monotonic() + 1.0
            )

    def _process_data(self, raw_bytes):
        self.read_buffer += raw_bytes
        while len(self.read_buffer) >= 7:
            start = self.read_buffer.find(b"\xff\xaa")
            if start < 0:
                self.read_buffer = (
                    self.read_buffer[-1:]
                    if self.read_buffer.endswith(b"\xff")
                    else bytearray()
                )
                break
            if start > 0:
                self.read_buffer = self.read_buffer[start:]
            if len(self.read_buffer) < 4:
                break
            payload_len = struct.unpack("<H", self.read_buffer[2:4])[0]
            if payload_len > 2048:
                self.read_buffer = self.read_buffer[2:]
                continue
            total_len = 4 + payload_len + 2 + 1
            if len(self.read_buffer) < total_len:
                break
            packet = self.read_buffer[:total_len]
            payload = packet[4:4 + payload_len]
            self.read_buffer = self.read_buffer[total_len:]
            try:
                response = json.loads(payload.decode("utf-8"))
            except Exception:
                logging.info("ACE: invalid JSON response")
                continue
            msg_id = response.get("id")
            callback = self._callback_map.pop(msg_id, None)
            if callback is not None:
                callback(response)
            if not self._callback_map:
                self._info["status"] = "ready"

    def _handle_info_response(self, response):
        if response.get("code", 0) != 0:
            self.log_error("ACE Error: %s" % (response.get("msg"),))
            return
        result = response.get("result", {})
        self.log_always(
            "ACE: Connected to %s\n Firmware Version: %s"
            % (result.get("model", "Unknown"), result.get("firmware", "Unknown"))
        )

    def _periodic_heartbeat_event(self, eventtime):
        def callback(response):
            if response is None or response.get("code", 0) != 0:
                return
            result = response.get("result", {})
            slots = result.get("slots", [])
            for i in range(min(4, len(slots))):
                slot = slots[i]
                if self.gate_status[i] == GATE_EMPTY and slot.get("status") != "empty":
                    self.reactor.register_async_callback(
                        lambda et, gate=i: self._pre_load(gate)
                    )
                if (
                    slot.get("rfid") == 2
                    and self._info.get("slots", [{}] * 4)[i].get("rfid") != 2
                ):
                    self._sync_slot_to_print_task_config(i, slot)
                self.gate_status[i] = (
                    GATE_EMPTY if slot.get("status") == "empty" else GATE_AVAILABLE
                )
            self._info.update(result)

        try:
            self.send_request({"method": "get_status"}, callback)
        except AceException:
            pass
        return eventtime + 1.0

    def _sync_slot_to_print_task_config(self, index, slot):
        color = slot.get("color") or [0, 0, 0]
        try:
            rgb = "%02X%02X%02X" % (int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            rgb = "000000"
        self.gcode.run_script_from_command(
            'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=%d FILAMENT_TYPE="%s" '
            'FILAMENT_COLOR_RGBA=%s VENDOR="%s" FILAMENT_SUBTYPE=""'
            % (
                index,
                slot.get("type", "PLA"),
                rgb,
                slot.get("brand", "Generic"),
            )
        )

    def _pre_load(self, gate):
        self._feed(gate, self.feed_length, self.feed_speed, 0)
        self.log_always("Select AutoLoad from the menu")

    def wait_ace_ready(self):
        if not self._connected:
            raise AceException("ACE Pro is not connected")
        deadline = self.reactor.monotonic() + 30.0
        while self._info.get("status") != "ready":
            if self.reactor.monotonic() >= deadline:
                raise AceException("Timed out waiting for ACE Pro")
            self.reactor.pause(self.reactor.monotonic() + 0.5)

    def is_ace_ready(self):
        return self._info.get("status") == "ready"

    def dwell(self, delay=1.0):
        self.reactor.pause(self.reactor.monotonic() + delay)

    def _command_callback(self, success_message):
        def callback(response):
            if response.get("code", 0) != 0:
                self.log_error("ACE Error: %s" % (response.get("msg"),))
                return
            if success_message:
                self.gcode.respond_info(success_message)
        return callback

    def cmd_ACE_START_DRYING(self, gcmd):
        try:
            temperature = gcmd.get_int("TEMP", 55, minval=1)
            duration = gcmd.get_int("DURATION", 240, minval=1)
            if temperature > self.max_dryer_temperature:
                raise gcmd.error("Wrong temperature")
            self.wait_ace_ready()
            self.send_request(
                {
                    "method": "drying",
                    "params": {
                        "temp": temperature,
                        "fan_speed": 7000,
                        "duration": duration,
                    },
                },
                self._command_callback("Started ACE drying"),
            )
        except AceException as e:
            raise gcmd.error(str(e))

    def cmd_ACE_STOP_DRYING(self, gcmd):
        try:
            self.wait_ace_ready()
            self.send_request(
                {"method": "drying_stop"},
                self._command_callback("Stopped ACE drying"),
            )
        except AceException as e:
            raise gcmd.error(str(e))

    def _enable_feed_assist(self, index):
        self.wait_ace_ready()
        self._retract(index, 5, 10)
        self.wait_ace_ready()
        self.send_request(
            {"method": "start_feed_assist", "params": {"index": index}},
            self._command_callback(None),
        )
        self._feed_assist_index = index
        self.dwell(0.7)

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        try:
            index = gcmd.get_int("INDEX", minval=0, maxval=3)
            self._enable_feed_assist(index)
        except AceException as e:
            raise gcmd.error(str(e))

    def _disable_feed_assist(self, index=-1):
        if index < 0:
            index = self._feed_assist_index
        if index < 0:
            return
        self.wait_ace_ready()
        self.send_request(
            {"method": "stop_feed_assist", "params": {"index": index}},
            self._command_callback("Disabled ACE feed assist"),
        )
        self._feed_assist_index = -1
        self.wait_ace_ready()
        self._retract(index, 5, 10)
        self.dwell(0.3)

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        try:
            index = gcmd.get_int("INDEX", self._feed_assist_index, minval=0, maxval=3)
            self._disable_feed_assist(index)
        except AceException as e:
            raise gcmd.error(str(e))

    def _feed(self, index, length, speed, how_wait=None):
        self.wait_ace_ready()
        self.send_request(
            {
                "method": "feed_filament",
                "params": {"index": index, "length": length, "speed": speed},
            },
            self._command_callback(None),
        )
        wait_len = how_wait if how_wait is not None else length
        self.dwell((wait_len / float(speed)) + 0.1)

    def cmd_ACE_FEED(self, gcmd):
        try:
            index = gcmd.get_int("INDEX", minval=0, maxval=3)
            length = gcmd.get_int("LENGTH", minval=1)
            speed = gcmd.get_int("SPEED", self.feed_speed, minval=1)
            self._feed(index, length, speed)
        except AceException as e:
            raise gcmd.error(str(e))

    def _retract(self, index, length, speed):
        self.wait_ace_ready()
        self.send_request(
            {
                "method": "unwind_filament",
                "params": {"index": index, "length": length, "speed": speed},
            },
            self._command_callback(None),
        )
        self.dwell((length / float(speed)) + 0.1)

    def retract_fil(self, index):
        self._retract(index, self.retract_length, self.retract_speed)

    def cmd_ACE_RETRACT(self, gcmd):
        try:
            index = gcmd.get_int("INDEX", minval=0, maxval=3)
            length = gcmd.get_int("LENGTH", minval=1)
            speed = gcmd.get_int("SPEED", self.retract_speed, minval=1)
            self._retract(index, length, speed)
        except AceException as e:
            raise gcmd.error(str(e))

    def _stop_feeding(self, index):
        self.send_request(
            {"method": "stop_feed_filament", "params": {"index": index}},
            self._command_callback(None),
        )

    def get_status(self, eventtime=None):
        return {
            "connected": self._connected,
            "status": self._info.get("status"),
            "temp": self._info.get("temp", 0),
            "dryer_status": self._info.get("dryer_status", {}),
            "gate_status": self.gate_status,
            "slots": self._info.get("slots", []),
        }


def load_config(config):
    return BunnyAce(config)
