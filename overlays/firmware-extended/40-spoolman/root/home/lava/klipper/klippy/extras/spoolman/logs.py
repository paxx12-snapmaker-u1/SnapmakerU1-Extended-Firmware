class Logs: # We only notify the user by design, this module shall never cause anything that crashes the print.
    levels = ["error", "info", "warn", "verbose", "debug"]

    def __init__(self, printer, helper):
        self.printer = printer
        self.gcode = self.printer.lookup_object("gcode")
        self.prefix = "🧶 SH"
        self.helper = helper

    def should_output(self, request_level):
        return self.levels.index(request_level) <= self.levels.index(self.helper.logging)

    def format_message(self, level, icon, message):
        return f"{icon}{self.prefix} [{level}]: {message}"

    def debug(self, message):
        if self.should_output("debug"):
            self.gcode.respond_info(self.format_message("DEBUG", "🔵",message))

    def log(self, message):
        if self.should_output("info"):
            self.gcode.respond_info(self.format_message("INFO", "", message))

    def warn(self, message):
        if self.should_output("warn"):
            self.gcode.respond_info(self.format_message("WARNING", "🟡", message))

    def verbose(self, message):
        if self.should_output("verbose"):
            self.gcode.respond_info(self.format_message("VERBOSE", "🟣", message))

    def error(self, message):
        if self.should_output("error"):
            self.gcode.respond_info(self.format_message("ERROR", "🔴", message))
