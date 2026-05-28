class PrintLifecycle:
    def __init__(self, printer, logs, helper):
        self.printer = printer
        self.logs = logs
        self.helper = helper

        self.printer.register_event_handler('print_stats:start', self.on_print_start)
        self.printer.register_event_handler('print_stats:stop', self.on_print_stop)
        self.printer.register_event_handler('pause_resume:cancel', self.on_print_cancel)

        # these are left here for convenience
        # self.printer.register_event_handler("virtual_sdcard:reset_file", self._on_reset_file)
        # self.printer.register_event_handler("idle_timeout:ready", self._on_idle_timeout_ready) # on pause
        # self.printer.register_event_handler("idle_timeout:idle", self._on_idle_timeout_idle)
        # self.printer.register_event_handler("idle_timeout:start", self._on_idle_timeout_start)
        # self.printer.register_event_handler("idle_timeout:stop", self._on_idle_timeout_stop)

    # def _on_idle_timeout_ready(self, print_time):
    #     self.logs.debug(f"PrintLifecycle idle_timeout:ready")
    
    # def _on_idle_timeout_idle(self, print_time):
    #     self.logs.debug(f"PrintLifecycle idle_timeout:idle")
    
    # def _on_idle_timeout_start(self):
    #     self.logs.debug(f"PrintLifecycle idle_timeout:start")
    
    # def _on_idle_timeout_stop(self):
    #     self.logs.debug(f"PrintLifecycle idle_timeout:stop")
    
    # def _on_reset_file(self):
    #     self.logs.debug(f"PrintLifecycle virtual_sdcard:reset_file")

    def on_print_start(self):
        self.logs.verbose(f"New print job start!")
        self.helper.sync_spools_tools()

    def on_print_stop(self):
        self.logs.verbose(f"Print job ended, clearing.")
        self.helper.u1_tools.clear_map()
        self.helper.spoolman.clear_active_spool()

    def on_print_cancel(self):
        self.logs.verbose(f"Print job canceled, clearing.")
        self.helper.u1_tools.clear_map()
        self.helper.spoolman.clear_active_spool()

    def on_print_resume(self):
        self.logs.verbose(f"Print job resumed, resuming spool tracking")
        if not self.helper.mode  == "manual":
            self.helper.u1_tools.update_map()
