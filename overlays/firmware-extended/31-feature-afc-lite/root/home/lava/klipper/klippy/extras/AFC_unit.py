# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
# SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

class AFCUnit:
    def __init__(self, config):
        # Prevent klipper error
        config.get("enabled", True)

        self.printer = config.get_printer()
        self.fullname = config.get_name()
        self.name = self.fullname.split()[-1]

        self.lanes = {}
        self.printer.register_event_handler("klippy:connect", self._handle_connect)

    def _handle_connect(self):
        for _, obj in self.printer.lookup_objects("AFC_lane"):
            if hasattr(obj, 'unit_name') and obj.unit_name == self.name:
                self.lanes[obj.name] = obj

    def get_status(self, eventtime=None):
        response = {}
        response['lanes'] = [lane.name for lane in self.lanes.values()]
        response["extruders"] = []
        response["hubs"] = []
        response["buffers"] = []
        return response

def load_config_prefix(config):
    return AFCUnit(config)
