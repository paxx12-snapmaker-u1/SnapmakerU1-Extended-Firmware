class Macros:
    def __init__(self, printer, logs):
        self.printer = printer
        self.logs = logs
        self.gcode = self.printer.lookup_object("gcode")
        self.webhooks = self.printer.lookup_object("webhooks")

    def run(self, command, error):
        try: 
            self.gcode.run_script_from_command(command)
        except Exception:
            self.logs.error(f"for {command}: {error}")

    def set_spool_id_for_tool(self, tool, spool_id):
        spool_id = repr(spool_id);
        command = f"SET_GCODE_VARIABLE MACRO={tool} VARIABLE=spool_id VALUE={spool_id}"
        self.logs.verbose(f"updating tool wtih command {command}")
        self.run(command, f"tool {tool} does not have a spool_id property.")

    def get_spool_id_for_tool(self, tool_id):
        try:
            macro = self.printer.lookup_object(f"gcode_macro T{tool_id}")
            self.logs.verbose(f"macro for T{tool_id} variables: {macro.variables}")
            spool_id = macro.variables.get('spool_id', None)

            if spool_id is None:
                self.logs.warn(f"T{tool_id} macro has no spool_id")

            return spool_id
        except Exception:
            self.logs.warn(f"T{tool_id} macro not found")
            return None
    
    def detect_spool(self, channel):
        command = f"FILAMENT_DT_UPDATE CHANNEL={channel}"
        self.run(command, f"did not update channel {channel}")
