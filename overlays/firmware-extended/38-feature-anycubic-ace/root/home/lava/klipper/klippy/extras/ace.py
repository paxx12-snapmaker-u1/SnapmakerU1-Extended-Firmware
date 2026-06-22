import glob
import json
import logging
import queue
import threading
import traceback

import serial

from . import ace2_protocol
from . import ace_protocol


GATE_UNKNOWN = -1
GATE_EMPTY = 0
GATE_AVAILABLE = 1

MODEL_ACE_PRO = "ace_pro"
MODEL_ACE_2_PRO = "ace_2_pro"

ACE_PRO_GLOB = "/dev/serial/by-id/usb-ANYCUBIC*"
ACE_2_PRO_GLOBS = (
    "/dev/serial/by-id/usb-1a86_USB_Single_Serial_*",
    "/dev/serial/by-id/usb-1a86_USB_Serial*",
)
ACE_PRO_DEFAULT_SERIAL = "/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00"

MODEL_INFO = {
    MODEL_ACE_PRO: {
        "display_name": "Anycubic ACE Pro",
        "protocol": "json",
        "baud": 115200,
    },
    MODEL_ACE_2_PRO: {
        "display_name": "Anycubic ACE 2 Pro",
        "protocol": "protobuf",
        "baud": 230400,
    },
}

RFID_SOURCE_LABELS = {
    "existing": "Existing U1/OpenRFID metadata",
    "ace": "ACE slot RFID metadata",
    "none": "Off",
}


class AceException(Exception):
    pass


