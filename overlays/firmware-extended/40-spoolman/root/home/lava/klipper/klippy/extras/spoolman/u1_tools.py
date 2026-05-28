import os, json
EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
MAX_TOOLS_MAX_INDEX = MAX_TOOLS_COUNT-1

def load_print_task(printer):
    path = os.path.join(printer.get_snapmaker_config_dir(), "print_task.json")
    with open(path, "r") as file:
        configfile = json.load(file)
        return configfile

def load_extruder_map(printer):
    return load_print_task(printer).get("extruder_map_table", [])

def load_spools_config(printer):
    path = os.path.join(printer.get_snapmaker_config_dir(), "print_task.json")
    with open(path, "r") as file:
        data = json.load(file)

    vendors = data.get("filament_vendor", [])
    types = data.get("filament_type", [])
    sub_types = data.get("filament_sub_type", [])
    colors = data.get("filament_color", [])
    color_rgba = data.get("filament_color_rgba", [])
    officials = data.get("filament_official", [])
    sku = data.get("filament_sku", [])
    spool_id = data.get("filament_spool_id", [])

    count = max(
        len(vendors), len(types), len(sub_types),
        len(colors), len(color_rgba), len(officials),
        len(sku), len(spool_id)
    )

    spools = []

    def get(arr, i, default=None):
        return arr[i] if i < len(arr) else default

    for i in range(count):
        spools.append({
            "VENDOR": get(vendors, i, "NONE"),
            "MAIN_TYPE": get(types, i, "NONE"),
            "SUB_TYPE": get(sub_types, i, "NONE"),
            "COLOR": get(colors, i, "FFFFFFFF"),
            "ARGB_COLOR": get(color_rgba, i, "FFFFFFFF"),
            "OFFICIAL": get(officials, i, False),
            "SKU": get(sku, i, "0"),
            "SPOOL_ID": get(spool_id, i, "0")
        })

    return spools

class U1Tools:
    def __init__(self, config, logs):
        self.printer = config if not hasattr(config, "get_printer") else config.get_printer()
        self.logs = logs
        self.extruder_map_table = [None] * MAX_TOOLS_COUNT

    def update_map(self):
        self.extruder_map_table = load_extruder_map(self.printer)
        self.logs.verbose(f"Tools-extruders map updated: {self.extruder_map_table}")

    def clear_map(self):
        self.extruder_map_table = [None] * MAX_TOOLS_COUNT
        self.logs.verbose("Tools-extruders map cleared.")

    def extruder_for_tool(self, tool_id):
        extruder = self.extruder_map_table[tool_id]
        if extruder is None:
            self.logs.error(f"Cannot resolve extruder for T{tool_id}. This should not happen and could be an issue. Please save your print_task.json for debugging, you can find it in your snapmaker folder via your printer's web UI")
        else:
            self.logs.verbose(f"Tool {tool_id} is Extruder {extruder}")

        return extruder

    def get_spools_config(self):
        return load_spools_config(self.printer)
