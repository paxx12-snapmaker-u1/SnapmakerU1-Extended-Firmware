import math, logging
import stepper, chelper, coded_exception, queuefile
import os, json, copy

class ExtruderParkAction(Exception):
    pass

class ExtruderUnknownParkStatus(Exception):
    pass

class ExtruderPickAbnormal(Exception):
    pass

PERIODIC_STATUS_CHECK_INTERVAL = 0.5
DETECTION_ERROR_THRESHOLDS = {
    'park_detector_threshold': 10,
    'fan_monitor_threshold': 10,
}
MAX_ALLOWED_DIFFERENCE = 3.0
STRUCTURED_CODE_LIST = []

EXTRUDER_SWITCH_RECORDER = "extruder_switch_recorder.json"

NOZZLE_CONFIG_POSTFIX = "_nozzle_config.json"
VALID_NOZZLE_DIAMETERS = [0.2, 0.4, 0.6, 0.8]
NOZZLE_CONFIG_DEFAULT = {
    "diameter": 0.4,
}

class ExtruderSwitchRecorder:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        config_dir = self.printer.get_snapmaker_config_dir("persistent")
        self.file_path = os.path.join(config_dir, EXTRUDER_SWITCH_RECORDER)

        self.old_file_path = os.path.join(self.printer.get_snapmaker_config_dir(), EXTRUDER_SWITCH_RECORDER)
        self.save_interval = config.getfloat('save_interval', 60.0)
        self.individual_maintenance_threshold = config.getint('individual_maintenance_threshold', 25100)
        self.total_maintenance_threshold = config.getint('total_maintenance_threshold', 100000)
        self.maintenance_exception_raised = False

        self._migrate_data_if_needed()

        self.data = self._load_data()
        self.allow_save = False
        self.dirty = False

        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('GET_EXTRUDER_SWITCH_RECORDER', self.cmd_GET_EXTRUDER_SWITCH_RECORDER)
        self.gcode.register_command('RESET_EXTRUDER_SWITCH_RECORDER', self.cmd_RESET_EXTRUDER_SWITCH_RECORDER)
        self.gcode.register_command('RESET_EXTRUDER_MAINTENANCE_COUNT', self.cmd_RESET_EXTRUDER_MAINTENANCE_COUNT)

        self.timer = self.reactor.register_timer(self._on_save_timer)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

    def _handle_ready(self):
        self.allow_save = True
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def _handle_shutdown(self):
        self.allow_save = False
        self.reactor.update_timer(self.timer, self.reactor.NEVER)

    def _check_maintenance_status(self):
        """Check if maintenance is needed for any extruder or in total"""
        maintenance_needed = False
        warning_message = "Maintenance recommended for extruders:\n"

        for extruder_name in sorted(self.data.keys()):
            self._init_extruder_entry(extruder_name)
            switch_count_since_maintenance = self.data[extruder_name]['switch_count'] - self.data[extruder_name]['last_maintenance_count']
            if switch_count_since_maintenance >= self.individual_maintenance_threshold:
                maintenance_needed = True
                warning_message += f"  {extruder_name}: {switch_count_since_maintenance} switches since last maintenance\n"

        total_switch_count = sum(self.data[ext_name].get('switch_count', 0) for ext_name in self.data.keys())
        total_maintenance_count = sum(self.data[ext_name].get('last_maintenance_count', 0) for ext_name in self.data.keys())
        total_switches_since_maintenance = total_switch_count - total_maintenance_count
        if total_switches_since_maintenance >= self.total_maintenance_threshold:
            maintenance_needed = True
            warning_message += f"  Total: {total_switches_since_maintenance} switches since last maintenance (threshold: {self.total_maintenance_threshold})\n"

        if maintenance_needed:
            logging.info(f"[ExtruderSwitchRecorder] {warning_message}")

        return maintenance_needed

    def _migrate_data_if_needed(self):
        """Migrate data from old location to new location if needed"""

        if not os.path.exists(self.file_path) and os.path.exists(self.old_file_path):
            try:
                import shutil
                shutil.copy2(self.old_file_path, self.file_path)
                logging.info(f"[ExtruderSwitchRecorder] Migrated data from {self.old_file_path} to {self.file_path}")
            except Exception as e:
                logging.warning(f"[ExtruderSwitchRecorder] Failed to migrate data: {e}")

    def _load_data(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r') as f:
                    raw = f.read().strip()
                    if raw:
                        return json.loads(raw)
        except Exception as e:
            logging.warning(f"[ExtruderSwitchRecorder] Failed to load data: {e}")
        return {}

    def _write_to_file(self):
        try:
            json_content = json.dumps(self.data, indent=4)
            queuefile.async_write_file(self.file_path, json_content, safe_write=True)
            self.dirty = False
            logging.info(f"[ExtruderSwitchRecorder] Data atomically written to: {self.file_path}")
            return True
        except Exception as e:
            logging.exception(f"[ExtruderSwitchRecorder] Failed to write file: {e}")
            return False
    def _init_extruder_entry(self, extruder_name):
        if extruder_name not in self.data:

            self.data[extruder_name] = {
                'switch_count': 0,
                'retry_count': 0,
                'error_count': 0,
                'fan_error_count': 0,
                'last_maintenance_count': 0
            }
        else:

            entry = self.data[extruder_name]
            for key in ['switch_count', 'retry_count', 'error_count', 'fan_error_count', 'last_maintenance_count']:
                if key not in entry:
                    entry[key] = 0

    def _on_save_timer(self, eventtime):

        if self.dirty and self.allow_save:
            self._write_to_file()

        if not self.maintenance_exception_raised:
            need_check = True
            msm = self.printer.lookup_object('machine_state_manager', None)
            if msm is not None:
                state_str = str(msm.get_status()['main_state'])
                if state_str == "PRINTING":
                    need_check = False
            if need_check and self._check_maintenance_status():
                msg = "Maintenance recommended for extruders"
                self.printer.raise_structured_code_exception("0001-0523-0000-0037", msg, 0)
                self.maintenance_exception_raised = True
        return eventtime + self.save_interval

    def get_data(self):
        return self.data

    def add_switch_count(self, extruder_name):
        self._init_extruder_entry(extruder_name)
        self.data[extruder_name]['switch_count'] += 1
        self.dirty = True

    def add_retry_count(self, extruder_name):
        self._init_extruder_entry(extruder_name)
        self.data[extruder_name]['retry_count'] += 1
        self.dirty = True

    def add_error_count(self, extruder_name):
        self._init_extruder_entry(extruder_name)
        self.data[extruder_name]['error_count'] += 1
        self.dirty = True

    def add_fan_error_count(self, extruder_name):
        self._init_extruder_entry(extruder_name)
        self.data[extruder_name]['fan_error_count'] += 1
        self.dirty = True

    def cmd_RESET_EXTRUDER_SWITCH_RECORDER(self, gcmd):
        """G-code command to reset extruder switch recorder"""

        if not self.printer.check_extruder_config_permission():
            raise gcmd.error("Reset of extruder switch recorder is not allowed.")

        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        self.data = {}
        self._write_to_file()
        self.printer.clear_structured_code_exception("0001-0523-0000-0037")
        self.maintenance_exception_raised = False
        self.reactor.update_timer(self.timer, self.reactor.NOW)
        gcmd.respond_info("Extruder switch recorder reset")

    def cmd_RESET_EXTRUDER_MAINTENANCE_COUNT(self, gcmd):
        """G-code command to reset extruder maintenance counter to current switch count"""

        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        for ext_name in self.data.keys():
            self._init_extruder_entry(ext_name)
            self.data[ext_name]['last_maintenance_count'] = self.data[ext_name]['switch_count']
        self.dirty = True
        self.printer.clear_structured_code_exception("0001-0523-0000-0037")
        self.maintenance_exception_raised = False
        self.reactor.update_timer(self.timer, self.reactor.NOW)
        gcmd.respond_info("Reset maintenance count for all extruders: current switch counts set as new baseline")
    def cmd_GET_EXTRUDER_SWITCH_RECORDER(self, gcmd):
        data = self.get_data()
        if not data:
            gcmd.respond_info("No recorded extruder data available.")
            return
        gcmd.respond_info("dirty: {}, allow_save: {}".format(self.dirty, self.allow_save))
        gcmd.respond_info("=== Extruder Switch/Retry Data ===")
        for extruder, stats in data.items():
            gcmd.respond_info(f"Extruder '{extruder}':")
            for key, value in stats.items():
                gcmd.respond_info(f"  {key}: {value}")
            gcmd.respond_info("-----------------------------")
        gcmd.respond_info("===============================")

        gcmd.respond_info("=== Extruder Maintenance Status ===")
        gcmd.respond_info(f"Individual threshold: {self.individual_maintenance_threshold}")
        gcmd.respond_info(f"Total threshold: {self.total_maintenance_threshold}")

        total_switch_count = sum(self.data[ext_name].get('switch_count', 0) for ext_name in self.data.keys())
        total_maintenance_count = sum(self.data[ext_name].get('last_maintenance_count', 0) for ext_name in self.data.keys())
        total_switches_since_maintenance = total_switch_count - total_maintenance_count
        total_remaining = max(0, self.total_maintenance_threshold - total_switches_since_maintenance)

        gcmd.respond_info(f"Total switches: {total_switch_count}")
        gcmd.respond_info(f"Total switches since maintenance: {total_switches_since_maintenance}/{self.total_maintenance_threshold} (due in {total_remaining})")

        for ext_name in sorted(self.data.keys()):
            self._init_extruder_entry(ext_name)
            switch_count_since_maintenance = self.data[ext_name]['switch_count'] - self.data[ext_name]['last_maintenance_count']
            remaining = max(0, self.individual_maintenance_threshold - switch_count_since_maintenance)
            gcmd.respond_info(f"  {ext_name}: {switch_count_since_maintenance}/{self.individual_maintenance_threshold} (due in {remaining})")
            fan_error_count = self.data[ext_name].get('fan_error_count', 0)
            gcmd.respond_info(f"  Fan errors: {fan_error_count}")
        gcmd.respond_info("==================================")

class ExtruderStepper:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.pressure_advance = self.pressure_advance_smooth_time = 0.
        self.config_pa = config.getfloat('pressure_advance', 0., minval=0.)
        self.config_smooth_time = config.getfloat(
                'pressure_advance_smooth_time', 0.040, above=0., maxval=.200)

        self.stepper = stepper.PrinterStepper(config)
        ffi_main, ffi_lib = chelper.get_ffi()
        self.sk_extruder = ffi_main.gc(ffi_lib.extruder_stepper_alloc(),
                                       ffi_lib.extruder_stepper_free)
        self.stepper.set_stepper_kinematics(self.sk_extruder)
        self.motion_queue = None

        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        if self.name == 'extruder':
            gcode.register_mux_command("SET_PRESSURE_ADVANCE", "EXTRUDER", None,
                                       self.cmd_default_SET_PRESSURE_ADVANCE,
                                       desc=self.cmd_SET_PRESSURE_ADVANCE_help)
        gcode.register_mux_command("SET_PRESSURE_ADVANCE", "EXTRUDER",
                                   self.name, self.cmd_SET_PRESSURE_ADVANCE,
                                   desc=self.cmd_SET_PRESSURE_ADVANCE_help)
        gcode.register_mux_command("SET_EXTRUDER_ROTATION_DISTANCE", "EXTRUDER",
                                   self.name, self.cmd_SET_E_ROTATION_DISTANCE,
                                   desc=self.cmd_SET_E_ROTATION_DISTANCE_help)
        gcode.register_mux_command("SYNC_EXTRUDER_MOTION", "EXTRUDER",
                                   self.name, self.cmd_SYNC_EXTRUDER_MOTION,
                                   desc=self.cmd_SYNC_EXTRUDER_MOTION_help)
    def _handle_connect(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.register_step_generator(self.stepper.generate_steps)
        self._set_pressure_advance(self.config_pa, self.config_smooth_time)
    def get_status(self, eventtime):
        return {'pressure_advance': self.pressure_advance,
                'smooth_time': self.pressure_advance_smooth_time,
                'motion_queue': self.motion_queue}
    def find_past_position(self, print_time):
        mcu_pos = self.stepper.get_past_mcu_position(print_time)
        return self.stepper.mcu_to_commanded_position(mcu_pos)
    def sync_to_extruder(self, extruder_name):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.flush_step_generation()
        if not extruder_name:
            self.stepper.set_trapq(None)
            self.motion_queue = None
            return
        extruder = self.printer.lookup_object(extruder_name, None)
        if extruder is None or not isinstance(extruder, PrinterExtruder):
            raise self.printer.command_error("'%s' is not a valid extruder."
                                             % (extruder_name,))
        self.stepper.set_position([extruder.last_position, 0., 0.])
        self.stepper.set_trapq(extruder.get_trapq())
        self.motion_queue = extruder_name
    def _set_pressure_advance(self, pressure_advance, smooth_time):
        old_smooth_time = self.pressure_advance_smooth_time
        if not self.pressure_advance:
            old_smooth_time = 0.
        new_smooth_time = smooth_time
        if not pressure_advance:
            new_smooth_time = 0.
        toolhead = self.printer.lookup_object("toolhead")
        if new_smooth_time != old_smooth_time:
            toolhead.note_step_generation_scan_time(
                    new_smooth_time * .5, old_delay=old_smooth_time * .5)
        ffi_main, ffi_lib = chelper.get_ffi()
        espa = ffi_lib.extruder_set_pressure_advance
        toolhead.register_lookahead_callback(
            lambda print_time: espa(self.sk_extruder, print_time,
                                    pressure_advance, new_smooth_time))
        self.pressure_advance = pressure_advance
        self.pressure_advance_smooth_time = smooth_time
    cmd_SET_PRESSURE_ADVANCE_help = "Set pressure advance parameters"
    def cmd_default_SET_PRESSURE_ADVANCE(self, gcmd):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        if extruder.extruder_stepper is None:
            raise gcmd.error("Active extruder does not have a stepper")
        strapq = extruder.extruder_stepper.stepper.get_trapq()
        if strapq is not extruder.get_trapq():
            raise gcmd.error("Unable to infer active extruder stepper")
        extruder.extruder_stepper.cmd_SET_PRESSURE_ADVANCE(gcmd)
    def cmd_SET_PRESSURE_ADVANCE(self, gcmd):
        print_stats_state = None
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None:
            print_stats_state = print_stats.get_status(self.reactor.monotonic())["state"]
        print_config = self.printer.lookup_object('print_task_config', None)
        if print_config is not None and print_stats is not None and \
                print_config.print_task_config['flow_calibrate'] == True:
            if print_stats_state in ['printing', 'paused']:
                gcmd.respond_info("flow calibration enabled, so not take effect.")
                return

        pressure_advance = gcmd.get_float('ADVANCE', self.pressure_advance,
                                          minval=0.)
        smooth_time = gcmd.get_float('SMOOTH_TIME',
                                     self.pressure_advance_smooth_time,
                                     minval=0., maxval=.200)

        old_pressure_advance = self.pressure_advance
        old_smooth_time = self.pressure_advance_smooth_time
        self._set_pressure_advance(pressure_advance, smooth_time)
        virtual_sdcard = self.printer.lookup_object('virtual_sdcard', None)
        if (virtual_sdcard is not None and
            (old_pressure_advance != self.pressure_advance or
             old_smooth_time != self.pressure_advance_smooth_time)):
            virtual_sdcard.record_pl_print_pressure_advance({self.name: [self.pressure_advance, self.pressure_advance_smooth_time]})
        msg = ("pressure_advance: %.6f\n"
               "pressure_advance_smooth_time: %.6f"
               % (pressure_advance, smooth_time))
        self.printer.set_rollover_info(self.name, "%s: %s" % (self.name, msg))
        gcmd.respond_info(msg, log=False)
    cmd_SET_E_ROTATION_DISTANCE_help = "Set extruder rotation distance"
    def cmd_SET_E_ROTATION_DISTANCE(self, gcmd):
        rotation_dist = gcmd.get_float('DISTANCE', None)
        if rotation_dist is not None:
            if not rotation_dist:
                raise gcmd.error("Rotation distance can not be zero")
            invert_dir, orig_invert_dir = self.stepper.get_dir_inverted()
            next_invert_dir = orig_invert_dir
            if rotation_dist < 0.:
                next_invert_dir = not orig_invert_dir
                rotation_dist = -rotation_dist
            toolhead = self.printer.lookup_object('toolhead')
            toolhead.flush_step_generation()
            self.stepper.set_rotation_distance(rotation_dist)
            self.stepper.set_dir_inverted(next_invert_dir)
        else:
            rotation_dist, spr = self.stepper.get_rotation_distance()
        invert_dir, orig_invert_dir = self.stepper.get_dir_inverted()
        if invert_dir != orig_invert_dir:
            rotation_dist = -rotation_dist
        gcmd.respond_info("Extruder '%s' rotation distance set to %0.6f"
                          % (self.name, rotation_dist))
    cmd_SYNC_EXTRUDER_MOTION_help = "Set extruder stepper motion queue"
    def cmd_SYNC_EXTRUDER_MOTION(self, gcmd):
        ename = gcmd.get('MOTION_QUEUE')
        self.sync_to_extruder(ename)
        gcmd.respond_info("Extruder '%s' now syncing with '%s'"
                          % (self.name, ename))

class PrinterExtruder:
    def __init__(self, config, extruder_num):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.last_position = 0.
        self.reactor = self.printer.get_reactor()
        self.activating_move = False
        self.extruder_num = extruder_num
        self.unipolar_hall = False

        self.nozzle_diameter = config.getfloat('nozzle_diameter', above=0.)
        nozzle_config_name = self.name + NOZZLE_CONFIG_POSTFIX
        nozzle_config_dir = self.printer.get_snapmaker_config_dir("persistent")
        self.nozzle_config_path = os.path.join(nozzle_config_dir, nozzle_config_name)
        self.nozzle_config_info = self.printer.load_snapmaker_config_file(
            self.nozzle_config_path, NOZZLE_CONFIG_DEFAULT)
        self.nozzle_diameter = self.nozzle_config_info['diameter']

        pheaters = self.printer.load_object(config, 'heaters')
        gcode_id = 'T%d' % (extruder_num,)
        self.gcode_id = gcode_id
        self.extruder_index = extruder_num
        self.heater = pheaters.setup_heater(config, gcode_id)

        filament_diameter = config.getfloat(
            'filament_diameter', minval=self.nozzle_diameter)
        self.filament_area = math.pi * (filament_diameter * .5)**2
        def_max_cross_section = 4. * self.nozzle_diameter**2
        def_max_extrude_ratio = def_max_cross_section / self.filament_area
        max_cross_section = config.getfloat(
            'max_extrude_cross_section', def_max_cross_section, above=0.)
        self.max_extrude_ratio = max_cross_section / self.filament_area
        logging.info("Extruder max_extrude_ratio=%.6f", self.max_extrude_ratio)
        toolhead = self.printer.lookup_object('toolhead')
        max_velocity, max_accel = toolhead.get_max_velocity()
        self.max_e_velocity = config.getfloat(
            'max_extrude_only_velocity', max_velocity * def_max_extrude_ratio
            , above=0.)
        self.max_e_accel = config.getfloat(
            'max_extrude_only_accel', max_accel * def_max_extrude_ratio
            , above=0.)
        self.max_e_dist = config.getfloat(
            'max_extrude_only_distance', 50., minval=0.)
        self.instant_corner_v = config.getfloat(
            'instantaneous_corner_velocity', 1., minval=0.)

        ffi_main, ffi_lib = chelper.get_ffi()
        self.trapq = ffi_main.gc(ffi_lib.trapq_alloc(), ffi_lib.trapq_free)
        self.trapq_append = ffi_lib.trapq_append
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves

        self.extruder_stepper = None
        if (config.get('step_pin', None) is not None
            or config.get('dir_pin', None) is not None
            or config.get('rotation_distance', None) is not None):
            self.extruder_stepper = ExtruderStepper(config)
            self.extruder_stepper.stepper.set_trapq(self.trapq)

        binding_ind_coil = config.get("inductance_coil", None)
        if binding_ind_coil != None:
            self.binding_probe = \
                self.printer.lookup_object("inductance_coil {}".format(binding_ind_coil), None)
            if self.binding_probe is None:
                raise config.error("Must provide binding probe[{}] for extruder[{}]".format(binding_ind_coil, self.name))
            self.printer_probe = self.printer.lookup_object('probe', None)
            if self.printer_probe is None:
                raise config.error("Must register probe firstly!")
        else:
            self.binding_probe = None

        park_detector = config.get('park_detector', None)
        if park_detector is not None:
            self.park_detector = self.printer.lookup_object("park_detector {}".format(park_detector), None)
            if self.park_detector is None:
                raise config.error("Must provide binding park_detector[{}] for extruder[{}]".format(park_detector, self.name))
        else:
            self.park_detector = None
        self.grab_hall_sensor_type = config.getint('grab_hall_sensor_type', 1, minval=0)

        self.park_check_enable = config.getboolean('park_check_enable', False)
        self.park_detector_threshold = config.getint(
            'park_detector_threshold',
            DETECTION_ERROR_THRESHOLDS['park_detector_threshold'], minval=1)
        self.fan_speed_check_enable = config.getboolean('fan_speed_check_enable', True)
        self.fan_monitor_threshold = config.getint(
            'fan_monitor_threshold',
            DETECTION_ERROR_THRESHOLDS['fan_monitor_threshold'], minval=1)
        self.check_interval = config.getfloat('check_interval', PERIODIC_STATUS_CHECK_INTERVAL, above=0.)
        self.park_exception_cnt = 0
        self.fan_speed_exception_cnt = 0
        self.print_task_fan_error_sum = 0
        self.print_stats = None
        self.periodic_check_timer = self.reactor.register_timer(self._periodic_status_check)

        self.xy_park_position = None
        self.y_idle_position = None
        xy_park_position = config.get("xy_park_position", None)
        xy_park_position_bak = self.get_extruder_config("xy_park_position")
        if xy_park_position is not None or xy_park_position_bak is not None:
            if xy_park_position_bak is not None:
                y_idle_position_bak = self.get_extruder_config("y_idle_position")
                if y_idle_position_bak is None:
                    raise config.error("extruder_config.json, {} config error".format(self.name))
                self.xy_park_position = xy_park_position_bak
                self.y_idle_position = y_idle_position_bak
                config.getlists('xy_park_position', None, seps=(',', '\n'), count=2, parser=float)
                config.getfloat('y_idle_position', 50., minval=0.)
            else:
                xy_park_position = config.getlists('xy_park_position', seps=(',', '\n'), count=2, parser=float)
                self.xy_park_position = list(xy_park_position[0])
                self.y_idle_position = config.getfloat('y_idle_position', 50., minval=0.)
            self.grab_dir = config.getboolean('extruder_grab_dir', True)
            self.horizontal_move_x = config.getfloat('horizontal_move_x', 10., minval=0.)
            self.retract_x_dist = config.getfloat('retract_x_dist', 1.5, minval=0.)
            self.fast_move_speed = config.getfloat('fast_move_speed', 200., above=0.)
            self.slow_move_speed = config.getfloat('slow_move_speed', 30., above=0.)
            self.grab_speed = config.getfloat('grab_speed', 10., above=0.)
            self.inser_buffer_dist = config.getfloat('insertion_buffer_dist', 5., minval=0.)
            self.printer.lookup_object('gcode').register_command(gcode_id, self.cmd_SWITCH_EXTRUDER_ADVANCED)
            park_command = "PARK_{}".format(self.name).upper()
            pick_command = "PICK_{}".format(self.name).upper()
            self.printer.lookup_object('gcode').register_command(park_command, self.cmd_PARK_EXTRUDER)
            self.printer.lookup_object('gcode').register_command(pick_command, self.cmd_PICK_EXTRUDER)
            tmp_command = "MOVE_TO_XY_IDLE_POSITION_{}".format(self.name).upper()
            self.printer.lookup_object('gcode').register_command(tmp_command, self.cmd_MOVE_TO_XY_IDLE_POSITION)

        self.switch_accel = config.getfloat('switch_accel', 5000., above=0.)
        self.retry_switch_limit = config.getint('retry_switch_limit', 3, minval=0)

        self.binding_fan = None
        fan = config.get("fan", None)
        if fan is not None and self.printer.lookup_object("{}".format(fan), None) is not None:
            self.binding_fan = self.printer.lookup_object("{}".format(fan)).fan
        self.switch_extruder_ctr_fan_pwm = config.getboolean('switch_extruder_ctr_fan_pwm', True)

        self.base_position = None
        self.gcode_offset = None
        base_position_bak = self.get_extruder_config("base_position")
        if config.get('base_position', None) is not None or base_position_bak is not None:
            if base_position_bak is not None:
                self.base_position = base_position_bak
                base_position = config.getlists('base_position', None, seps=(',', '\n'), parser=float)
            else:
                base_position = config.getlists('base_position', seps=(',', '\n'), parser=float)
                if base_position is not None and len(base_position[0]) == 3:
                    self.base_position = [base_position[0][i] for i in range(0, 3)]
        self.vref_sw = None
        if config.get('vref_sw_pin', None) is not None:
            self.vref_sw = self.printer.lookup_object("output_pin {}".format(config.get('vref_sw_pin')), None)
        self.print_config = self.printer.lookup_object('print_task_config', None)
        self.printing_e_pos = 0.0

        self.is_calibrating_flow = False
        self.printer.register_event_handler("flow_calibration:begin", self._handle_flow_calibration_begin)
        self.printer.register_event_handler("flow_calibration:end", self._handle_flow_calibration_end)
        self.printer.register_event_handler("virtual_sdcard:reset_file", self._handle_flow_calibration_end)

        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler("probe_inductance_coil: update_extruder_offset", self._update_extruder_offset)
        self.printer.register_event_handler("print_stats:new_task_start", self._handle_new_task_start)

        gcode = self.printer.lookup_object('gcode')
        wh = self.printer.lookup_object('webhooks')
        if self.name == 'extruder':
            toolhead.set_extruder(self, 0.)
            self.gcode_offset = [0, 0, 0]
            gcode.register_command("M104", self.cmd_M104)
            gcode.register_command("M109", self.cmd_M109)
            gcode.register_command("SET_MAX_E_ACCEL", self.cmd_SET_MAX_E_ACCEL)
            gcode.register_command("SET_MAX_E_VELOCITY", self.cmd_SET_MAX_E_VELOCITY)
            gcode.register_command("ENTER_PARK_POINT_MANUAL_CALIBRATION", self.cmd_ENTER_PARK_POINT_MANUAL_CALIBRATION)
            gcode.register_command("EXIT_PARK_POINT_MANUAL_CALIBRATION", self.cmd_EXIT_PARK_POINT_MANUAL_CALIBRATION)
            wh.register_endpoint("control/extruder_temp", self._handle_control_extruder_temp)
            wh.register_endpoint("control/nozzle_diameter", self._handle_control_nozzle_diameter)
            if self.park_detector is not None:
                gcode.register_command("GET_EXTRUDER_ACTIVATE_INFO", self.cmd_GET_EXTRUDER_ACTIVATE_INFO)
            self.printer.register_event_handler('print_stats:stop', self._handle_stop_print_job)
        gcode.register_mux_command("ACTIVATE_EXTRUDER", "EXTRUDER",
                                   self.name, self.cmd_ACTIVATE_EXTRUDER,
                                   desc=self.cmd_ACTIVATE_EXTRUDER_help)
        gcode.register_mux_command("INNER_APPLY_FLOW_K", "EXTRUDER",
                            self.name, self.cmd_INNER_APPLY_FLOW_K)
        gcode.register_mux_command("SET_EXTRUDER_BASE_POSITION", "EXTRUDER",
                                   self.name, self.cmd_SET_EXTRUDER_BASE_POSITION,
                                   desc="Set extruder base position")
        gcode.register_mux_command("SET_EXTRUDER_PARK_POSITION", "EXTRUDER",
                                   self.name, self.cmd_SET_EXTRUDER_PARK_POSITION,
                                   desc="Set extruder park position")
        gcode.register_mux_command("MOVE_TO_PARK_CALIBRATION_POINT", "EXTRUDER",
                                   self.name, self.cmd_MOVE_TO_PARK_CALIBRATION_POINT)
        gcode.register_mux_command("VERIFY_PARK_POSITION", "EXTRUDER",
                                   self.name, self.cmd_VERIFY_PARK_POSITION)
        gcode.register_mux_command("SET_NOZZLE_DIAMETER", "EXTRUDER",
                                   self.name, self.cmd_SET_NOZZLE_DIAMETER)
        self.gcode = gcode
    def _handle_connect(self):
        self.update_extruder_gcode_offset()
    def _handle_ready(self):
        self.print_stats = self.printer.lookup_object('print_stats', None)
        if self.park_check_enable or self.fan_speed_check_enable:
            self.reactor.update_timer(self.periodic_check_timer, self.reactor.NOW)
    def _handle_shutdown(self):
        self.reactor.update_timer(self.periodic_check_timer, self.reactor.NEVER)
    def _handle_new_task_start(self):
        self.fan_speed_exception_cnt = 0
        self.print_task_fan_error_sum = 0
    def _handle_stop_print_job(self):

        extruder_list = self.printer.lookup_object('extruder_list', [])
        fan_error_info = {}
        fan_error_counts = []
        all_fan_error_sum = 0
        for extruder in extruder_list:
            fan_error_sum = getattr(extruder, 'print_task_fan_error_sum', 0)
            fan_error_counts.append(fan_error_sum)
            if fan_error_sum != 0:
                fan_error_info[extruder.name] = fan_error_sum
                all_fan_error_sum += fan_error_sum
        if fan_error_info and all_fan_error_sum >= 2:
            logging.info(f"Fan error summary: {fan_error_info}")
            exception_manager = self.printer.lookup_object('exception_manager', None)
            if exception_manager is not None:
                msg = f"Detected pogopin disconnection during print\nPlease clean and retry    {fan_error_counts}"
                exception_manager.raise_exception_async(
                    id=523, index=0, code=48, message=msg, oneshot=1, level=1)
    def _handle_flow_calibration_end(self):
        logging.info("extruder: end flow calibration")
        self.is_calibrating_flow = False
    def _handle_flow_calibration_begin(self):
        logging.info("extruder: begin flow calibration")
        self.is_calibrating_flow = True
    def _update_extruder_offset(self):
        self.update_extruder_gcode_offset()
        self.active_gcode_offset()
    def active_binding_probe(self):
        if self.binding_probe is None:
            return
        logging.info("active new eddy current probe: {}".format(
            self.binding_probe.name))
        self.printer_probe.set_mcu_probe(self.binding_probe)
    def active_binding_fan(self):
        if self.binding_fan is None:
            return
        print_fan = self.printer.lookup_object('fan', None)
        if print_fan is not None:
            print_fan.fan = self.binding_fan

    def get_park_detector_status(self):
        if self.park_detector is not None:
            return self.park_detector.get_park_detector_status()
        else:
            return None
    def set_park_detector_enable(self, enable):
        if self.park_detector is not None:
            self.park_exception_cnt = 0
            self.park_check_enable = not not enable
    def update_move_time(self, flush_time, clear_history_time):
        self.trapq_finalize_moves(self.trapq, flush_time, clear_history_time)
    def active_gcode_offset(self):
        if self.base_position is None or self.name == 'extruder':
            return
        gcode = self.printer.lookup_object('gcode')
        if self.gcode_offset is not None:
            gcode.run_script_from_command("SET_GCODE_OFFSET X=%f Y=%f Z=%f" % (self.gcode_offset[0], self.gcode_offset[1], self.gcode_offset[2]))
    def update_extruder_gcode_offset(self):

        if self.name == 'extruder':
            return
        extruder = self.printer.lookup_object('extruder', None)
        if extruder is not None:
            if extruder.base_position is not None and self.base_position is not None:
                self.gcode_offset = [self.base_position[i] - extruder.base_position[i] for i in range(0, 3)]
    def get_status(self, eventtime):
        sts = self.heater.get_status(eventtime)
        sts['can_extrude'] = bool(self.heater.can_extrude)
        sts['extruder_index'] = self.extruder_index
        sts['nozzle_diameter'] = self.nozzle_diameter
        sts['printing_e_pos'] = self.printing_e_pos
        sts['activating_move'] = self.activating_move
        if self.park_detector is not None:
            sts.update(self.get_park_detector_status())
            sts['real_extruder_stats'] = self.get_extruder_activate_status()[0]
        sts['extruder_offset'] = [0, 0, 0] if self.gcode_offset is None else self.gcode_offset
        switch_recorder = self.printer.lookup_object('extruder_switch_recorder', None)
        if switch_recorder is not None:
            recorder_data = switch_recorder.get_data()
            extruder_entry = recorder_data.get(self.name, {})
            sts['switch_count'] = extruder_entry.get('switch_count', 0)
            sts['retry_count'] = extruder_entry.get('retry_count', 0)
            sts['error_count'] = extruder_entry.get('error_count', 0)
            sts['last_maintenance_count'] = extruder_entry.get('last_maintenance_count', 0)
        if self.extruder_stepper is not None:
            sts.update(self.extruder_stepper.get_status(eventtime))
        return sts
    def get_name(self):
        return self.name
    def get_heater(self):
        return self.heater
    def get_trapq(self):
        return self.trapq
    def stats(self, eventtime):
        return self.heater.stats(eventtime)
    def check_move(self, move):
        axis_r = move.axes_r[3]
        if not self.heater.can_extrude:
            coded = f"0003-0523-{self.extruder_num:04d}-0001"
            msg = f"{self.name} below minimum temp, temperature: {self.heater.smoothed_temp:.2f}, See the 'min_extrude_temp' config option for details"
            coded_msg = '{"coded":"%s", "msg":"%s"}' % (coded, msg)
            raise self.printer.command_error(coded_msg)
        if (not move.axes_d[0] and not move.axes_d[1]) or axis_r < 0.:

            if abs(move.axes_d[3]) > self.max_e_dist:
                coded = f"0003-0523-{self.extruder_num:04d}-0004"
                msg = "Extrude only move too long (%.3fmm vs %.3fmm), See the 'max_extrude_only_distance' config option for details" % (move.axes_d[3], self.max_e_dist)
                coded_msg = '{"coded":"%s", "msg":"%s"}' % (coded, msg)
                raise self.printer.command_error(coded_msg)

            inv_extrude_r = 1. / abs(axis_r)
            move.limit_speed(self.max_e_velocity * inv_extrude_r,
                             self.max_e_accel * inv_extrude_r)
        elif axis_r > self.max_extrude_ratio:
            if move.axes_d[3] <= self.nozzle_diameter * self.max_extrude_ratio:

                return
            area = axis_r * self.filament_area
            logging.debug("Overextrude: %s vs %s (area=%.3f dist=%.3f)",
                          axis_r, self.max_extrude_ratio, area, move.move_d)
            coded = f"0003-0523-{self.extruder_num:04d}-0005"
            msg = "Move exceeds maximum extrusion (%.3fmm^2 vs %.3fmm^2)\\n" % (area, self.max_extrude_ratio * self.filament_area)
            msg +="See the 'max_extrude_cross_section' config option for details"
            coded_msg = '{"coded":"%s", "msg":"%s"}' % (coded, msg)
            raise self.printer.command_error(coded_msg)

    def calc_junction(self, prev_move, move):
        diff_r = move.axes_r[3] - prev_move.axes_r[3]
        if diff_r:
            return (self.instant_corner_v / abs(diff_r))**2
        return move.max_cruise_v2
    def move(self, print_time, move):
        axis_r = move.axes_r[3]
        accel = move.accel * axis_r
        start_v = move.start_v * axis_r
        cruise_v = move.cruise_v * axis_r
        can_pressure_advance = False
        if axis_r > 0. and ((move.axes_d[0] or move.axes_d[1]) or
                self.is_calibrating_flow):
            can_pressure_advance = True

        self.trapq_append(self.trapq, print_time,
                          move.accel_t, move.cruise_t, move.decel_t,
                          move.start_pos[3], 0., 0.,
                          1., can_pressure_advance, 0.,
                          start_v, cruise_v, accel, move.line)
        self.last_position = move.end_pos[3]
    def find_past_position(self, print_time):
        if self.extruder_stepper is None:
            return 0.
        return self.extruder_stepper.find_past_position(print_time)
    def check_homing(self):
        curtime = self.printer.get_reactor().monotonic()
        homed_axes_list = self.printer.lookup_object('toolhead').get_status(curtime)['homed_axes']
        return ('x' in homed_axes_list and 'y' in homed_axes_list and 'z' in homed_axes_list)
    def check_xy_homing(self):
        curtime = self.printer.get_reactor().monotonic()
        homed_axes_list = self.printer.lookup_object('toolhead').get_status(curtime)['homed_axes']
        return ('x' in homed_axes_list and 'y' in homed_axes_list)
    def get_extruder_activate_status(self):
        toolhead = self.printer.lookup_object('toolhead')
        extruder_info = ['unknown extruder', 2]
        extruder_park = pin_sta = None
        if self.park_detector is not None:
            extruder_list = self.printer.lookup_object('extruder_list', [])
            state_list = [extruder_list[i].get_park_detector_status() for i in range(len(extruder_list))]
            extruder_park = [state_list[i]['state'] for i in range(len(state_list))]
            pin_sta = [[state_list[i]['park_pin'], state_list[i]['active_pin'], state_list[i]['grab_valid_pin']] for i in range(len(state_list))]
            extruder_park_num = extruder_active_num = 0
            if 'PARKED' in extruder_park:
                extruder_park_num = extruder_park.count('PARKED')
            if 'ACTIVATE' in extruder_park:
                extruder_active_num = extruder_park.count('ACTIVATE')

            if extruder_park_num == len(extruder_park):
                extruder_info = [toolhead.get_extruder().name, 1]
            elif extruder_active_num == 1 and extruder_park_num + 1 == len(extruder_park):
                if 'ACTIVATE' in extruder_park:
                    index =  extruder_park.index('ACTIVATE')
                    extruder_info = [extruder_list[index].name, 0]
        else:
            extruder_info = [toolhead.get_extruder().name, 0]
        return [extruder_info, extruder_park, pin_sta]
    def check_allow_retry_switch_extruder(self):
        def check_retry_extruder(status_list):
            if not status_list:
                return False, -1
            unknown_indices = [i for i, s in enumerate(status_list) if s == 'UNKNOWN']
            if len(status_list) == 1:
                is_valid = len(unknown_indices) == 1
                return (is_valid, unknown_indices[0] if is_valid else -1)
            valid = len(unknown_indices) == 1 and all(s == 'PARKED' for s in status_list if s != 'UNKNOWN')
            return (valid, unknown_indices[0]) if valid else (False, -1)

        activate_status = self.get_extruder_activate_status()
        if not activate_status or len(activate_status) < 3:
            return None

        first_item = activate_status[0]
        extruder_status_list = activate_status[1]

        if not isinstance(first_item, (list, tuple)) or len(first_item) < 2 or first_item[1] != 2:
            return None

        if not isinstance(extruder_status_list, list):
            return None

        allow_retry, retry_extruder_index = check_retry_extruder(extruder_status_list)
        if allow_retry and isinstance(activate_status[2], list) and len(activate_status[2]) > retry_extruder_index:
            pin_status = activate_status[2][retry_extruder_index]
            park_pin = pin_status[0]
            active_pin = pin_status[1]
            grab_valid_pin = pin_status[2]
            if park_pin is False and active_pin is False and grab_valid_pin:
                return retry_extruder_index
        return None

    def set_vref_sw(self, value):
        if self.vref_sw is not None:
            toolhead = self.printer.lookup_object('toolhead')
            self.vref_sw._set_pin(toolhead.get_last_move_time(), value)
    def only_enable_current_extruder_vref_sw(self):
        toolhead = self.printer.lookup_object('toolhead')
        extruder_list = self.printer.lookup_object('extruder_list', [])
        for i in range(len(extruder_list)):
            if extruder_list[i].vref_sw is not None:
                if extruder_list[i].name == self.name:
                    extruder_list[i].set_vref_sw(1)
                else:
                    extruder_list[i].set_vref_sw(0)
    def get_extruder_config(self, field_name=None):
        extruder_bak = self.printer.lookup_object('extruder_config_bak', None)
        if extruder_bak is None:
            return None
        return extruder_bak.get_extruder_config(self.name, field_name)

    def update_extruder_config(self, field_name=None, value=None):
        extruder_bak = self.printer.lookup_object('extruder_config_bak', None)
        if extruder_bak is None:
            return False
        return extruder_bak.update_extruder_config(self.name, field_name, value)

    def analyze_switch_extruder_error(self, err_state=None):
        try:
            if err_state is None:
                err_state = self.get_extruder_activate_status()

            if not isinstance(err_state, list) or len(err_state) < 3:
                logging.info("Invalid state format: %s", str(err_state))
                return None

            if not (isinstance(err_state[0], list) and len(err_state[0]) > 1):
                logging.info("Invalid header format: %s", str(err_state[0]))
                return None

            if err_state[0][1] != 2:
                return None

            states = err_state[1]
            grips = err_state[2] if len(err_state) > 2 else []

            if len(states) != len(grips):
                logging.info("State/grip mismatch: states=%d, grips=%d", len(states), len(grips))
                return None

            MAX_EXTRUDERS = 4
            if len(states) > MAX_EXTRUDERS:
                logging.warning("Too many extruders: %d, exceeds maximum: %d", len(states), MAX_EXTRUDERS)
                return None

            fmt_grip = lambda g: ''.join('T' if x else 'F' for x in g) if isinstance(g, list) else "?"

            activated = []
            unknown = []
            error_messages = []
            all_grip_states = []

            for i, s in enumerate(states):
                if s == 'ACTIVATE':
                    activated.append(i)
                elif s == 'UNKNOWN':
                    unknown.append(i)

                all_grip_states.append(fmt_grip(grips[i]))

            activated_code = 0
            unknown_code = 0

            if len(activated) > 1:
                grip_info = [f"{i}-({all_grip_states[i]})" for i in activated]
                error_messages.append(f"multi-act: {grip_info}")

                activated_sorted = sorted(activated)
                extruder_combinations = {
                    (0, 1): 0,
                    (0, 2): 1,
                    (0, 3): 2,
                    (1, 2): 3,
                    (1, 3): 4,
                    (2, 3): 5,
                    (0, 1, 2): 6,
                    (0, 1, 3): 7,
                    (0, 2, 3): 8,
                    (1, 2, 3): 9,
                    (0, 1, 2, 3): 10
                }

                combination_index = extruder_combinations.get(tuple(activated_sorted))
                if combination_index is not None:
                    activated_code = combination_index + 1

            if unknown:
                unknown_info = []
                for idx in unknown:
                    grip = grips[idx]
                    if isinstance(grip, list) and len(grip) >= 2:
                        if len(grip) >= 3 and self.unipolar_hall:
                            if not grip[0] and not grip[1]:
                                if grip[2]:
                                    unknown_info.append(f"{idx}-(pogopin unconnected)")
                                else:
                                    unknown_info.append(f"{idx}-(extruder detached)")
                            elif grip[0] and grip[1]:
                                unknown_info.append(f"{idx}-({all_grip_states[idx]})")
                            else:
                                unknown_info.append(f"{idx}-({all_grip_states[idx]})")
                        else:
                            unknown_info.append(f"{idx}-({all_grip_states[idx]})")
                    else:
                        unknown_info.append(f"{idx}-({all_grip_states[idx]})")
                error_messages.append(f"err-sta: {unknown_info}")

                unknown_sorted = sorted(unknown)
                unknown_combinations = {
                    (0,): 0,
                    (1,): 1,
                    (2,): 2,
                    (3,): 3,
                    (0, 1): 4,
                    (0, 2): 5,
                    (0, 3): 6,
                    (1, 2): 7,
                    (1, 3): 8,
                    (2, 3): 9,
                    (0, 1, 2): 10,
                    (0, 1, 3): 11,
                    (0, 2, 3): 12,
                    (1, 2, 3): 13,
                    (0, 1, 2, 3): 14
                }

                unknown_index = unknown_combinations.get(tuple(unknown_sorted))
                if unknown_index is not None:
                    unknown_code = unknown_index + 1

            if error_messages:
                return (
                    "; ".join(error_messages),
                    activated,
                    unknown,
                    all_grip_states,
                    activated_code,
                    unknown_code
                )
            return None

        except Exception as e:
            logging.exception("Error analyzing extruder state: %s", str(e))
            return None

    def _periodic_status_check(self, eventtime):

        check_interval = None
        if self.print_stats is not None:
            toolhead = self.printer.lookup_object('toolhead')
            is_grab_complete = toolhead.get_grab_complete()
            if self.fan_speed_check_enable and self.binding_fan is not None:
                check_interval = self.check_interval
                if (self.print_stats.state == "printing"
                        and is_grab_complete
                        and toolhead.get_extruder() is self):
                    fan_info = self.binding_fan.get_status(eventtime)
                    fan_speed = fan_info.get('speed', 0.0)
                    fan_rpm = fan_info.get('rpm')
                    if fan_speed > 0 and fan_rpm is not None and fan_rpm == 0:
                        if self.fan_speed_exception_cnt < self.fan_monitor_threshold:
                            self.fan_speed_exception_cnt += 1
                            if self.fan_speed_exception_cnt >= self.fan_monitor_threshold:
                                self.print_task_fan_error_sum += 1
                                switch_recorder = self.printer.lookup_object('extruder_switch_recorder', None)
                                if switch_recorder is not None:
                                    switch_recorder.add_fan_error_count(self.name)
                    else:
                        if self.fan_speed_exception_cnt < self.fan_monitor_threshold:
                            self.fan_speed_exception_cnt = 0
                else:
                    self.fan_speed_exception_cnt = 0
        if check_interval is None:
            return self.reactor.NEVER
        return eventtime + check_interval
    def _add_structured_code_list(self, e):
        global STRUCTURED_CODE_LIST
        try:
            exception_manager = self.printer.lookup_object('exception_manager', None)
            if exception_manager is None:
                logging.warning("Exception manager not found when adding structured code")
                return

            exc_obj = coded_exception.CodedException.from_exception(e)
            if exc_obj is None:
                logging.warning("Failed to create CodedException from exception")
                return

            id = exc_obj.id
            index = exc_obj.index
            code = exc_obj.code
            message = exc_obj.message
            oneshot = exc_obj.oneshot

            if message:
                try:
                    coded_message = self.printer.extract_encoded_message(message)
                    if coded_message:
                        structured_code = coded_message.get("coded")
                        if structured_code:
                            parsed = exception_manager._parse_structured_code(structured_code)
                            id = parsed.get("id", id)
                            index = parsed.get("index", index)
                            code = parsed.get("code", code)
                            level = parsed.get("level", exc_obj.level)
                        else:
                            id = coded_message.get("id", id)
                            index = coded_message.get("index", index)
                            code = coded_message.get("code", code)
                            level = coded_message.get("level", exc_obj.level)
                        oneshot = coded_message.get("oneshot", oneshot)
                    if id == 523 and oneshot == 0:
                        structured_code = f"0002-{id:04d}-{index:04d}-{code:04d}"
                        if structured_code not in STRUCTURED_CODE_LIST:
                            STRUCTURED_CODE_LIST.append(structured_code)
                except Exception as parse_err:
                    logging.warning(f"Error parsing structured code: {str(parse_err)}")

        except Exception as err:
            logging.error(f"Error in _add_structured_code_list: {str(err)}")

    def _clear_structured_code_list(self):
        global STRUCTURED_CODE_LIST
        if STRUCTURED_CODE_LIST:
            for code in STRUCTURED_CODE_LIST:
                self.printer.clear_structured_code_exception(code)
            STRUCTURED_CODE_LIST = []

    def _set_extruder_temp(self, temp, index, map, wait=False):
        if map != 0 and index is not None and self.print_config is not None:
            index = self.print_config.get_extruder_map_index(index)
        if index is not None:
            section = 'extruder'
            if index:
                section = 'extruder%d' % (index,)
            extruder = self.printer.lookup_object(section, None)
            if extruder is None:
                if temp <= 0.:
                    return
                raise self.printer.command_error('{"coded":"0001-0523-0000-0006", "msg":"%s not configured"}' % (section))
        else:
            extruder = self.printer.lookup_object('toolhead').get_extruder()
        pheaters = self.printer.lookup_object('heaters')
        pheaters.set_temperature(extruder.get_heater(), temp, wait)

    def _handle_control_extruder_temp(self, web_request):
        """Handle extruder temperature setting request"""
        try:
            temp = web_request.get_float('S', 0.)
            index = web_request.get_int('T', None)
            extruder_map = web_request.get_int('A', 1)
            if temp < 0:
                temp = 0
            self._set_extruder_temp(temp, index, extruder_map, False)
            web_request.send({'state': 'success'})
        except Exception as e:
            logging.error(f'failed to set extruder temp: {str(e)}')
            web_request.send({'state': 'error', 'message': str(e)})

    def _set_nozzle_diameter(self, diameter):
        self.nozzle_diameter = diameter
        self.nozzle_config_info['diameter'] = diameter
        if not self.printer.update_snapmaker_config_file(self.nozzle_config_path, self.nozzle_config_info):
            logging.error("failed to save nozzle diameter config")

    def _handle_control_nozzle_diameter(self, web_request):
        try:
            extruder_index = web_request.get_int('extruder', None)
            diameter = web_request.get_float('diameter')
            if diameter not in VALID_NOZZLE_DIAMETERS:
                raise web_request.error(
                    "invalid nozzle diameter %s (allowed: %s)" % (
                        diameter, VALID_NOZZLE_DIAMETERS))
            extruder_list = self.printer.lookup_object('extruder_list', [])
            targets = []
            if extruder_index is None:
                targets = list(extruder_list)
            else:
                for ex in extruder_list:
                    if getattr(ex, 'extruder_index', None) == extruder_index:
                        targets.append(ex)
                if not targets:
                    raise web_request.error("extruder %d not found" % extruder_index)
            for ex in targets:
                ex._set_nozzle_diameter(diameter)
            web_request.send({'state': 'success'})
        except Exception as e:
            logging.error(f'failed to set nozzle diameter: {str(e)}')
            web_request.send({'state': 'error', 'message': str(e)})

    cmd_SET_NOZZLE_DIAMETER_help = "Set the persisted nozzle diameter for this extruder"
    def cmd_SET_NOZZLE_DIAMETER(self, gcmd):
        diameter = gcmd.get_float('DIAMETER')
        if diameter not in VALID_NOZZLE_DIAMETERS:
            raise gcmd.error("invalid nozzle diameter %s (allowed: %s)" % (
                diameter, VALID_NOZZLE_DIAMETERS))
        self._set_nozzle_diameter(diameter)
        gcmd.respond_info("Nozzle diameter for %s set to %.1fmm" % (self.name, diameter))

    def cmd_M104(self, gcmd, wait=False):

        temp = gcmd.get_float('S', 0.)
        index = gcmd.get_int('T', None, minval=0)
        extruder_map = gcmd.get_int('A', 1, minval=0)
        try:
            self._set_extruder_temp(temp, index, extruder_map, wait)
        except Exception as e:
            raise gcmd.error(str(e))
    def cmd_M109(self, gcmd):

        self.cmd_M104(gcmd, wait=True)
    cmd_ACTIVATE_EXTRUDER_help = "Change the active extruder"
    def cmd_ACTIVATE_EXTRUDER(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        self.only_enable_current_extruder_vref_sw()
        if toolhead.get_extruder() is self:
            gcmd.respond_info("Extruder %s already active" % (self.name,))
            return
        gcmd.respond_info("Activating extruder %s" % (self.name,))
        toolhead.flush_step_generation()
        toolhead.set_extruder(self, self.last_position)
        self.printer.send_event("extruder:activate_extruder")
        self.active_binding_probe()
        self.active_binding_fan()
        self.active_gcode_offset()
    def set_max_accel(self, accel):
        self.max_e_accel = accel
    def get_max_accel(self):
        return self.max_e_accel
    def cmd_SET_MAX_E_ACCEL(self, gcmd):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        accel = gcmd.get_float('A', extruder.max_e_accel)
        extruder.max_e_accel = accel
        gcmd.respond_info("{}'s max accel: {}".format(extruder.name, accel))
    def cmd_SET_MAX_E_VELOCITY(self, gcmd):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        velocity = gcmd.get_float('V', extruder.max_e_velocity)
        extruder.max_e_velocity = velocity
        gcmd.respond_info("{}'s max velocity: {}".format(extruder.name, velocity))
    def cmd_GET_EXTRUDER_ACTIVATE_INFO(self, gcmd):
        if self.park_detector is not None:
            activate_status = self.get_extruder_activate_status()
            gcmd.respond_info("{} grab_complete: {}".format(activate_status, self.printer.lookup_object('toolhead').is_grab_complete))
    def cmd_PARK_EXTRUDER(self, gcmd):
        gcmd.get_command_parameters()['ACTION'] = 'PARK'
        self.cmd_SWITCH_EXTRUDER(gcmd)

    def cmd_PICK_EXTRUDER(self, gcmd):
        gcmd.get_command_parameters()['ACTION'] = 'PICK'
        self.cmd_SWITCH_EXTRUDER(gcmd)
    def cmd_SWITCH_EXTRUDER_ADVANCED(self, gcmd):
        gcmd.get_command_parameters()['ACTION'] = None
        extruder_map = gcmd.get_int('A', 1, minval=0)

        if extruder_map != 0 and self.print_config is not None:
            index = int(self.gcode_id.split('T')[1])
            index = self.print_config.get_extruder_map_index(index)
            section = 'extruder'
            if index:
                section = 'extruder%d' % (index,)
            extruder = self.printer.lookup_object(section, None)
            if extruder is not None:
                extruder.cmd_SWITCH_EXTRUDER(gcmd)
        else:
            self.cmd_SWITCH_EXTRUDER(gcmd)
    def cmd_SWITCH_EXTRUDER(self, gcmd):
        retry_count = 0
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        switch_recorder = self.printer.lookup_object('extruder_switch_recorder', None)
        def handle_retry(error_msg_prefix):
            nonlocal retry_count
            nonlocal switch_recorder
            retry_count += 1
            if retry_count < self.retry_switch_limit:
                gcmd.respond_info(f"Failed to switch extruder, retrying... (Attempt: {retry_count})")
                return True
            else:
                retry_extruder_id = self.check_allow_retry_switch_extruder()
                if "pogopin" in error_msg_prefix and retry_extruder_id is not None:
                    try:
                        self._cmd_SWITCH_EXTRUDER(gcmd, forced_park=True)
                    except Exception as inner_e:
                        logging.warning("Inner exception during forced park: %s", str(inner_e))

                if switch_recorder is not None:
                    switch_recorder.add_error_count(self.name)
                raise gcmd.error(error_msg_prefix, action="pause")

        try:
            while retry_count < self.retry_switch_limit:
                try:
                    if retry_count > 0:

                        retry_extruder_id = self.check_allow_retry_switch_extruder()
                        if retry_extruder_id is not None:

                            if switch_recorder is not None:
                                extruder_list = self.printer.lookup_object('extruder_list', [])
                                if len(extruder_list) > retry_extruder_id:
                                    switch_recorder.add_retry_count(extruder_list[retry_extruder_id].name)
                            self._cmd_SWITCH_EXTRUDER(gcmd, forced_park=True)
                    self._cmd_SWITCH_EXTRUDER(gcmd)
                    break

                except ExtruderUnknownParkStatus as e:
                    handle_retry(str(e))
                except ExtruderPickAbnormal as e:
                    handle_retry(str(e))
                except Exception as e:
                    retry_extruder_id = self.check_allow_retry_switch_extruder()
                    if "pogopin" in str(e) and retry_extruder_id is not None:
                        try:
                            self._cmd_SWITCH_EXTRUDER(gcmd, forced_park=True)
                        except Exception as inner_e:
                            logging.warning("Inner exception during forced park: %s", str(inner_e))

                    if switch_recorder is not None:
                        switch_recorder.add_error_count(self.name)
                    raise
            self._clear_structured_code_list()
        except Exception as e:
            self._add_structured_code_list(e)
            if hasattr(toolhead.get_kinematics(), "note_x_not_homed"):
                toolhead.get_kinematics().note_x_not_homed()
            if hasattr(toolhead.get_kinematics(), "note_y_not_homed"):
                toolhead.get_kinematics().note_y_not_homed()
            raise

    def _cmd_SWITCH_EXTRUDER(self, gcmd, forced_park=False):
        switch_complete = restore_state = False
        toolhead = self.printer.lookup_object('toolhead')
        gcode = self.printer.lookup_object('gcode')
        gcode_move = self.printer.lookup_object('gcode_move')
        extruder_list = self.printer.lookup_object('extruder_list', [])
        activate_status = None
        fan_pwm_set = False
        action = None
        params = gcmd.get_command_parameters()
        is_grab_complete = False
        ace = self.printer.lookup_object('ace')
        if 'ACTION' in params:
            action = params['ACTION']

        if forced_park:
            action = 'PARK'

        try:
            if (self.printer.lookup_object('homing_xyz_override', None) is not None or
                self.printer.lookup_object('safe_z_home', None) is not None):
                if not self.check_xy_homing():
                    gcode.run_script_from_command("G28 X Y")
            else:
                if not self.check_homing():
                    gcode.run_script_from_command("G28")
            toolhead.wait_moves()
            self.activating_move = True
            gcmd.respond_info("{} -> {}".format(toolhead.get_extruder().name, self.name))
            forced_skip = False
            retry_extruder_id = None
            for i in range(10):
                activate_status = self.get_extruder_activate_status()
                retry_extruder_id = self.check_allow_retry_switch_extruder()
                if forced_park and retry_extruder_id is not None:
                    forced_skip = True
                if activate_status[0][1] != 2 or forced_skip:
                    break
                else:
                    if i == 9:
                        result = self.analyze_switch_extruder_error(activate_status)
                        if result:
                            error_info, activated, unknown, grip_states, activated_code, unknown_code = result
                            error_msg = "Extruder %s switch is not allowed, %s" % (self.name, error_info)
                            if "multi-act" in error_info:
                                if activated_code + 6 < 18:
                                    message = '{"coded": "0002-0523-%4d-%4d", "oneshot": %d, "msg":"%s", "action": "pause"}' % (self.extruder_num, activated_code + 6, 0, error_msg)
                            else:
                                message = '{"coded": "0002-0523-%4d-0018", "oneshot": %d, "msg":"%s", "action": "pause"}' % (self.extruder_num, 0, error_msg)
                                if self.grab_hall_sensor_type:
                                    first_unknown_index = None
                                    for i, idx in enumerate(unknown):
                                        if grip_states[idx] != 'FFT':
                                            first_unknown_index = idx
                                            break

                                    if first_unknown_index is not None:
                                        grip_state = grip_states[first_unknown_index]
                                        if grip_state == 'FFF' or grip_state == 'FFT':
                                            info = "Extruder %s is not allowed to switch, detected that extruder%d is detached. %s" % (self.name, first_unknown_index, error_info)
                                            message = '{"coded": "0002-0523-%4d-0040", "oneshot": %d, "msg":"%s", "action": "pause"}' % (first_unknown_index, 0, info)
                                        elif (grip_state == 'TTF' or grip_state == 'TTT'):
                                            info = "Extruder %s is not allowed to switch, detected conflicting status for extruder%d: both parked and picked states detected. %s" % (self.name, first_unknown_index, error_info)
                                            message = '{"coded": "0002-0523-%4d-0041", "oneshot": %d, "msg":"%s", "action": "pause"}' % (first_unknown_index, 0, info)
                        else:
                            error_msg = f"{activate_status[1]} {activate_status[2]}"
                            message = f"Extruder {self.name} switch is not allowed, {error_msg}"

                        if retry_extruder_id is not None:
                            raise ExtruderUnknownParkStatus(message)
                        else:
                            raise gcmd.error(message)
                    gcmd.respond_info("Retrying to get normal extruder activation status (attempt {}), forced_park: {}, retry_extruder_id: {}".format(i,
                                        forced_park, retry_extruder_id))
                    toolhead.dwell(0.2)
                    toolhead.wait_moves()

            if (activate_status[0][1] == 0 and activate_status[0][0] == self.name and toolhead.get_extruder() is self and
                ((self.get_park_detector_status() is None and action is None) or (self.get_park_detector_status() is not None and action != 'PARK'))):
                gcmd.respond_info("Extruder %s already active" % (self.name,))
                is_grab_complete = True
                return

            if activate_status[0][1] == 1 and action == 'PARK':
                gcmd.respond_info("All extruder is parked")
                return

            print_fan = self.printer.lookup_object('fan', None)
            print_fan_speed = None
            current_time = self.printer.get_reactor().monotonic()
            if print_fan is not None:
                print_fan_speed = print_fan.get_status(current_time)['speed']
                print_fan.fan.set_speed_from_command(0)

            for i in range(len(extruder_list)):
                if extruder_list[i].binding_fan is not None and extruder_list[i].switch_extruder_ctr_fan_pwm:
                    extruder_list[i].binding_fan.set_speed_from_command(1, False)

            fan_pwm_set = True
            fan_close_tick = toolhead.reactor.monotonic()

            saved_states = {
                'absolute_coord': gcode_move.absolute_coord,
                'absolute_extrude': gcode_move.absolute_extrude,
                'base_position': list(gcode_move.base_position),
                'last_position': list(gcode_move.last_position),
                'homing_position': list(gcode_move.homing_position),
                'speed': gcode_move.speed, 'speed_factor': gcode_move.speed_factor,
                'extrude_factor': gcode_move.extrude_factor,
                'fan_speed': print_fan_speed,
                'max_accel': toolhead.max_accel,
            }

            if activate_status[0][1] == 0 or (forced_park and retry_extruder_id is not None):

                if not forced_park:
                    cur_extruder = self.printer.lookup_object(activate_status[0][0], None)
                    if cur_extruder is None:
                        raise gcmd.error("The current extruder lookup object failed")

                    cur_extruder_state = cur_extruder.get_park_detector_status()
                    if cur_extruder_state is not None and cur_extruder_state['state'] != 'ACTIVATE':
                        state, park_pin = cur_extruder_state['state'], cur_extruder_state['park_pin']
                        active_pin, grab_valid_pin = cur_extruder_state['active_pin'], cur_extruder_state['grab_valid_pin']
                        pin_sta = ''.join(['T' if x else 'F' for x in [park_pin, active_pin, grab_valid_pin]])
                        msg = f"Parking is not allowed for {cur_extruder.name}, status: {state} [{pin_sta}]"
                        message = '{"coded": "0002-0523-%4d-0019", "oneshot": %d, "msg":"%s", "action": "pause"}' % (cur_extruder.extruder_num, 0, msg)
                        raise gcmd.error(message)

                    if cur_extruder_state is None and action == 'PARK':
                        cur_extruder = self
                else:
                    cur_extruder = extruder_list[retry_extruder_id]
                    cur_extruder_state = None

                if cur_extruder.xy_park_position is not None and ((activate_status[0][0] != self.name and action is None) or
                    (cur_extruder_state is None and action == 'PARK') or (cur_extruder_state is not None and
                    (action == 'PARK' or (action == 'PICK' and activate_status[0][0] != self.name)))):
                    restore_state = True

                    if self.switch_accel != toolhead.max_accel:
                        toolhead.set_accel(self.switch_accel)

                    x_move_position = cur_extruder.xy_park_position[0] + [1, -1][not cur_extruder.grab_dir] * \
                                    (cur_extruder.horizontal_move_x - cur_extruder.retract_x_dist)
                    gcmd.respond_info("park {} !!!".format(cur_extruder.name))
                    pos = toolhead.get_position()
                    if pos[1] > cur_extruder.y_idle_position:
                        toolhead.manual_move([None, cur_extruder.y_idle_position, None], cur_extruder.fast_move_speed)

                        toolhead.manual_move([x_move_position + [1, -1][cur_extruder.grab_dir]*0.5, None, None], cur_extruder.fast_move_speed)

                        toolhead.manual_move([x_move_position, None, None], cur_extruder.fast_move_speed)

                    else:
                        toolhead.manual_move([x_move_position + [1, -1][cur_extruder.grab_dir]*0.5, None, None], cur_extruder.fast_move_speed)

                        toolhead.manual_move([x_move_position, None, None], cur_extruder.fast_move_speed)

                        toolhead.manual_move([None, cur_extruder.y_idle_position, None], cur_extruder.fast_move_speed)

                    y_move_position = max(cur_extruder.xy_park_position[1] - cur_extruder.inser_buffer_dist, cur_extruder.y_idle_position)
                    toolhead.manual_move([None, y_move_position, None], cur_extruder.fast_move_speed)

                    toolhead.manual_move([None, cur_extruder.xy_park_position[1], None], cur_extruder.slow_move_speed)

                    toolhead.manual_move([cur_extruder.xy_park_position[0], None, None], cur_extruder.slow_move_speed)

                    toolhead.manual_move([None, cur_extruder.y_idle_position, None], cur_extruder.fast_move_speed)

                    toolhead.wait_moves()

                    for i in range(10):
                        cur_extruder_state = cur_extruder.get_park_detector_status()
                        if not (cur_extruder_state is not None and cur_extruder_state['state'] != 'PARKED'):
                            break
                        else:
                            if i == 9:
                                if cur_extruder_state is not None:
                                    state, park_pin = cur_extruder_state['state'], cur_extruder_state['park_pin']
                                    active_pin, grab_valid_pin = cur_extruder_state['active_pin'], cur_extruder_state['grab_valid_pin']
                                    pin_sta = ''.join(['T' if x else 'F' for x in [park_pin, active_pin, grab_valid_pin]])
                                    msg = f"Extruder {cur_extruder.name} malfunction after parking, state: {state} [{pin_sta}]"
                                    message = '{"coded": "0002-0523-%4d-0020", "oneshot": %d, "msg":"%s", "action": "pause"}' % (cur_extruder.extruder_num, 0, msg)
                                    if self.grab_hall_sensor_type:
                                        if pin_sta == 'FTT' or pin_sta == 'FTF':
                                            msg = f"Extruder {cur_extruder.name} malfunction after parking, {cur_extruder.name} failed to return to park position. state: {state} [{pin_sta}]"
                                            message = '{"coded": "0002-0523-%4d-0042", "oneshot": %d, "msg":"%s", "action": "pause"}' % (cur_extruder.extruder_num, 0, msg)
                                        elif pin_sta == 'FFT' or pin_sta == 'FFF':
                                            msg = f"Extruder {cur_extruder.name} malfunction after parking, {cur_extruder.name} detached detected. state: {state} [{pin_sta}]"
                                            message = '{"coded": "0002-0523-%4d-0043", "oneshot": %d, "msg":"%s", "action": "pause"}' % (cur_extruder.extruder_num, 0, msg)
                                        elif pin_sta == 'TTT' or pin_sta == 'TTF':
                                            msg = f"Extruder {cur_extruder.name} malfunction after parking, conflicting status detected: both parked and picked states. state: {state} [{pin_sta}]"
                                            message = '{"coded": "0002-0523-%4d-0044", "oneshot": %d, "msg":"%s", "action": "pause"}' % (cur_extruder.extruder_num, 0, msg)
                                    raise gcmd.error(message)
                                else:
                                    raise gcmd.error("Extruder malfunction after parking, {}: {}".format(cur_extruder.name, cur_extruder_state), 'pause')
                            gcmd.respond_info(f"Post-park check failed for {cur_extruder.name}, retry {i}")
                            toolhead.dwell(0.2)
                            toolhead.wait_moves()

            if action == 'PARK':
                raise ExtruderParkAction("park action success!!!")

            extruder_state = self.get_park_detector_status()
            if (self.xy_park_position is not None and (extruder_state is None or (extruder_state is not None and
                not (activate_status[0][0] == self.name and activate_status[0][1] == 0)))):
                for i in range(10):
                    extruder_state = self.get_park_detector_status()
                    if not (extruder_state is not None and extruder_state['state'] != 'PARKED'):
                        break
                    else:
                        if i == 9:
                            if extruder_state is not None:
                                state, park_pin = extruder_state['state'], extruder_state['park_pin']
                                active_pin, grab_valid_pin = extruder_state['active_pin'], extruder_state['grab_valid_pin']
                                pin_sta = ''.join(['T' if x else 'F' for x in [park_pin, active_pin, grab_valid_pin]])
                                msg = f"Picking is not allowed for the {self.name}, status: {state} [{pin_sta}]"
                                message = '{"coded": "0002-0523-%4d-0021", "oneshot": %d, "msg":"%s", "action": "pause"}' % (self.extruder_num, 0, msg)
                                raise gcmd.error(message)
                            else:
                                raise gcmd.error("Pre-pick check failed for {}, {}".format(self.name, extruder_state), 'pause')
                        gcmd.respond_info(f"Pre-pick check failed for {self.name}, retry {i}")
                        toolhead.dwell(0.2)
                        toolhead.wait_moves()

                restore_state = True
                if self.switch_accel != toolhead.max_accel:
                    toolhead.set_accel(self.switch_accel)
                gcmd.respond_info("pick {} !!!".format(self.name))
                x_move_position = self.xy_park_position[0]
                pos = toolhead.get_position()
                if pos[1] > self.y_idle_position:
                    toolhead.manual_move([None, self.y_idle_position, None], self.fast_move_speed)

                    toolhead.manual_move([x_move_position + [1, -1][self.grab_dir]*0.5, None, None], self.fast_move_speed)

                    toolhead.manual_move([x_move_position, None, None], self.fast_move_speed)

                else:
                    toolhead.manual_move([x_move_position + [1, -1][self.grab_dir]*0.5, None, None], self.fast_move_speed)

                    toolhead.manual_move([x_move_position, None, None], self.fast_move_speed)

                    toolhead.manual_move([None, self.y_idle_position, None], self.fast_move_speed)

                y_move_position = max(self.xy_park_position[1] - self.inser_buffer_dist, self.y_idle_position)
                toolhead.manual_move([None, y_move_position, None], self.fast_move_speed)

                toolhead.manual_move([None, self.xy_park_position[1], None], self.slow_move_speed)

                x_move_position = self.xy_park_position[0] + [-1, 1][self.grab_dir]*self.horizontal_move_x
                toolhead.manual_move([x_move_position, None, None], self.grab_speed)

                toolhead.manual_move([x_move_position+[1, -1][self.grab_dir]*self.retract_x_dist, None, None], self.grab_speed)

                toolhead.manual_move([None, self.y_idle_position, None], self.fast_move_speed)

                toolhead.wait_moves()
                check_tick = toolhead.reactor.monotonic()
                if self.switch_extruder_ctr_fan_pwm and check_tick - fan_close_tick < 2.5:
                    gcmd.respond_info("wait_moves {}s!!!".format(2.5 - (check_tick - fan_close_tick)))
                    toolhead.dwell(2.5 - (check_tick - fan_close_tick))
                    toolhead.wait_moves()
                if self.get_park_detector_status() is not None:
                    for i in range(10):
                        extruder_state = self.get_extruder_activate_status()
                        retry_extruder_id = self.check_allow_retry_switch_extruder()
                        if (extruder_state[0][1] == 0 and extruder_state[0][0] == self.name):
                            break
                        else:
                            if i == 9:
                                code = 35
                                msg = "Extruder pickup failed, {}".format(extruder_state)
                                extruder_num = self.extruder_num
                                if extruder_state[0][1] == 0:
                                    code = 22
                                    msg = f"{self.name} pickup failed, target: {self.name} current: {extruder_state[0][0]}"
                                elif extruder_state[0][1] == 1:
                                    code = 23
                                    msg = f"{self.name} pickup failed"
                                elif extruder_state[0][1] == 2:
                                    result = self.analyze_switch_extruder_error(extruder_state)
                                    if result:
                                        error_msg, activated, unknown, grip_states, activated_code, unknown_code = result
                                        if "multi-act" in error_msg:
                                            if 23 + activated_code < 35:
                                                code = 23 + activated_code
                                        else:
                                            code = 35
                                            if self.grab_hall_sensor_type:
                                                first_unknow_state = grip_states[unknown[0]]
                                                if first_unknow_state == 'FFT':
                                                    code = 45
                                                    extruder_num = unknown[0]
                                                    error_msg = f'detected that extruder{unknown[0]} pogopin not connected. {error_msg}'
                                                elif first_unknow_state == 'FFF':
                                                    code = 46
                                                    extruder_num = unknown[0]
                                                    error_msg = f'detected that extruder{unknown[0]} is detached. {error_msg}'
                                                elif first_unknow_state == 'TTT' or first_unknow_state == 'TTF':
                                                    code = 47
                                                    extruder_num = unknown[0]
                                                    error_msg = f'detected conflicting status for extruder{unknown[0]}: both parked and picked states. {error_msg}'
                                        msg = f"Extruder {self.name} pickup failed, {error_msg}"
                                message = '{"coded": "0002-0523-%4d-%4d", "oneshot": %d, "msg":"%s", "action": "pause"}' % (extruder_num, code, 0, msg)
                                if retry_extruder_id is not None:
                                    raise ExtruderPickAbnormal(message)
                                else:
                                    raise gcmd.error(message)
                            gcmd.respond_info("After picking the extruder, checking retry attempt {}, retry_extruder_id: {}".format(i, retry_extruder_id))
                            toolhead.dwell(0.2)
                            toolhead.wait_moves()
            switch_recorder = self.printer.lookup_object('extruder_switch_recorder', None)
            if switch_recorder is not None:
                switch_recorder.add_switch_count(self.name)
            switch_complete, is_grab_complete = True, True
        except ExtruderParkAction as e:
            pass
        except Exception as e:

            raise
        finally:
            self.activating_move = False
            for i in range(len(extruder_list)):
                if fan_pwm_set and extruder_list[i].binding_fan is not None and extruder_list[i].switch_extruder_ctr_fan_pwm:
                    extruder_list[i].binding_fan.set_speed_from_command(0)

                if extruder_list[i].vref_sw is not None:
                    if extruder_list[i].name == self.name:
                        toolhead.register_lookahead_callback(lambda print_time: extruder_list[i].vref_sw._set_pin(print_time, 1))

                    else:
                        toolhead.register_lookahead_callback(lambda print_time: extruder_list[i].vref_sw._set_pin(print_time, 0))

            if restore_state == True:

                gcode_move.absolute_coord = saved_states['absolute_coord']
                gcode_move.absolute_extrude = saved_states['absolute_extrude']
                gcode_move.base_position = list(saved_states['base_position'])
                gcode_move.homing_position = list(saved_states['homing_position'])
                gcode_move.speed = saved_states['speed']
                gcode_move.speed_factor = saved_states['speed_factor']
                gcode_move.extrude_factor = saved_states['extrude_factor']

                e_diff = gcode_move.last_position[3] - saved_states['last_position'][3]
                gcode_move.base_position[3] += e_diff
                if saved_states['max_accel'] != toolhead.max_accel:
                    toolhead.set_accel(saved_states['max_accel'])

            if switch_complete == True:
                gcmd.respond_info("Activating extruder %s" % (self.name,))
                toolhead.flush_step_generation()
                toolhead.set_extruder(self, self.last_position)
                self.printer.send_event("extruder:activate_extruder")

                self.active_binding_probe()
                self.active_binding_fan()

                if saved_states['fan_speed'] is not None and saved_states['fan_speed'] > 0:
                    print_fan.fan.set_speed_from_command(saved_states['fan_speed'])

                if self.base_position is not None and self.gcode_offset is not None:
                    build_params = {}
                    build_params['X'] = str(self.gcode_offset[0])
                    build_params['Y'] = str(self.gcode_offset[1])
                    build_params['Z'] = str(self.gcode_offset[2])
                    build_params['MOVE'] = '0'

                    restore_offset_gcmd = gcode.create_gcode_command("", "", build_params)
                    gcode_move.cmd_SET_GCODE_OFFSET(restore_offset_gcmd)
            toolhead.set_grab_complete(is_grab_complete)

    def cmd_MOVE_TO_XY_IDLE_POSITION(self, gcmd):
        gcode_move = self.printer.lookup_object('gcode_move')
        toolhead = self.printer.lookup_object('toolhead')
        current_extruder = toolhead.get_extruder()
        x_offset = gcmd.get_float('X_OFFSET', 0.)
        y_offset = gcmd.get_float('Y_OFFSET', 0.)
        z_offset = gcmd.get_float('Z_OFFSET', None)
        speed = gcmd.get_float('SPEED', 200.)
        accel = gcmd.get_float('ACCEL', None)

        if not self.check_xy_homing():
            raise gcmd.error("Activate extruder must home XY first")

        x_idle_position = current_extruder.xy_park_position[0] + [1, -1][not current_extruder.grab_dir] * \
                (current_extruder.horizontal_move_x - current_extruder.retract_x_dist)
        if (current_extruder.name == "extruder"):
            x_idle_position -= 2

        y_idle_position = current_extruder.y_idle_position

        toolhead.wait_moves()
        gcode_move_status = gcode_move.get_status()
        gcode_move.absolute_coord = True
        old_accel = toolhead.max_accel
        if accel == None:
            accel = old_accel
        pos = toolhead.get_position()
        if not (pos[0] == x_idle_position + x_offset and pos[1] == y_idle_position + y_offset):
            toolhead.max_accel = accel
            toolhead._calc_junction_deviation()
            if z_offset is not None:
                toolhead.manual_move([None, None, pos[2] + z_offset], speed)
            if pos[1] > current_extruder.y_idle_position:
                toolhead.manual_move([None, y_idle_position, None], speed)
                toolhead.manual_move([x_idle_position+x_offset, None, None], speed)
                toolhead.manual_move([None, y_idle_position+y_offset, None], speed)
            else:
                toolhead.manual_move([x_idle_position+x_offset, None, None], speed)
                toolhead.manual_move([None, y_idle_position+y_offset, None], speed)
            toolhead.max_accel = old_accel
            toolhead._calc_junction_deviation()
            toolhead.wait_moves()

        if not gcode_move_status['absolute_coordinates']:
            gcode_move.absolute_coord = False

    def cmd_INNER_APPLY_FLOW_K(self, gcmd):
        apply = gcmd.get_int('APPLY', None)

        if apply is not None:
            if apply == 0:
                self.is_calibrating_flow = False
            else:
                self.is_calibrating_flow = True
    def cmd_SET_EXTRUDER_BASE_POSITION(self, gcmd):

        is_first_time = self.base_position is None

        new_base_position = list(self.base_position) if self.base_position is not None else [0.0, 0.0, 0.0]

        has_adjust_params = any(gcmd.get_float(axis + '_ADJUST', None) is not None for axis in 'XYZ')
        if is_first_time and has_adjust_params:
            raise gcmd.error("Cannot use X_ADJUST, Y_ADJUST, or Z_ADJUST when base_position is not set. Use absolute values (X, Y, Z) instead.")

        any_param_specified = False
        specified_axes = [False, False, False]
        for pos, axis in enumerate('XYZ'):
            offset = gcmd.get_float(axis, None)
            if offset is None:
                offset = gcmd.get_float(axis + '_ADJUST', None)
                if offset is None:
                    continue

                offset += new_base_position[pos]

            new_base_position[pos] = offset
            any_param_specified = True
            specified_axes[pos] = True

        if not any_param_specified:
            if self.base_position is not None:
                gcmd.respond_info("Base position for %s: X:%.3f Y:%.3f Z:%.3f" %
                                 (self.name, self.base_position[0], self.base_position[1], self.base_position[2]))
            else:
                gcmd.respond_info("Base position for %s is not set" % (self.name,))
            return

        if is_first_time and not all(specified_axes):
            raise gcmd.error("When setting base_position for the first time, all three axes (X, Y, Z) must be specified.")

        self.base_position = new_base_position

        configfile = self.printer.lookup_object('configfile')
        extruder_bak = self.printer.lookup_object('extruder_config_bak', None)
        if extruder_bak is None or not os.path.exists(extruder_bak.base_position_config_path):
            configfile.set(self.name, 'base_position',
                        "\n%.6f, %.6f, %.6f\n" % (self.base_position[0], self.base_position[1], self.base_position[2]))
            self.printer.lookup_object('gcode').run_script_from_command("SAVE_CONFIG RESTART=0")
        else:
            if not self.update_extruder_config("base_position", new_base_position):
                gcmd.respond_info("Warning: Failed to save base position to config file")

        gcmd.respond_info("Base position for %s set to X:%.3f Y:%.3f Z:%.3f" %
                         (self.name, new_base_position[0], new_base_position[1], new_base_position[2]))

        self.printer.send_event("probe_inductance_coil: update_extruder_offset")

    def cmd_SET_EXTRUDER_PARK_POSITION(self, gcmd):

        if self.xy_park_position is None or self.y_idle_position is None:
            raise gcmd.error("Park position is not set. Cannot modify park position.")

        new_xy_park_position = list(self.xy_park_position)
        new_y_idle_position = self.y_idle_position
        force_save = not not gcmd.get_int('FORCE_SAVE', 0)

        any_param_specified = False
        for pos, axis in enumerate('XY'):
            offset = gcmd.get_float(axis, None)
            if offset is None:
                offset = gcmd.get_float(axis + '_ADJUST', None)
                if offset is None:
                    continue

                offset += new_xy_park_position[pos]

            new_xy_park_position[pos] = offset
            any_param_specified = True

        y_idle = gcmd.get_float('Y_IDLE', None)
        if y_idle is None:
            y_idle = gcmd.get_float('Y_IDLE_ADJUST', None)
            if y_idle is None:

                y_idle = new_y_idle_position
            else:

                y_idle += new_y_idle_position
                any_param_specified = True
        else:
            any_param_specified = True

        if not any_param_specified:

            gcmd.respond_info("Park position for %s: X:%.3f Y:%.3f Y_IDLE:%.3f" %
                             (self.name, self.xy_park_position[0], self.xy_park_position[1], self.y_idle_position))
            return

        x_diff = abs(new_xy_park_position[0] - self.xy_park_position[0])
        y_diff = abs(new_xy_park_position[1] - self.xy_park_position[1])

        if (x_diff > MAX_ALLOWED_DIFFERENCE or
            y_diff > MAX_ALLOWED_DIFFERENCE) and not force_save:
            diff_details = []
            if x_diff > MAX_ALLOWED_DIFFERENCE:
                diff_details.append(f"X: {x_diff:.3f}mm")
            if y_diff > MAX_ALLOWED_DIFFERENCE:
                diff_details.append(f"Y: {y_diff:.3f}mm")
            msg = (f"New park position differs too much. Exceeded: {', '.join(diff_details)}. "
                      f"Max allowed: {MAX_ALLOWED_DIFFERENCE:.3f}mm. Use FORCE_SAVE=1 to override.")
            err_msg = '{"coded": "0003-0530-0000-0023", "msg":"%s"}' % (msg)
            raise gcmd.error(err_msg)

        gcmd.respond_info("Park position for %s changed from X:%.3f Y:%.3f Y_IDLE:%.3f to X:%.3f Y:%.3f Y_IDLE:%.3f" %
                         (self.name,
                          self.xy_park_position[0], self.xy_park_position[1], self.y_idle_position,
                          new_xy_park_position[0], new_xy_park_position[1], y_idle))

        self.xy_park_position = new_xy_park_position
        self.y_idle_position = y_idle

        configfile = self.printer.lookup_object('configfile')
        extruder_bak = self.printer.lookup_object('extruder_config_bak', None)
        if extruder_bak is None or not os.path.exists(extruder_bak.config_path):
            configfile.set(self.name, 'xy_park_position',
                        "\n%.6f, %.6f\n" % (self.xy_park_position[0], self.xy_park_position[1]))
            configfile.set(self.name, 'y_idle_position', "%.6f" % (self.y_idle_position,))
            self.printer.lookup_object('gcode').run_script_from_command("SAVE_CONFIG RESTART=0")
        else:
            park_data = {
                'xy_park_position': self.xy_park_position,
                'y_idle_position': self.y_idle_position
            }
            if not self.update_extruder_config(None, park_data):
                gcmd.respond_info("Warning: Failed to save park position to config file")

        gcmd.respond_info("Park position for %s set to X:%.3f Y:%.3f Y_IDLE:%.3f" %
                         (self.name, new_xy_park_position[0], new_xy_park_position[1], y_idle))

    def cmd_ENTER_PARK_POINT_MANUAL_CALIBRATION(self, gcmd):
        if not hasattr(self, 'xy_park_position') or self.xy_park_position is None:
            err_msg = '{"coded": "0003-0530-0000-0019", "msg":"Park position is not configured, Cannot enter park point manual calibration"}'
            raise gcmd.error(err_msg)
        self.printer.lookup_object('gcode').run_script_from_command("SET_MAIN_STATE MAIN_STATE=PARK_POINT_MANUAL_CALIBRATION")
        self.printer.lookup_object('gcode').run_script_from_command("SET_IDLE_TIMEOUT TIMEOUT=9999999999")
    def cmd_EXIT_PARK_POINT_MANUAL_CALIBRATION(self, gcmd):
        machine_state_manager = self.printer.lookup_object('machine_state_manager', None)
        if machine_state_manager and str(machine_state_manager.get_status()['main_state']) == "PARK_POINT_MANUAL_CALIBRATION":
            self.printer.lookup_object('gcode').run_script_from_command("EXIT_TO_IDLE REQ_FROM_STATE=PARK_POINT_MANUAL_CALIBRATION")
            self.printer.lookup_object('gcode').run_script_from_command("SET_IDLE_TIMEOUT TIMEOUT=300")
    def cmd_MOVE_TO_PARK_CALIBRATION_POINT(self, gcmd):
        if not hasattr(self, 'xy_park_position') or self.xy_park_position is None:
            err_msg = '{"coded": "0003-0530-0000-0019", "msg":"Park position is not configured, Cannot enter park point manual calibration"}'
            raise gcmd.error(err_msg)

        if hasattr(self, 'park_detector') and self.park_detector is not None:
            extruder_state = self.get_extruder_activate_status()
            if extruder_state[0][1] != 1:
                err_msg = '{"coded": "0003-0530-0000-0020", "msg":"All extruders must be parked, Cannot move to park point"}'
                raise gcmd.error(err_msg)

        self.printer.lookup_object('gcode').run_script_from_command("SET_ACTION_CODE ACTION=PARK_POINT_MANUAL_CALIBRATING")
        xy_park_position = copy.deepcopy(self.xy_park_position)
        move_calibration_x = xy_park_position[0]
        move_calibration_y = 320

        force_move = not not gcmd.get_int('FORCE_MOVE', 0)
        macro = self.printer.lookup_object('gcode_macro _MOVE_TO_{}_PARK_CALIBRATION_POINT'.format(self.name.upper()), None)
        if macro is not None:
            move_calibration_x = macro.variables.get('x_pos', move_calibration_x)
            move_calibration_y = macro.variables.get('y_pos', move_calibration_y)

        move_calibration_x = gcmd.get_float('X', move_calibration_x)
        move_calibration_y = gcmd.get_float('Y', move_calibration_y)

        x_diff = abs(move_calibration_x - self.xy_park_position[0])
        if (x_diff > MAX_ALLOWED_DIFFERENCE and not force_move):
            msg = (f"Move to park calibration position X differs too much from current position. Exceeded: X: {x_diff:.3f}mm. "
                  f"Max allowed: {MAX_ALLOWED_DIFFERENCE:.3f}mm.")
            err_msg = '{"coded": "0003-0530-0000-0023", "msg":"%s"}' % (msg)
            raise gcmd.error(err_msg)

        toolhead = self.printer.lookup_object('toolhead')
        pos = toolhead.get_position()
        if pos[1] > self.y_idle_position:
            toolhead.manual_move([None, self.y_idle_position, None], 200)

        if move_calibration_y > self.y_idle_position:
            toolhead.manual_move([move_calibration_x, None], 200)
        toolhead.manual_move([move_calibration_x, move_calibration_y], 200)

    def cmd_VERIFY_PARK_POSITION(self, gcmd):
        if not hasattr(self, 'xy_park_position') or self.xy_park_position is None:
            err_msg = '{"coded": "0003-0530-0000-0019", "msg":"Park position is not configured, Cannot enter park point manual calibration"}'
            raise gcmd.error(err_msg)

        if hasattr(self, 'park_detector') and self.park_detector is not None:
            extruder_state = self.get_extruder_activate_status()
            if extruder_state[0][1] != 1:
                err_msg = '{"coded": "0003-0530-0000-0020", "msg":"All extruders must be parked, Cannot move to park point"}'
                raise gcmd.error(err_msg)

        force_move = not not gcmd.get_int('FORCE_MOVE', 0)
        verify_cnt = gcmd.get_int('VERIFY_CNT', 2, minval=1)
        skip_home = gcmd.get_int('SKIP_HOME', 0, minval=0, maxval=1)
        original_xy_park_position = copy.deepcopy(self.xy_park_position)
        xy_park_position = copy.deepcopy(self.xy_park_position)
        verify_x = gcmd.get_float('X', xy_park_position[0])
        verify_y = gcmd.get_float('Y', xy_park_position[1])
        xy_park_position[0] = verify_x
        xy_park_position[1] = verify_y

        x_diff = abs(verify_x - self.xy_park_position[0])
        if (x_diff > MAX_ALLOWED_DIFFERENCE and not force_move):
            msg = (f"Verify park position differs too much from current position. Exceeded: X: {x_diff:.3f}mm. "
                  f"Max allowed: {MAX_ALLOWED_DIFFERENCE:.3f}mm.")
            err_msg = '{"coded": "0003-0530-0000-0023", "msg":"%s"}' % (msg)
            raise gcmd.error(err_msg)

        try:
            for i in range(verify_cnt):
                try:
                    self.xy_park_position[:] = xy_park_position
                    self.printer.lookup_object('gcode').run_script_from_command("SET_ACTION_CODE ACTION=EXTRUDER_PICK_VERIFY")
                    if i == 0 and not skip_home:
                        self.printer.lookup_object('gcode').run_script_from_command("G28 X Y")
                    self.cmd_PICK_EXTRUDER(gcmd)
                except Exception as e:
                    raw_msg = self.printer.extract_coded_message_field(str(e))
                    err_msg = '{"coded": "0003-0530-%4d-0021", "msg":"%s"}' % (self.extruder_index, raw_msg)
                    raise gcmd.error(err_msg)

                try:
                    self.printer.lookup_object('gcode').run_script_from_command("SET_ACTION_CODE ACTION=EXTRUDER_PARK_VERIFY")
                    self.cmd_PARK_EXTRUDER(gcmd)
                except Exception as e:
                    raw_msg = self.printer.extract_coded_message_field(str(e))
                    err_msg = '{"coded": "0003-0530-%4d-0022", "msg":"%s"}' % (self.extruder_index, raw_msg)
                    raise gcmd.error(err_msg)
        except Exception as e:
            raise
        finally:
            try:
                self.xy_park_position[:] = original_xy_park_position
            except:
                logging.warning("Failed to restore XY park position")

class DummyExtruder:
    def __init__(self, printer):
        self.printer = printer
    def update_move_time(self, flush_time, clear_history_time):
        pass
    def check_move(self, move):
        raise move.move_error("Extrude when no extruder present")
    def find_past_position(self, print_time):
        return 0.
    def calc_junction(self, prev_move, move):
        return move.max_cruise_v2
    def get_name(self):
        return ""
    def get_heater(self):
        raise self.printer.command_error("Extruder not configured")
    def get_trapq(self):
        raise self.printer.command_error("Extruder not configured")

def add_printer_objects(config):
    global STRUCTURED_CODE_LIST
    STRUCTURED_CODE_LIST = []
    printer = config.get_printer()
    extruder_list = []
    park_position_config = park_check_config = None
    for i in range(99):
        section = 'extruder'
        if i:
            section = 'extruder%d' % (i,)
        if not config.has_section(section):
            break
        if 0 == i:
            extruder_switch_recorder = ExtruderSwitchRecorder(config.getsection(section))
            printer.add_object('extruder_switch_recorder', extruder_switch_recorder)
        pe = PrinterExtruder(config.getsection(section), i)
        if 0 == i:
            pe.active_binding_probe()
            park_check_config = pe.park_detector
            park_position_config = pe.xy_park_position
        else:
            if (park_check_config is None and pe.park_detector is not None) or (park_check_config is not None and pe.park_detector is None):
                raise config.error("park_detector config mismatch,  extruder: {},  {}: {} !!!".format(
                                  ['None', 'config'][park_check_config is not None],
                                  pe.name, ['None', 'config'][pe.park_detector is not None]))

            if (park_position_config is None and pe.xy_park_position is not None) or (park_position_config is not None and pe.xy_park_position is None):
                raise config.error("xy_park_position config mismatch, extruder: {},  {}: {} !!!".format(
                    ['None', 'config'][park_position_config is not None],
                    pe.name, ['None', 'config'][pe.xy_park_position is not None]))
        extruder_list.append(pe)
        printer.add_object(section, pe)
    printer.add_object('extruder_list', extruder_list)
