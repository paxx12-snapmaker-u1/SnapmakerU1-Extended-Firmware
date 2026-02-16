import json
import logging
import os

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
        self.name = config.get_name().replace("AFC_lane ", "", 1)

        self.unit_name = config.get("unit", "")
        self.lane_index = config.getint("lane", 0)
        self.extruder_name = config.get("extruder", None)
        self.toolhead_sensor_name = config.get("toolhead_sensor", None)
        self.filament_feed_name = config.get("filament_feed", None)

        self.print_task_config = None
        self.toolhead_sensor = None
        self.filament_feed = None

        self.printer.register_event_handler("klippy:connect", self._handle_connect)

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
                'color': 'FFFFFFFF'
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
        response['spool_id'] = 0
        response['color'] = f"#{spool.get('color', 'FFFFFFFF')[:6]}" # RGB only, ignore alpha
        response['weight'] = 1000
        response['runout_lane'] = state.get('runout_lane', '?')
        response['filament_status'] = 'unknown'
        response['filament_status_led'] = 'gray'
        response['status'] = AFCLaneState.EMPTY
        return response

def load_config_prefix(config):
    return AFCLane(config)
