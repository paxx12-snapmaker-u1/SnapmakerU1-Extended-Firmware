import json
import logging
import os
import copy
from . import filament_protocol

SPOOLMAN_PROXY_ENDPOINT_BASE = "klippy/spoolman_proxy"

class AFCLaneState:
    EMPTY = "empty"
    LOADING = "loading"
    LOADED = "loaded"
    UNLOADING = "unloading"
    TOOL_LOADED = "tool_loaded"
    ERROR = "error"

class AFCLane:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.webhooks = self.printer.lookup_object('webhooks')
        self.name = config.get_name().replace("AFC_lane ", "", 1)

        self.unit_name = config.get("unit", "")
        self.lane_index = config.getint("lane", 0)
        self.extruder_name = config.get("extruder", None)
        self.toolhead_sensor_name = config.get("toolhead_sensor", None)
        self.filament_feed_name = config.get("filament_feed", None)

        self.print_task_config = None
        self.toolhead_sensor = None
        self.filament_feed = None

        self.pending_refresh = False
        self.pending_spool_id = None

        self.cached_spool_weight = 1000

        self.printer.register_event_handler("klippy:connect", self._handle_connect)

        self.gcode.register_mux_command(
            "SET_SPOOL_ID", "LANE", self.name,
            self.cmd_SET_SPOOL_ID,
            desc=self.cmd_SET_SPOOL_ID_help)

        self.gcode.register_mux_command(
            "REFRESH_SPOOL", "LANE", self.name,
            self.cmd_REFRESH_SPOOL,
            desc=self.cmd_REFRESH_SPOOL_help)

        self.find_by_spool_id_callback = f"{SPOOLMAN_PROXY_ENDPOINT_BASE}/find_by_spool_id/{config.get_name()}"
        self.webhooks.register_endpoint(
            self.find_by_spool_id_callback,
            self._handle_find_by_spool_id_callback)

    def _handle_connect(self):
        try:
            self.print_task_config = self.printer.lookup_object("print_task_config")
        except:
            pass

        if self.filament_feed_name:
            try:
                self.filament_feed = self.printer.lookup_object(self.filament_feed_name)
            except:
                pass

        if self.toolhead_sensor_name:
            try:
                self.toolhead_sensor = self.printer.lookup_object(self.toolhead_sensor_name)
            except:
                pass

    def _set_filament_config(self, vendor='NONE', type='NONE', sub_type='NONE', color='FFFFFFFF', official=False, spool_id=0, weight=1000):
        try:
            info = copy.deepcopy(filament_protocol.FILAMENT_INFO_STRUCT)
            if vendor:
                info['VENDOR'] = vendor
            if type:
                info['MAIN_TYPE'] = type
            if sub_type:
                info['SUB_TYPE'] = sub_type
            if color:
                info['COLOR_NUMS'] = 1
                info['RGB_1'] = int(color[:6], 16)
                info['ALPHA'] = int(color[6:8] or 'FF', 16)
                info['ARGB_COLOR'] = info['ALPHA'] << 24 | info['RGB_1']
            info['SPOOL_ID'] = spool_id
            info['OFFICIAL'] = spool_id not in (None, 0) or official
            logging.info(f"Setting filament config for lane {self.name}: {info}")
            self.print_task_config._rfid_filament_info_update_cb(self.lane_index, info, is_clear=True)
            self.cached_spool_weight = weight
            return True
        except Exception as e:
            logging.error(f"Failed to set filament config: {e}")
            return False

    def _clear_filament_config(self):
        try:
            info = copy.deepcopy(filament_protocol.FILAMENT_INFO_STRUCT)
            logging.info(f"Clearing filament config for lane {self.name}")
            self.print_task_config._rfid_filament_info_update_cb(self.lane_index, info, is_clear=True)
            self.cached_spool_weight = -1
            return True
        except Exception as e:
            logging.error(f"Failed to clear spool data: {e}")
            return False

    cmd_SET_SPOOL_ID_help = "Set spool ID and fetch filament data from Spoolman"
    def cmd_SET_SPOOL_ID(self, gcmd):
        """Set spool ID and fetch filament info from spoolman"""
        spool_id = gcmd.get_int('SPOOL_ID', 0)

        if not spool_id:
            gcmd.respond_info(f"Clearing spool data for lane {self.name}")
            reactor = self.printer.get_reactor()
            reactor.register_callback(lambda et: self._clear_filament_config())
            return

        self.pending_refresh = False
        self.pending_spool_id = spool_id

        gcmd.respond_info(f"Fetching spool {spool_id} data for lane {self.name}...")

        try:
            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=self.find_by_spool_id_callback,
                request_method="GET",
                path=f"/v1/spool/{spool_id}"
            )
        except Exception as e:
            logging.error(f"Failed to query spoolman: {e}")

    def _refresh_spool(self, spool_id):
        if not spool_id:
            return
        if not self.webhooks.has_remote_method('spoolman_proxy'):
            return

        try:
            self.pending_refresh = True
            self.pending_spool_id = spool_id
            self.webhooks.call_remote_method(
                "spoolman_proxy",
                cb_endpoint=self.find_by_spool_id_callback,
                request_method="GET",
                path=f"/v1/spool/{spool_id}"
            )
        except Exception as e:
            logging.error(f"Failed to query spoolman: {e}")

    cmd_REFRESH_SPOOL_help = "Refresh current spool data from Spoolman"
    def cmd_REFRESH_SPOOL(self, gcmd):
        state = self._get_state()
        spool_id = state.get('spool', {}).get('spool_id', 0)
        if not spool_id:
            gcmd.respond_info(f"No spool configured for lane {self.name}")
            return

        self._refresh_spool(spool_id)

    def _handle_find_by_spool_id_callback(self, web_request):
        try:
            payload = web_request.get_dict('payload', {})
            error = web_request.get('error', None)

            if error:
                logging.error(f"Spoolman error: {error}")
                return

            self._apply_spool_data_from_webhook(payload)
        except Exception as e:
            raise web_request.error(f"Failed to process spoolman response: {e}")

    def _apply_spool_data_from_webhook(self, response):
        """Apply spool data from spoolman response via webhook"""
        if not response:
            self.gcode.respond_info("Empty response from spoolman")
            return

        spool_id = response.get('id', 0)
        filament = response.get('filament', {})
        material = filament.get('material', 'PLA')
        vendor = filament.get('vendor', {}).get('name', 'Generic')
        color_hex = filament.get('color_hex', 'FFFFFFFF')
        sub_type = filament.get('extra', {}).get('sub_type', 'Basic')
        weight = response.get('remaining_weight', -1)

        if self.pending_spool_id != spool_id:
            self.gcode.respond_info(f"Spool ID mismatch for lane {self.name}: expected {self.pending_spool_id}, got {spool_id}")
            return

        if self.pending_refresh:
            self.pending_refresh = False
            self.cached_spool_weight = weight
            return

        self._set_filament_config(
            vendor=vendor,
            type=material,
            sub_type=sub_type,
            color=color_hex,
            spool_id=spool_id,
            weight=weight,
            official=True
        )

    def _get_state(self, eventtime=None):
        """Get filament info from print_task_config based on lane index"""
        if not self.print_task_config:
            return {}

        state = {
            'loaded': False,
            'tool_loaded': False,
            'spool': {
                'vendor': 'NONE',
                'type': 'NONE',
                'subtype': 'NONE',
                'color': 'FFFFFFFF',
                'spool_id': 0,
            },
            'map': f"T{self.lane_index}",
            'runout_lane': 'NONE',
        }

        try:
            status = self.print_task_config.get_status(eventtime)
            state['loaded'] = dict(enumerate(status.get('filament_exist', []))).get(self.lane_index, False)
            if status.get('auto_replenish_filament', False):
                state['runout_lane'] = 'AUTO'

            spool = state['spool']
            spool['vendor'] = dict(enumerate(status.get('filament_vendor', []))).get(self.lane_index, 'NONE')
            spool['type'] = dict(enumerate(status.get('filament_type', []))).get(self.lane_index, 'NONE')
            spool['subtype'] = dict(enumerate(status.get('filament_sub_type', []))).get(self.lane_index, 'NONE')
            spool['color'] = dict(enumerate(status.get('filament_color_rgba', []))).get(self.lane_index, 'FFFFFFFF')
            spool['spool_id'] = dict(enumerate(status.get('filament_spool_id', []))).get(self.lane_index, 0)

            tool_to_extruder = dict(enumerate(status.get('extruder_map_table', [])))
            for tool_idx, extruder_idx in tool_to_extruder.items():
                if extruder_idx == self.lane_index:
                    # TODO: AFC only supports a single tool mapped
                    state['map'] = f"T{tool_idx}"
                    break
        except:
            pass

        try:
            status = self.toolhead_sensor.get_status(eventtime)
            state['tool_loaded'] = status.get('filament_detected', True)
        except:
            state['tool_loaded'] = state['loaded']

        return state

    def get_status(self, eventtime=None):
        response = {}

        state = self._get_state(eventtime)
        spool = state.get('spool', {})

        response['name'] = self.name
        response['unit'] = self.unit_name
        response['lane'] = self.lane_index
        response['extruder'] = self.extruder_name
        response['map'] = state.get('map', f"T{self.lane_index}")
        response['load'] = state.get('loaded', False)
        response['prep'] = state.get('loaded', False)
        response['tool_loaded'] = state.get('tool_loaded', response['load'])
        response['loaded_to_hub'] = False
        response['material'] = spool.get('type', 'NONE')
        response['spool_id'] = spool.get('spool_id', 0) or 0
        response['color'] = f"#{spool.get('color', 'FFFFFFFF')[:6]}" # RGB only, ignore alpha
        response['weight'] = self.cached_spool_weight if response['spool_id'] > 0 else 1000
        response['runout_lane'] = state.get('runout_lane', '?')
        response['filament_status'] = 'unknown'
        response['filament_status_led'] = 'gray'
        response['status'] = AFCLaneState.EMPTY
        return response

def load_config_prefix(config):
    return AFCLane(config)
