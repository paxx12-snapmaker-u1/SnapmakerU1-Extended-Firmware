import logging, os

POSTFIX_CONFIG_FILE ='_runout_sensor.json'
DEFAULT_CONFIG = {
    'enable': True
}

class RunoutHelper:
    def __init__(self, config):
        self.name = config.get_name().split()[-1]
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.runout_pause = config.getboolean('pause_on_runout', True)
        if self.runout_pause:
            self.printer.load_object(config, 'pause_resume')
        self.runout_gcode = self.insert_gcode = None
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        if self.runout_pause or config.get('runout_gcode', None) is not None:
            self.runout_gcode = gcode_macro.load_template(
                config, 'runout_gcode', '')
        if config.get('insert_gcode', None) is not None:
            self.insert_gcode = gcode_macro.load_template(
                config, 'insert_gcode')
        self.pause_delay = config.getfloat('pause_delay', .5, above=.0)
        self.event_delay = config.getfloat('event_delay', 3., above=0.)

        self.min_event_systime = self.reactor.NEVER
        self.filament_present = False
        self.sensor_enabled = True

        self.extruder_index = self._get_extruder_index(config.get('extruder'))
        self.exception_manager = self.printer.lookup_object('exception_manager', None)

        config_dir = self.printer.get_snapmaker_config_dir()
        config_name = self.name + POSTFIX_CONFIG_FILE
        self.config_path = os.path.join(config_dir, config_name)
        self.config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_CONFIG)
        self.sensor_enabled = self.config['enable']

        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.gcode.register_mux_command(
            "QUERY_FILAMENT_SENSOR", "SENSOR", self.name,
            self.cmd_QUERY_FILAMENT_SENSOR,
            desc=self.cmd_QUERY_FILAMENT_SENSOR_help)
        self.gcode.register_mux_command(
            "SET_FILAMENT_SENSOR", "SENSOR", self.name,
            self.cmd_SET_FILAMENT_SENSOR,
            desc=self.cmd_SET_FILAMENT_SENSOR_help)
        self.gcode.register_mux_command(
            "CHECK_FILAMENT_RUNOUT", "SENSOR", self.name,
            self.cmd_CHECK_FILAMENT_RUNOUT,
            desc=self.cmd_CHECK_FILAMENT_RUNOUT_help)
    def _handle_ready(self):
        self.min_event_systime = self.reactor.monotonic() + 2.
        self.print_task_config = self.printer.lookup_object('print_task_config', None)

    def _get_extruder_index(self, extruder_name):
        if extruder_name is not None and extruder_name.startswith('extruder'):
            num_str = extruder_name[8:]
            return int(num_str) if num_str.isdigit() else 0
        return 0

    def _runout_disp(self):
        ace = self.printer.lookup_object('ace', None)
        head = self.extruder_index
        if ace is not None and hasattr(ace, '_t'):
            hd = ace._disp(head)
            src = (getattr(ace, '_head_source', None) or {}).get(head) or {}
            a, s = src.get('ace_index'), src.get('slot')
            loc = ' (ACE %d / Slot %d)' % (ace._disp(a), ace._disp(s)) \
                if a is not None and s is not None else ''
            return ace._t('msg.pause_runout', head=hd, loc=loc)
        return ('[multiACE] %s runout - reload filament '
                '(display or web "Reload"), then RESUME' % self.name)

    def _runout_event_handler(self, eventtime):

        ace = self.printer.lookup_object('ace', None)
        if ace is not None and getattr(ace, '_swap_in_progress', False):
            logging.info("[multiACE] filament_switch_sensor: blocking runout during swap")
            return
        if ace is not None and self.extruder_index in getattr(ace, '_runout_suppress_heads', ()):
            logging.info("[multiACE] filament_switch_sensor: runout suppressed for head %d (recovery: empty head awaiting reload)" % self.extruder_index)
            return

        full_msg = self._runout_disp()
        pause_prefix = ""
        if self.runout_pause:
            if self.exception_manager is not None:
                self.printer.send_event("print_stats:update_exception_info",
                                        self.exception_manager.list.MODULE_ID_TOOLHEAD,
                                        self.extruder_index,
                                        self.exception_manager.list.CODE_TOOLHEAD_FILAMENT_RUNOUT,
                                        full_msg,
                                        2)
            pause_resume = self.printer.lookup_object('pause_resume')
            pause_resume.send_pause_command()
            pause_prefix = "PAUSE IS_RUNOUT=1\n"
            self.printer.get_reactor().pause(eventtime + self.pause_delay)
        self._exec_gcode(pause_prefix, self.runout_gcode)
        if self.runout_pause:
            try:
                self.gcode.run_script(f'\nM400\nINNER_AUTO_REPLENISH_FILAMENT EXTRUDER={self.extruder_index}\n')
            except Exception:
                logging.exception("Script running error")
            if self.print_task_config.perform_auto_replenish == False:
                if self.exception_manager is not None:
                    self.exception_manager.raise_exception_async(
                        id = self.exception_manager.list.MODULE_ID_TOOLHEAD,
                        index = self.extruder_index,
                        code = self.exception_manager.list.CODE_TOOLHEAD_FILAMENT_RUNOUT,
                        message = full_msg,
                        oneshot = 0,
                        level = 2)

    def _insert_event_handler(self, eventtime):
        self._exec_gcode("", self.insert_gcode)

    def _exec_gcode(self, prefix, template):
        try:
            self.gcode.run_script(prefix + template.render() + "\nM400")
        except Exception:
            logging.exception("Script running error")
        self.min_event_systime = self.reactor.monotonic() + self.event_delay
    def note_filament_present(self, is_filament_present, force=False):
        if is_filament_present == self.filament_present and force == False:
            return
        self.filament_present = is_filament_present
        eventtime = self.reactor.monotonic()
        if eventtime < self.min_event_systime or not self.sensor_enabled:

            return

        if self.filament_present:
            logging.info("Filament Sensor %s: insert event detected, Time %.2f" %
                         (self.name, eventtime))
        else:
            logging.info("Filament Sensor %s: remove event detected, Time %.2f" %
                         (self.name, eventtime))

        if self.print_task_config is not None:
            self.print_task_config.backup_filament_info(self.extruder_index)
        self.printer.send_event("filament_switch_sensor:runout",
                                self.extruder_index, is_filament_present)

        print_stats = self.printer.lookup_object('print_stats')
        is_printing = print_stats.state == "printing"

        if is_filament_present:
            ace = self.printer.lookup_object('ace', None)
            if ace is not None and self.extruder_index in getattr(ace, '_runout_suppress_heads', ()):
                ace._runout_suppress_heads.discard(self.extruder_index)
                logging.info("[multiACE] note_filament_present: head %d (re)loaded - clearing runout suppression" % self.extruder_index)
            if ace is not None and self.extruder_index in getattr(ace, '_bg_left_empty', ()):
                ace._bg_left_empty.discard(self.extruder_index)
            if not is_printing and self.insert_gcode is not None:

                self.min_event_systime = self.reactor.NEVER
                self.reactor.register_callback(self._insert_event_handler)
            if self.exception_manager is not None:
                self.exception_manager.clear_exception(
                    id = self.exception_manager.list.MODULE_ID_TOOLHEAD,
                    index = self.extruder_index,
                    code = self.exception_manager.list.CODE_TOOLHEAD_FILAMENT_RUNOUT)
        elif is_printing and self.runout_gcode is not None:

            ace = self.printer.lookup_object('ace', None)
            if ace is not None and getattr(ace, '_swap_in_progress', False):
                logging.info("[multiACE] note_filament_present: blocking runout callback during swap")
                return
            if ace is not None and self.extruder_index in getattr(ace, '_runout_suppress_heads', ()):
                logging.info("[multiACE] note_filament_present: runout suppressed for head %d (recovery: empty head awaiting reload)" % self.extruder_index)
                return

            if self.print_task_config is not None and \
                    getattr(self.print_task_config, 'is_exec_print_end_action', False):
                return

            self.min_event_systime = self.reactor.NEVER
            logging.info(
                "Filament Sensor %s: runout event detected, Time %.2f" %
                (self.name, eventtime))
            self.reactor.register_callback(self._runout_event_handler)

    def get_status(self, eventtime):
        return {
            "filament_detected": bool(self.filament_present),
            "enabled": bool(self.sensor_enabled)}
    cmd_QUERY_FILAMENT_SENSOR_help = "Query the status of the Filament Sensor"
    def cmd_QUERY_FILAMENT_SENSOR(self, gcmd):
        if self.filament_present:
            msg = "Filament Sensor %s: filament detected" % (self.name)
        else:
            msg = "Filament Sensor %s: filament not detected" % (self.name)
        gcmd.respond_info(msg)
    cmd_SET_FILAMENT_SENSOR_help = "Sets the filament sensor on/off"
    def cmd_SET_FILAMENT_SENSOR(self, gcmd):
        self.sensor_enabled = gcmd.get_int("ENABLE", 1)
        self.config['enable'] = bool(self.sensor_enabled)
        logging.info("Filament Sensor: set enable/disable -- %d", self.sensor_enabled)

        if self.print_task_config is not None and \
                hasattr(self.print_task_config, 'update_filament_flags'):
            self.print_task_config.update_filament_flags()

        need_save = gcmd.get_int('SAVE', 1, minval=0, maxval=1)
        if (need_save):
            load_config = self.printer.load_snapmaker_config_file(self.config_path, DEFAULT_CONFIG)
            load_config['enable'] = self.config['enable']
            ret = self.printer.update_snapmaker_config_file(self.config_path, load_config, DEFAULT_CONFIG)
            if not ret:
                raise gcmd.error("save startup stay failed!")
    cmd_CHECK_FILAMENT_RUNOUT_help = "Check for filament runout during printing process."
    def cmd_CHECK_FILAMENT_RUNOUT(self, gcmd):
        print_stats = self.printer.lookup_object('print_stats', None)
        if print_stats is not None and print_stats.state in ["printing", "paused"]:
            if bool(self.sensor_enabled) and not bool(self.filament_present):
                ace = self.printer.lookup_object('ace', None)
                if ace is not None and self.extruder_index in getattr(
                        ace, '_runout_suppress_heads', ()):
                    logging.info(
                        '[multiACE] CHECK_FILAMENT_RUNOUT: suppressed for '
                        'head %d (recovery: empty head awaiting reload)'
                        % self.extruder_index)
                    return
                if (ace is not None
                        and getattr(ace, '_print_has_gcode_loads', False)
                        and ace.head_uses_ace(self.extruder_index)
                        and not ace._head_is_loaded(self.extruder_index)):
                    logging.info(
                        '[multiACE] CHECK_FILAMENT_RUNOUT: head %d is an '
                        'unloaded ACE-driven head and this print carries '
                        'multiACE loads - its load is ahead in the gcode, '
                        'allowing RESUME' % self.extruder_index)
                    return
                raise gcmd.error(
                        message = self._runout_disp(),
                        action = 'pause',
                        id = 523,
                        index = self.extruder_index,
                        code = 0,
                        oneshot = 0,
                        level = 2)

class SwitchSensor:
    def __init__(self, config):
        printer = config.get_printer()
        buttons = printer.load_object(config, 'buttons')
        switch_pin = config.get('switch_pin')

        if config.get('analog_range', None) is None:
            buttons.register_buttons([switch_pin], self._button_handler)
        else:
            amin, amax = config.getfloatlist('analog_range', count=2)
            pullup = config.getfloat('analog_pullup_resistor', 4700., above=0.)
            buttons.register_adc_button(switch_pin, amin, amax, pullup, self._button_handler)
        self.runout_helper = RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
    def _button_handler(self, eventtime, state):
        self.runout_helper.note_filament_present(state)

def load_config_prefix(config):
    return SwitchSensor(config)
