import glob
import json
import logging
import queue
import struct
import traceback

import serial

from . import ace2_protocol


GATE_UNKNOWN = -1
GATE_EMPTY = 0
GATE_AVAILABLE = 1

MODEL_AUTO = "auto"
MODEL_ACE_PRO = "ace_pro"
MODEL_ACE_2_PRO = "ace_2_pro"

ACE_PRO_GLOB = "/dev/serial/by-id/usb-ANYCUBIC*"
ACE_2_PRO_GLOB = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_*"
ACE_PRO_DEFAULT_SERIAL = "/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00"
ACE_2_PRO_DEFAULT_SERIAL = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B5F070433-if00"

MODEL_INFO = {
    MODEL_ACE_PRO: {
        "display_name": "Anycubic ACE Pro",
        "protocol": "json",
        "baud": 115200,
        "default_serial": ACE_PRO_DEFAULT_SERIAL,
    },
    MODEL_ACE_2_PRO: {
        "display_name": "Anycubic ACE 2 Pro",
        "protocol": "protobuf",
        "baud": 230400,
        "default_serial": ACE_2_PRO_DEFAULT_SERIAL,
    },
}


class AceException(Exception):
    pass


class BunnyAce:
    VARS_ACE_REVISION = "ace__revision"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        self.device_model = config.get("device_model", MODEL_AUTO).lower()
        if self.device_model not in (MODEL_AUTO, MODEL_ACE_PRO, MODEL_ACE_2_PRO):
            logging.warning("ACE: invalid device_model '%s', using auto",
                            self.device_model)
            self.device_model = MODEL_AUTO
        self.configured_serial = config.get("serial", ACE_PRO_DEFAULT_SERIAL)
        self.configured_baud = config.getint("baud", 115200)
        self.detected_model, self.serial_id = self._detect_model_and_serial()
        self.model_info = MODEL_INFO[self.detected_model]
        self._is_v2 = self.detected_model == MODEL_ACE_2_PRO
        self.baud = self.model_info["baud"] if self._is_v2 else self.configured_baud
        self.feed_speed = config.getint("feed_speed", 50, minval=1)
        self.load_speed = config.getint("load_speed", 100, minval=1)
        self.retract_speed = config.getint("retract_speed", 50, minval=1)
        self.assist_source = config.get("assist_source", "ace").lower()
        if self.assist_source not in ("ace", "snapmaker", "off"):
            self.assist_source = "ace"
        self.rfid_source = config.get("rfid_source", "existing").lower()
        if self.rfid_source not in ("existing", "ace", "none"):
            self.rfid_source = "existing"
        self.force_generic = config.getboolean("force_generic", False)
        old_retract_length = config.getint("retract_length", 100, minval=1)
        old_feed_length = config.getint("feed_length", 100, minval=1)
        self.feed_lengths = [
            config.getint("feed_length_slot%d" % (i + 1), old_feed_length, minval=1)
            for i in range(4)
        ]
        self.load_lengths = [
            config.getint("load_length_slot%d" % (i + 1), 850, minval=1)
            for i in range(4)
        ]
        self.retract_lengths = [
            config.getint("retract_length_slot%d" % (i + 1), old_retract_length, minval=1)
            for i in range(4)
        ]
        self.retract_length = self.retract_lengths[0]
        self.feed_length = self.feed_lengths[0]
        self.feed_check_len = config.getint("feed_check_len", 254, minval=1, maxval=255)
        self.feed_check_error_len = config.getint(
            "feed_check_error_len", 254, minval=1, maxval=255
        )
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
        self._v2_seq = 0
        self._v2_callback_map = {}
        self.gate_status = [GATE_UNKNOWN] * 4
        self._info = {
            "status": "disconnected",
            "device_model": self.detected_model,
            "display_name": self.model_info["display_name"],
            "protocol": self.model_info["protocol"],
            "serial": self.serial_id,
            "baud": self.baud,
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
        self.gcode.register_command(
            "ACE_GET_STATUS",
            self.cmd_ACE_GET_STATUS,
            desc="Reports ACE connection and slot status",
        )
        self.gcode.register_command(
            "ACE_GET_TEMP",
            self.cmd_ACE_GET_TEMP,
            desc="Reports ACE temperature status",
        )

    def _detect_model_and_serial(self):
        ace_pro_devices = glob.glob(ACE_PRO_GLOB)
        ace_2_pro_devices = glob.glob(ACE_2_PRO_GLOB)
        if self.device_model == MODEL_ACE_PRO:
            serial_id = ace_pro_devices[0] if ace_pro_devices else self.configured_serial
            return MODEL_ACE_PRO, serial_id
        if self.device_model == MODEL_ACE_2_PRO:
            serial_id = ace_2_pro_devices[0] if ace_2_pro_devices else (
                self.configured_serial
                if "1a86" in self.configured_serial
                else ACE_2_PRO_DEFAULT_SERIAL
            )
            return MODEL_ACE_2_PRO, serial_id
        if ace_pro_devices:
            return MODEL_ACE_PRO, ace_pro_devices[0]
        if ace_2_pro_devices:
            return MODEL_ACE_2_PRO, ace_2_pro_devices[0]
        if "1a86" in self.configured_serial:
            return MODEL_ACE_2_PRO, self.configured_serial
        return MODEL_ACE_PRO, self.configured_serial

    def _handle_ready(self):
        self.toolhead = self.printer.lookup_object("toolhead")
        logging.info(
            "ACE: connecting to %s on %s @ %d baud",
            self.model_info["display_name"], self.serial_id, self.baud
        )
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
                rtscts=not self._is_v2,
                timeout=0,
                write_timeout=0,
            )
            if self._is_v2:
                self._initialize_v2_transport()
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
            if self._is_v2:
                self.send_request(
                    {
                        "method": "set_feed_check",
                        "params": {
                            "check_len": self.feed_check_len,
                            "error_len": self.feed_check_error_len,
                        },
                    },
                    self._command_callback(None),
                )
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

    def _initialize_v2_transport(self):
        self._serial.timeout = 0.2
        self._serial.reset_input_buffer()
        self._serial.write(ace2_protocol.build_discover_packet(seq=1))
        self._serial.flush()
        buffer = bytearray()
        deadline = self.reactor.monotonic() + 1.5
        uids = None
        while self.reactor.monotonic() < deadline:
            waiting = self._serial.in_waiting
            if waiting:
                buffer.extend(self._serial.read(waiting))
                packets, buffer = ace2_protocol.parse_stream(buffer)
                for packet in packets:
                    if (
                        packet["is_resp"]
                        and packet["cmd"] == ace2_protocol.CMD_DISCOVER_DEVICE
                    ):
                        uids = ace2_protocol.parse_discover_response(packet["payload"])
                        break
            if uids is not None:
                break
            self.reactor.pause(self.reactor.monotonic() + 0.05)
        if uids is not None:
            self._serial.write(ace2_protocol.build_assign_id_packet(
                uids[0], uids[1], uids[2], dev_id=1, seq=2
            ))
            self._serial.flush()
        self._serial.timeout = 0
        self._serial.reset_input_buffer()

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
        if self._is_v2:
            return self._send_v2_request(request)
        return self._send_json_request(request)

    def _send_json_request(self, request):
        if not self._connected or self._serial is None:
            raise AceException("%s is not connected" % (self.model_info["display_name"],))
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

    def _send_v2_request(self, request):
        if not self._connected or self._serial is None:
            raise AceException("%s is not connected" % (self.model_info["display_name"],))
        encoded = ace2_protocol.encode_v1_request(request)
        if encoded is None:
            raise AceException("ACE 2 Pro command is not supported: %s" % (
                request.get("method"),
            ))
        cmd, payload, decoder = encoded
        self._v2_seq = (self._v2_seq + 1) & 0xFFFF
        if self._v2_seq == 0:
            self._v2_seq = 1
        self._v2_callback_map[self._v2_seq] = (
            self._callback_map.pop(request.get("id"), None),
            decoder,
        )
        try:
            self._serial.write(
                ace2_protocol.build_packet(cmd, payload, seq=self._v2_seq)
            )
        except Exception:
            self._serial_disconnect()
            raise AceException("ACE 2 Pro serial write failed")

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
        if self._is_v2:
            self._process_v2_data(raw_bytes)
            return
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

    def _process_v2_data(self, raw_bytes):
        self.read_buffer += raw_bytes
        packets, self.read_buffer = ace2_protocol.parse_stream(self.read_buffer)
        for packet in packets:
            if not packet["is_resp"]:
                continue
            callback, decoder = self._v2_callback_map.pop(
                packet["seq"], (None, None)
            )
            if callback is None or decoder is None:
                continue
            try:
                response = decoder(packet["payload"])
            except Exception:
                logging.info("ACE: invalid ACE 2 Pro response: %s",
                             traceback.format_exc())
                continue
            callback(response)
        if not self._v2_callback_map and not self._callback_map:
            self._info["status"] = "ready"

    def _handle_info_response(self, response):
        if response.get("code", 0) != 0:
            self.log_error("ACE Error: %s" % (response.get("msg"),))
            return
        result = response.get("result", {})
        self.log_always(
            "ACE: Connected to %s on %s @ %d baud\n Firmware Version: %s"
            % (
                self.model_info["display_name"],
                self.serial_id,
                self.baud,
                result.get("firmware", result.get("version", "Unknown")),
            )
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
                    self.rfid_source == "ace" and
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
        if self.rfid_source != "ace":
            return
        color = slot.get("color") or [0, 0, 0]
        try:
            rgb = "%02X%02X%02X" % (int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            rgb = "000000"
        vendor = slot.get("brand", "Generic")
        if self.force_generic:
            vendor = "Generic"
        self.gcode.run_script_from_command(
            'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=%d FILAMENT_TYPE="%s" '
            'FILAMENT_COLOR_RGBA=%s VENDOR="%s" FILAMENT_SUBTYPE=""'
            % (
                index,
                slot.get("type", "PLA"),
                rgb,
                vendor,
            )
        )

    def _pre_load(self, gate):
        self._feed(gate, self.feed_lengths[gate], self.feed_speed, 0)
        self.log_always("Select AutoLoad from the menu")

    def wait_ace_ready(self):
        if not self._connected:
            raise AceException("%s is not connected" % (self.model_info["display_name"],))
        deadline = self.reactor.monotonic() + 30.0
        while self._info.get("status") != "ready":
            if self.reactor.monotonic() >= deadline:
                raise AceException("Timed out waiting for %s" % (
                    self.model_info["display_name"],
                ))
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
            temperature = gcmd.get_int(
                "TEMP", gcmd.get_int("TEMPERATURE", 55, minval=1), minval=1
            )
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
        if self.assist_source != "ace":
            return
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
        self._retract(index, self.retract_lengths[index], self.retract_speed)

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

    def cmd_ACE_GET_STATUS(self, gcmd):
        status = self.get_status()
        gcmd.respond_info(
            "ACE Status\n"
            "  Model: %s\n"
            "  Protocol: %s\n"
            "  Serial: %s\n"
            "  Baud: %s\n"
            "  Connected: %s\n"
            "  Status: %s\n"
            "  Assist Source: %s\n"
            "  RFID Source: %s\n"
            "  Gates: %s"
            % (
                status["display_name"],
                status["protocol"],
                status["serial"],
                status["baud"],
                "yes" if status["connected"] else "no",
                status["status"],
                status["assist_source"],
                status["rfid_source"],
                status["gate_status"],
            )
        )

    def cmd_ACE_GET_TEMP(self, gcmd):
        try:
            self.wait_ace_ready()
            self.send_request(
                {"method": "get_temp"},
                lambda response: gcmd.respond_info("ACE temperature: %s" % (
                    response.get("result", response),
                )),
            )
        except AceException as e:
            raise gcmd.error(str(e))

    def get_status(self, eventtime=None):
        return {
            "connected": self._connected,
            "status": self._info.get("status"),
            "device_model": self.detected_model,
            "display_name": self.model_info["display_name"],
            "protocol": self.model_info["protocol"],
            "serial": self.serial_id,
            "baud": self.baud,
            "assist_source": self.assist_source,
            "rfid_source": self.rfid_source,
            "temp": self._info.get("temp", 0),
            "dryer_status": self._info.get("dryer_status", {}),
            "gate_status": self.gate_status,
            "slots": self._info.get("slots", []),
        }


def load_config(config):
    return BunnyAce(config)