class AceManager:
    VARS_ACE_REVISION = "ace__revision"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        self.detected_model, self.serial_id = self._detect_model_and_serial()
        self.model_info = MODEL_INFO[self.detected_model]
        self._is_v2 = self.detected_model == MODEL_ACE_2_PRO
        self.baud = self.model_info["baud"]
        self.load_speed = config.getint("load_speed", 100, minval=1)
        self.rfid_source = config.get("rfid_source", "existing").lower()
        if self.rfid_source not in ("existing", "ace", "none"):
            self.rfid_source = "existing"
        self.force_generic = config.getboolean("force_generic", False)
        self.load_lengths = [
            config.getint("load_length_slot%d" % (i + 1), 850, minval=1)
            for i in range(4)
        ]
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
        self._callback_deadlines = {}
        self._connect_timer = None
        self._heartbeat_timer = None
        self._ace_dev_fd = None
        self.read_buffer = bytearray()
        self._v2_seq = 0
        self._v2_callback_map = {}
        self._v2_callback_deadlines = {}
        self._v2_callback_lock = threading.Lock()
        self._v2_reader_stop = None
        self._v2_reader_thread = None
        self._v2_writer_stop = None
        self._v2_writer_thread = None
        self._v2_writer_queue = None
        self._request_timeout = 5.0
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
            raise config.error("Anycubic ACE requires a [save_variables] section")
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
            "ACE_GET_STATUS",
            self.cmd_ACE_GET_STATUS,
            desc="Reports ACE connection and slot status",
        )
        self.gcode.register_command(
            "ACE_GET_TEMP",
            self.cmd_ACE_GET_TEMP,
            desc="Reports ACE temperature status",
        )
        self.gcode.register_command(
            "ACE_REFRESH",
            self.cmd_ACE_REFRESH,
            desc="Reconnects and redetects Anycubic ACE",
        )

    def _detect_model_and_serial(self):
        ace_pro_devices = glob.glob(ACE_PRO_GLOB)
        ace_2_pro_devices = [
            device
            for pattern in ACE_2_PRO_GLOBS
            for device in glob.glob(pattern)
        ]
        if ace_pro_devices:
            return MODEL_ACE_PRO, ace_pro_devices[0]
        if ace_2_pro_devices:
            return MODEL_ACE_2_PRO, ace_2_pro_devices[0]
        return MODEL_ACE_PRO, ACE_PRO_DEFAULT_SERIAL

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

    def _refresh_connection(self):
        self._serial_disconnect()
        self.detected_model, self.serial_id = self._detect_model_and_serial()
        self.model_info = MODEL_INFO[self.detected_model]
        self._is_v2 = self.detected_model == MODEL_ACE_2_PRO
        self.baud = self.model_info["baud"]
        self.read_buffer = bytearray()
        self._v2_seq = 0
        if self._connect_timer is None:
            self._connect_timer = self.reactor.register_timer(
                self._connect, self.reactor.monotonic()
            )
        else:
            self.reactor.update_timer(self._connect_timer, self.reactor.monotonic())

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

    def _connect(self, eventtime):
        try:
            self._serial = serial.Serial(
                port=self.serial_id,
                baudrate=self.baud,
                exclusive=True,
                rtscts=not self._is_v2,
                timeout=0.1 if self._is_v2 else 0,
                write_timeout=1.0 if self._is_v2 else 0,
            )
            if self._is_v2:
                self._serial.reset_input_buffer()
            self._connected = True
            self._info["status"] = "ready"
            self._request_id = 0
            if self._is_v2:
                self._start_v2_reader_thread()
                self._start_v2_writer_thread()
            else:
                self._ace_dev_fd = self.reactor.register_fd(
                    self._serial.fileno(), self._reader_cb
                )
            self._heartbeat_timer = self.reactor.register_timer(
                self._periodic_heartbeat_event, self.reactor.NOW
            )
            if self._is_v2:
                self.send_request(
                    {"method": "discover_device"},
                    self._command_callback(None),
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
        reader_stop = self._v2_reader_stop
        writer_stop = self._v2_writer_stop
        if reader_stop is not None:
            reader_stop.set()
        if writer_stop is not None:
            writer_stop.set()
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        current_thread = threading.current_thread()
        if (
            self._v2_reader_thread is not None
            and self._v2_reader_thread is not current_thread
        ):
            self._v2_reader_thread.join(1.0)
        if (
            self._v2_writer_thread is not None
            and self._v2_writer_thread is not current_thread
        ):
            self._v2_writer_thread.join(1.0)
        self._v2_reader_stop = None
        self._v2_reader_thread = None
        self._v2_writer_stop = None
        self._v2_writer_thread = None
        self._v2_writer_queue = None
        self._connected = False
        self._info["status"] = "disconnected"
        self._callback_map.clear()
        self._callback_deadlines.clear()
        with self._v2_callback_lock:
            self._v2_callback_map.clear()
            self._v2_callback_deadlines.clear()
        if self._heartbeat_timer is not None:
            self.reactor.unregister_timer(self._heartbeat_timer)
            self._heartbeat_timer = None
        if self._ace_dev_fd is not None:
            self.reactor.set_fd_wake(self._ace_dev_fd, False, False)
            self._ace_dev_fd = None

    def _start_v2_reader_thread(self):
        if self._v2_reader_thread is not None:
            return
        self._v2_reader_stop = threading.Event()
        self._v2_reader_thread = threading.Thread(
            target=self._v2_reader_loop,
            name="ace2-reader",
        )
        self._v2_reader_thread.daemon = True
        self._v2_reader_thread.start()

    def _start_v2_writer_thread(self):
        if self._v2_writer_thread is not None:
            return
        self._v2_writer_stop = threading.Event()
        self._v2_writer_queue = queue.Queue()
        self._v2_writer_thread = threading.Thread(
            target=self._v2_writer_loop,
            name="ace2-writer",
        )
        self._v2_writer_thread.daemon = True
        self._v2_writer_thread.start()

    def _v2_reader_loop(self):
        while (
            self._v2_reader_stop is not None
            and not self._v2_reader_stop.is_set()
        ):
            try:
                if self._serial is None or not self._serial.is_open:
                    return
                data = self._serial.read(128)
                if data:
                    self._process_v2_data(data)
            except serial.SerialException:
                logging.info("ACE: ACE 2 Pro read error: %s", traceback.format_exc())
                self._restart_after_v2_transport_error()
                return
            except Exception:
                logging.info("ACE: ACE 2 Pro process error: %s", traceback.format_exc())

    def _v2_writer_loop(self):
        while (
            self._v2_writer_stop is not None
            and not self._v2_writer_stop.is_set()
        ):
            try:
                packet = self._v2_writer_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if self._serial is None or not self._serial.is_open:
                    return
                self._serial.write(packet)
                self._serial.flush()
            except serial.SerialTimeoutException:
                logging.info("ACE: ACE 2 Pro write timeout")
                self._restart_after_v2_transport_error()
                return
            except serial.SerialException:
                logging.info("ACE: ACE 2 Pro write error: %s", traceback.format_exc())
                self._restart_after_v2_transport_error()
                return

    def _restart_after_v2_transport_error(self):
        self.reactor.register_async_callback(
            lambda eventtime: self._restart_v2_connection(eventtime)
        )

    def _restart_v2_connection(self, eventtime):
        self._serial_disconnect()
        self._connect_timer = self.reactor.register_timer(
            self._connect, self.reactor.monotonic() + 1.0
        )

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
        data, crc = ace_protocol.encode_json_frame(payload)
        attempts = 0
        while crc == 0xAAFF and attempts < 10:
            request["id"] = self._get_next_request_id()
            payload = json.dumps(request).encode("utf-8")
            data, crc = ace_protocol.encode_json_frame(payload)
            attempts += 1
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
            msg_id = request.get("id")
            self._callback_map.pop(msg_id, None)
            self._callback_deadlines.pop(msg_id, None)
            if not self._callback_map and not self._v2_callback_map:
                self._info["status"] = "ready"
            raise AceException("ACE 2 Pro command is not supported: %s" % (
                request.get("method"),
            ))
        cmd, payload, decoder = encoded
        self._v2_seq = (self._v2_seq + 1) & 0xFFFF
        if self._v2_seq == 0:
            self._v2_seq = 1
        msg_id = request.get("id")
        with self._v2_callback_lock:
            self._v2_callback_map[self._v2_seq] = (
                self._callback_map.pop(msg_id, None),
                decoder,
            )
            self._v2_callback_deadlines[self._v2_seq] = self._callback_deadlines.pop(
                msg_id, self.reactor.monotonic() + self._request_timeout
            )
        try:
            packet = ace2_protocol.build_packet(cmd, payload, seq=self._v2_seq)
            if self._v2_writer_queue is None:
                raise AceException("ACE 2 Pro writer is not running")
            self._v2_writer_queue.put(packet)
        except Exception:
            self._serial_disconnect()
            raise AceException("ACE 2 Pro serial write failed")

    def send_request(self, request, callback, mark_busy=True):
        msg_id = self._get_next_request_id()
        request["id"] = msg_id
        self._callback_map[msg_id] = callback
        self._callback_deadlines[msg_id] = self.reactor.monotonic() + self._request_timeout
        if mark_busy:
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
        for response in ace_protocol.parse_json_frames(self.read_buffer):
            msg_id = response.get("id")
            callback = self._callback_map.pop(msg_id, None)
            self._callback_deadlines.pop(msg_id, None)
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
            with self._v2_callback_lock:
                callback, decoder = self._v2_callback_map.pop(
                    packet["seq"], (None, None)
                )
                self._v2_callback_deadlines.pop(packet["seq"], None)
            if callback is None or decoder is None:
                continue
            try:
                response = decoder(packet["payload"])
            except Exception:
                logging.info("ACE: invalid ACE 2 Pro response: %s",
                             traceback.format_exc())
                continue
            self.reactor.register_async_callback(
                lambda eventtime, cb=callback, res=response: cb(res)
            )
        with self._v2_callback_lock:
            no_v2_callbacks = not self._v2_callback_map
        if no_v2_callbacks and not self._callback_map:
            self._info["status"] = "ready"

    def _prune_stale_callbacks(self):
        now = self.reactor.monotonic()
        stale_json_ids = [
            msg_id for msg_id, deadline in self._callback_deadlines.items()
            if deadline <= now
        ]
        for msg_id in stale_json_ids:
            self._callback_map.pop(msg_id, None)
            self._callback_deadlines.pop(msg_id, None)
            logging.info("ACE: dropped stale request id %s", msg_id)
        with self._v2_callback_lock:
            stale_v2_ids = [
                seq for seq, deadline in self._v2_callback_deadlines.items()
                if deadline <= now
            ]
            for seq in stale_v2_ids:
                self._v2_callback_map.pop(seq, None)
                self._v2_callback_deadlines.pop(seq, None)
                logging.info("ACE: dropped stale ACE 2 Pro request seq %s", seq)
            no_v2_callbacks = not self._v2_callback_map
        if not self._callback_map and no_v2_callbacks and self._connected:
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
                if (
                    self.rfid_source == "ace" and
                    slot.get("rfid") == 2
                    and self._info.get("slots", [{}] * 4)[i].get("rfid") != 2
                ):
                    if self._is_v2:
                        self.send_request(
                            {"method": "get_filament_info", "params": {"index": i}},
                            lambda resp, idx=i: self._handle_filament_info(idx, resp),
                            mark_busy=False,
                        )
                    else:
                        self._sync_slot_to_print_task_config(i, slot)
                self.gate_status[i] = (
                    GATE_EMPTY if slot.get("status") == "empty" else GATE_AVAILABLE
                )
            self._info.update(result)

        try:
            self._prune_stale_callbacks()
            if self._callback_map or self._v2_callback_map:
                return eventtime + 1.0
            self.send_request({"method": "get_status"}, callback, mark_busy=False)
        except AceException:
            pass
        return eventtime + 1.0

    def get_filament_detect(self, index):
        if index < 0 or index >= 4:
            return False
        slots = self._info.get("slots") or []
        if index < len(slots):
            return slots[index].get("status") != "empty"
        return self.gate_status[index] == GATE_AVAILABLE

    def _async_motion_callback(self, label, index):
        def callback(response):
            if response is None:
                return
            code = response.get("code", 0)
            if code:
                logging.warning("ACE: %s slot=%d failed: %s",
                                label, index, response.get("msg") or code)
            else:
                logging.info("ACE: %s slot=%d ack", label, index)
        return callback

    def _send_motion_async(self, method, index, params, label):
        if not self._connected:
            logging.info("ACE: skipping %s slot=%d; not connected", label, index)
            return False
        request = {"method": method, "params": params}
        try:
            self.send_request(
                request,
                self._async_motion_callback(label, index),
                mark_busy=False,
            )
            return True
        except AceException as e:
            logging.warning("ACE: %s slot=%d failed before send: %s",
                            label, index, e)
            return False

    def start_load_feed(self, index):
        length = self.load_lengths[index]
        logging.info("ACE: load_feeding slot=%d -> feed %dmm @ %dmm/s",
                     index, length, self.load_speed)
        return self._send_motion_async(
            "feed_filament",
            index,
            {"index": index, "length": length, "speed": self.load_speed},
            "load_feed",
        )

    def stop_load_feed(self, index, why=""):
        logging.info("ACE: stop load feed slot=%d (%s)", index, why)
        return self._send_motion_async(
            "stop_feed_filament",
            index,
            {"index": index},
            "stop_load_feed",
        )

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

    def _handle_filament_info(self, index, response):
        if response.get("code", 0) != 0:
            return
        result = response.get("result", {})
        if not result:
            return
        color = result.get("color") or [0, 0, 0]
        try:
            rgb = "%02X%02X%02X" % (int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            rgb = "000000"
        vendor = result.get("brand", "Generic")
        if self.force_generic:
            vendor = "Generic"
        filament_type = result.get("type", "PLA")
        extruder_temp = result.get("extruder_temp", {})
        hotbed_temp = result.get("hotbed_temp", {})
        diameter = result.get("diameter", 1.75)
        extras = []
        if extruder_temp.get("min") and extruder_temp.get("max"):
            extras.append('FILAMENT_EXTRUDER_TEMP_RANGE="%d,%d"' % (
                extruder_temp["min"], extruder_temp["max"]))
        if hotbed_temp.get("min") and hotbed_temp.get("max"):
            extras.append('FILAMENT_BED_TEMP_RANGE="%d,%d"' % (
                hotbed_temp["min"], hotbed_temp["max"]))
        if diameter:
            extras.append("FILAMENT_DIAMETER=%.2f" % diameter)
        self.gcode.run_script_from_command(
            'SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER=%d FILAMENT_TYPE="%s" '
            'FILAMENT_COLOR_RGBA=%s VENDOR="%s" FILAMENT_SUBTYPE=""%s'
            % (
                index,
                filament_type,
                rgb,
                vendor,
                " " + " ".join(extras) if extras else "",
            )
        )

    def wait_ace_ready(self):
        if not self._connected:
            raise AceException("%s is not connected" % (self.model_info["display_name"],))
        deadline = self.reactor.monotonic() + 30.0
        while self._info.get("status") != "ready":
            self._prune_stale_callbacks()
            if self.reactor.monotonic() >= deadline:
                raise AceException("Timed out waiting for %s" % (
                    self.model_info["display_name"],
                ))
            self.reactor.pause(self.reactor.monotonic() + 0.5)

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

    def cmd_ACE_GET_STATUS(self, gcmd):
        status = self.get_status()
        gcmd.respond_info(
            "ACE Status\n"
            "  Model: %s\n"
            "  Protocol: %s\n"
            "  Serial: %s\n"
            "  Baud: %s\n"
            "  Connected: %s\n"
            "  Device Status: %s\n"
            "  RFID Metadata: %s\n"
            "  Gates: %s"
            % (
                status["display_name"],
                status["protocol"],
                status["serial"],
                status["baud"],
                "yes" if status["connected"] else "no",
                status["status"],
                RFID_SOURCE_LABELS.get(status["rfid_source"], status["rfid_source"]),
                status["gate_status"],
            )
        )

    def cmd_ACE_GET_TEMP(self, gcmd):
        try:
            if self._is_v2:
                status = self.get_status()
                dryer = status.get("dryer_status") or {}
                gcmd.respond_info(
                    "ACE temperature\n"
                    "  Temp: %s\n"
                    "  Humidity: %s\n"
                    "  Dryer: %s\n"
                    "  Target: %s\n"
                    "  Remaining: %s"
                    % (
                        status.get("temp", 0),
                        self._info.get("humidity", 0),
                        dryer.get("status", "unknown"),
                        dryer.get("target_temp", 0),
                        dryer.get("remain_time", 0),
                    )
                )
                return
            self.wait_ace_ready()
            self.send_request(
                {"method": "get_temp"},
                lambda response: gcmd.respond_info("ACE temperature: %s" % (
                    response.get("result", response),
                )),
            )
        except AceException as e:
            raise gcmd.error(str(e))

    def cmd_ACE_REFRESH(self, gcmd):
        self._refresh_connection()
        gcmd.respond_info(
            "ACE reconnecting as %s on %s @ %d baud" % (
                self.model_info["display_name"],
                self.serial_id,
                self.baud,
            )
        )

    def get_status(self, eventtime=None):
        return {
            "connected": self._connected,
            "status": self._info.get("status"),
            "device_model": self.detected_model,
            "display_name": self.model_info["display_name"],
            "protocol": self.model_info["protocol"],
            "serial": self.serial_id,
            "baud": self.baud,
            "rfid_source": self.rfid_source,
            "temp": self._info.get("temp", 0),
            "dryer_status": self._info.get("dryer_status", {}),
            "gate_status": self.gate_status,
            "slots": self._info.get("slots", []),
        }


def load_config(config):
    return AceManager(config)
