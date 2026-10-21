# Support fans that are enabled when a heater is on
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import fan

PIN_MIN_TIME = 0.100

class PrinterHeaterFan:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.printer.load_object(config, 'heaters')
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.heater_names = config.getlist("heater", ("extruder",))
        self.heater_temp = config.getfloat("heater_temp", 50.0)
        self.heaters = []
        self.fan = fan.Fan(config, default_shutdown_speed=1.)
        self.fan_speed = config.getfloat("fan_speed", 1., minval=0., maxval=1.)
        self.min_speed = config.getfloat("min_speed", 0., minval=0., maxval=1.)
        self.probe_speed = config.getfloat("probe_speed", self.fan_speed, minval=0., maxval=1.)
        self.temp_speed_table = config.getlists('temp_speed_table', None, seps=(',', '\n'), count=5, parser=float)
        self.original_fan_speed = None
        self.last_speed = 0.
        self.fan_timer = None
        # Stepped fan table
        self.stepped_temp_table = config.getlists('stepped_temp_table', None, seps=(',', '\n'), count=2, parser=float)
        if self.stepped_temp_table is not None and self.temp_speed_table is not None:
            raise config.error("Cannot use both 'stepped_temp_table' and 'temp_speed_table' at the same time")
        if self.stepped_temp_table is not None:
            for j in range(1, len(self.stepped_temp_table)):
                current_temp = self.stepped_temp_table[j][0]
                prev_temp = self.stepped_temp_table[j-1][0]
                if current_temp <= prev_temp:
                    raise config.error(f"Temperature values must be ascending. Found {prev_temp} then {current_temp}")
        self.current_stepped_index = -1
        # Currently uses a fixed value, could be optimized in the future to use half the difference
        # between adjacent temperature points for more precise hysteresis control
        self.temp_hysteresis = config.getfloat("temp_hysteresis", 5, minval=0.)
        self.external_temp_guard_range = None
        self.external_temp_guard_fan_speed = None
        self.external_temp_sensor = None
        self.external_temp_in_guard_mode = False
        external_temp_sensor_name = config.get('external_temp_sensor', None)
        if external_temp_sensor_name is not None:
            self.external_temp_guard_range = config.getlists('external_temp_guard_range', seps=(',', '\n'), count=2, parser=float)
            self.external_temp_guard_fan_speed = config.getfloat('external_temp_guard_fan_speed', 1.0, minval=0., maxval=1.)
            self.external_temp_hysteresis = config.getfloat("external_temp_hysteresis", 2.5, minval=0.)
            if self.external_temp_guard_range[0][0] >= self.external_temp_guard_range[0][1]:
                raise config.error("external_temp_guard_range: min temperature must be less than max temperature")
            full_name = f"temperature_sensor {external_temp_sensor_name}"
            self.external_temp_sensor = self.printer.load_object(config, full_name)
            if not hasattr(self.external_temp_sensor, 'get_temp'):
                raise config.error(f"Object '{full_name}' does not have temperature sensor interface")

        # Register SET_HEATER_FAN command
        gcode = self.printer.lookup_object('gcode')
        self.fan_name = config.get_name().split()[1]
        gcode.register_mux_command("SET_HEATER_FAN", "FAN", self.fan_name,
                                 self.cmd_SET_HEATER_FAN,
                                 desc=self.cmd_SET_HEATER_FAN_help)
        gcode.register_mux_command("SET_PROBE_FAN", "FAN", self.fan_name,
                                 self.cmd_SET_PROBE_FAN,
                                 desc=self.cmd_SET_PROBE_FAN_help)
        gcode.register_mux_command("RESTORE_FAN", "FAN", self.fan_name,
                                 self.cmd_RESTORE_FAN,
                                 desc=self.cmd_RESTORE_FAN_help)
        self.printer.register_event_handler("inductance_coil:probe_start", self._handle_probe_start)
        self.printer.register_event_handler("inductance_coil:probe_end", self._handle_probe_end)
    def handle_ready(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heaters = [pheaters.lookup_heater(n) for n in self.heater_names]
        reactor = self.printer.get_reactor()
        self.fan_timer = reactor.register_timer(self.callback, reactor.monotonic()+PIN_MIN_TIME)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def set_probe_speed(self):
        if self.original_fan_speed is None:
            self.original_fan_speed = self.fan_speed
        self.fan_speed = self.probe_speed
        self.last_speed = self.current_stepped_index = -1  # Force update in next callback
        if self.fan_timer is not None:
            self.reactor.update_timer(self.fan_timer, self.reactor.NOW)
    def restore_fan_speed(self):
        if self.original_fan_speed is not None:
            if self.stepped_temp_table is None:
                self.fan_speed = self.original_fan_speed
            self.original_fan_speed = None
            self.last_speed = self.current_stepped_index = -1  # Force update in next callback
            if self.fan_timer is not None:
                self.reactor.update_timer(self.fan_timer, self.reactor.NOW)
    def calculate_stepped_fan_speed(self, temp):
        if not self.stepped_temp_table:
            return 0.0, 'below_min'
        if len(self.stepped_temp_table) == 1:
            temp_threshold, speed_value = self.stepped_temp_table[0]
            status = 'in_range' if temp >= temp_threshold else 'below_min'
            return (speed_value if temp >= temp_threshold else 0.0), status

        min_temp, max_temp = self.stepped_temp_table[0][0], self.stepped_temp_table[-1][0]
        if temp < min_temp:
            self.current_stepped_index = -1
            return 0.0, 'below_min'
        elif temp >= max_temp:
            self.current_stepped_index = len(self.stepped_temp_table) - 1
            speed = self.stepped_temp_table[-1][1]
            return max(self.min_speed, min(speed, 1.0)), 'above_max'
        target_index = -1
        for i, (threshold, _) in enumerate(self.stepped_temp_table):
            if temp >= threshold:
                target_index = i
            else:
                break
        if target_index > self.current_stepped_index:
            self.current_stepped_index = target_index
        elif target_index < self.current_stepped_index:
            current_temp_threshold = self.stepped_temp_table[self.current_stepped_index][0]
            if temp < (current_temp_threshold - self.temp_hysteresis):
                self.current_stepped_index = target_index
        if self.current_stepped_index >= 0:
            speed = self.stepped_temp_table[self.current_stepped_index][1]
            return max(self.min_speed, min(speed, 1.0)), 'in_range'
        else:
            return 0.0, 'below_min'
    def callback(self, eventtime):
        speed = 0.
        if self.temp_speed_table is not None:
            for rule in self.temp_speed_table:
                temp_threshold, target_temp_threshold, satisfied_heater_threshold, heater_count_threshold, rule_speed = rule
                satisfied_heaters = 0
                heater_count = 0
                for heater in self.heaters:
                    current_temp, target_temp = heater.get_temp(eventtime)
                    if target_temp > 0:
                        heater_count += 1
                    if current_temp > temp_threshold or target_temp > target_temp_threshold:
                        satisfied_heaters += 1

                if satisfied_heaters >= satisfied_heater_threshold and heater_count >= heater_count_threshold:
                    speed = max(self.min_speed, min(rule_speed, 1.0))
                    break
        else:
            if self.stepped_temp_table is not None:
                max_effective_temp = 0
                for heater in self.heaters:
                    current_temp, target_temp = heater.get_temp(eventtime)
                    effective_temp = max(current_temp, target_temp)
                    max_effective_temp = max(max_effective_temp, effective_temp)
                fan_speed, status = self.calculate_stepped_fan_speed(max_effective_temp)
                speed = max(self.min_speed, min(fan_speed, 1.0))
                if speed > 0.0 and self.original_fan_speed is not None:
                    speed = self.probe_speed
                    self.current_stepped_index = -1
            else:
                for heater in self.heaters:
                    current_temp, target_temp = heater.get_temp(eventtime)
                    if target_temp > self.heater_temp or current_temp > self.heater_temp:
                        speed = max(self.min_speed, min(self.fan_speed, 1.0))
                        break

        if self.external_temp_sensor is not None and speed > 0 and self.original_fan_speed is None:
            current_temp, target_temp = self.external_temp_sensor.get_temp(eventtime)
            min_temp, max_temp = self.external_temp_guard_range[0]
            if not self.external_temp_in_guard_mode:
                if current_temp < min_temp or current_temp > max_temp:
                    self.external_temp_in_guard_mode = True
                    speed = self.external_temp_guard_fan_speed
            else:
                guard_min_temp = min_temp + self.external_temp_hysteresis
                guard_max_temp = max_temp - self.external_temp_hysteresis
                if guard_min_temp <= current_temp <= guard_max_temp:
                    self.external_temp_in_guard_mode = False
                else:
                    speed = self.external_temp_guard_fan_speed

        if speed != self.last_speed:
            self.last_speed = speed
            curtime = self.printer.get_reactor().monotonic()
            print_time = self.fan.get_mcu().estimated_print_time(curtime)
            self.fan.set_speed(print_time + PIN_MIN_TIME, speed)
        return eventtime + 1.
    def _handle_probe_start(self):
        # Get current extruder name
        cur_extruder_name = self.printer.lookup_object('toolhead').get_extruder().get_name()
        if cur_extruder_name in self.heater_names:
            self.set_probe_speed()
    def _handle_probe_end(self):
        cur_extruder_name = self.printer.lookup_object('toolhead').get_extruder().get_name()
        if cur_extruder_name in self.heater_names:
            self.restore_fan_speed()
    cmd_SET_HEATER_FAN_help = "Set the speed of a heater fan (0.0 to 1.0)"
    def cmd_SET_HEATER_FAN(self, gcmd):
        speed = gcmd.get_float('SPEED', minval=0., maxval=1.)
        if speed > 0 and speed < self.min_speed:
            gcmd.respond_info("Error: Speed cannot be below minimum speed of %.0f%%"
                           % (self.min_speed * 100,))
            return
        self.fan_speed = speed
        self.last_speed = self.current_stepped_index = -1  # Force update in next callback
        if self.fan_timer is not None:
            self.reactor.update_timer(self.fan_timer, self.reactor.NOW)
        gcmd.respond_info("%s speed set to %.0f%%" % (self.fan_name, speed * 100,))
    cmd_SET_PROBE_FAN_help = "Set fan speed for probing"
    def cmd_SET_PROBE_FAN(self, gcmd):
        """Set fan speed for probing"""
        self.set_probe_speed()
        gcmd.respond_info("%s probe speed set to %.0f%%" % (
            self.fan_name, self.probe_speed * 100))
    cmd_RESTORE_FAN_help = "Restore original fan speed"
    def cmd_RESTORE_FAN(self, gcmd):
        """Restore original fan speed"""
        self.restore_fan_speed()
        gcmd.respond_info("%s speed restored to %.0f%%" % (
            self.fan_name, self.fan_speed * 100))

def load_config_prefix(config):
    return PrinterHeaterFan(config)
