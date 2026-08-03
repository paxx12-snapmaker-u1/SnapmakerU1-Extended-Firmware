class AFCState:
    IDLE = "idle"
    LOADING = "loading"
    UNLOADING = "unloading"
    TOOL_CHANGE = "tool_change"
    ERROR = "error"

class AFC:
    def __init__(self, config):
        self.printer = config.get_printer()
        config.get("enabled", "True")
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object('webhooks')

        self.units = {}
        self.lanes = {}

        self.printer.register_event_handler("klippy:connect", self._handle_connect)

    def _handle_connect(self):
        for name, obj in self.printer.lookup_objects("AFC_unit"):
            unit_name = name.replace("AFC_", "", 1)
            self.units[unit_name] = obj

        for name, obj in self.printer.lookup_objects("AFC_lane"):
            lane_name = name.split(None, 1)[1] if " " in name else name
            self.lanes[lane_name] = obj

    def _get_current_lane(self):
        try:
            toolhead = self.printer.lookup_object('toolhead')
            current_extruder = toolhead.extruder.get_name()
            for lane_name, lane in self.lanes.items():
                if lane.extruder_name == current_extruder:
                    return lane_name
        except:
            pass
        return None

    def get_status(self, eventtime=None):
        str = {}
        str['current_load'] = None
        str['current_lane'] = self._get_current_lane()
        str['next_lane'] = None
        str['current_state'] = AFCState.IDLE
        str["current_toolchange"] = 0
        str["number_of_toolchanges"] = 0
        str['spoolman'] = self.webhooks.has_remote_method('spoolman_set_active_spool')
        str["td1_present"] = False
        str["lane_data_enabled"] = False
        str['error_state'] = False
        str["bypass_state"] = False
        str["quiet_mode"] = False
        str["position_saved"] = False

        str['units'] = list(self.units.keys())
        str['lanes'] = list(self.lanes.keys())
        str["extruders"] = []
        str["hubs"] = []
        str["buffers"] = []
        str["message"] = ""
        str["led_state"] = ""
        return str

def load_config(config):
    return AFC(config)
