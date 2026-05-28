import json

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
MAX_TOOLS_MAX_INDEX = MAX_TOOLS_COUNT-1

class Commands: # We only notify the user by design, this module shall never cause anything that crashes the print.
    def __init__(self, printer, logs, helper):
        self.printer = printer
        self.gcode = self.printer.lookup_object("gcode")
        self.logs = logs
        self.helper = helper

        self.gcode.register_command(
            "SH_SET_ACTIVE_TOOL",
            self.cmd_SET_ACTIVE_TOOL,
            desc="Spoolman Helper: Sets the current active spool"
        )

        self.gcode.register_command(
            "SH_CLEAR_ACTIVE_SPOOL",
            self.cmd_CLEAR_ACTIVE_SPOOL,
            desc="Spoolman Helper: Clears the current active spool"
        )

        self.gcode.register_command(
            "SH_CLEAR_ALL_SPOOLS",
            self.cmd_CLEAR_ALL_SPOOLS,
            desc="Spoolman Helper: Clears all spools currently associated to tools"
        )

        self.gcode.register_command(
            "SH_DETECT_SPOOLS",
            self.cmd_DETECT_SPOOLS,
            desc="Spoolman Helper: detects and configures spools"
        )

        self.gcode.register_command(
            "SH_DUMP_SPOOLS",
            self.cmd_DUMP_SPOOLS,
            desc="Spoolman Helper: Dumps the current spools configuration as known by the helper"
        )

        self.gcode.register_command(
            "SH_CONFIG",
            self.cmd_SH_CONFIG,
            desc="Spoolman Helper: Configure the module's options at runtime without needing to restart klipper. These changes are ephemeral and will not be persisted across restarts and reboots."
        )

        self.gcode.register_command(
            "SH_DEBUG",
            self.cmd_SH_DEBUG,
            desc="Spoolman Helper: Do I really need to explain this one? Come on..."
        )

    def cmd_SET_ACTIVE_TOOL(self, gcmd):
        tool_id = gcmd.get_int("TOOL", minval=0, maxval=MAX_TOOLS_MAX_INDEX)
        self.logs.verbose(f"SET_ACTIVE_TOOL: T{tool_id}")
        self.helper.set_active_tool(tool_id)

    def cmd_CLEAR_ACTIVE_SPOOL(self, gcmd):
        self.logs.log(f"Active Spool Cleared")
        self.helper.spoolman.set_active_spool(None)

    def cmd_CLEAR_ALL_SPOOLS(self, gcmd):
        self.helper.clear_spool_ids()
        for tool in range(MAX_TOOLS_COUNT):
            self.logs.verbose(f"Clearing spool config for T{tool}")
            self.helper.macros.set_spool_id_for_tool(f"T{tool}", None)

    def cmd_DETECT_SPOOLS(self, gcmd):
        self.helper.detect_spools()

    def cmd_DUMP_SPOOLS(self, gcmd):
        raw = gcmd.get("RAW", None)
        self.helper.dump(raw)

    def cmd_SH_CONFIG(self, gcmd):
        mode = gcmd.get("MODE", None)
        logging = gcmd.get("LOGS", None)

        if mode is not None:
            mode = mode.lower()
            if mode not in ("auto", "manual"):
                self.logs.error("MODE must be: auto or manual")
            self.helper.mode = mode

        if logging is not None:
            logging = logging.lower()
            if logging not in ("error", "info", "warn", "verbose", "debug"):
                self.logs.error("LOGS must be: error, info, warn, verbose, debug")
                return
            self.helper.logging = logging

        self.logs.log(f"Config: mode->{self.helper.mode}, log level->{self.helper.logging}")
    
    def cmd_SH_DEBUG(self, gcmd):
        sku = gcmd.get("SKU", None)
        self.logs.log(f"Config: mode->{self.helper.mode}, log level->{self.helper.logging}")

        if sku:
            def on_spool_result(error, spools):
                spool = spools[0]
                self.logs.log(f"cmd_SH_DEBUG found sppol: {spool}")
                if error:
                    self.logs.error(f"on_spool_result {json.dumps(error)}")

            self.helper.spoolman.lookup_spoolman(sku, on_spool_result)
