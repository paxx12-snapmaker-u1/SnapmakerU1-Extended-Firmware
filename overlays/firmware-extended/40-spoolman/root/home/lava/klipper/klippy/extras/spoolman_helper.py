import json
from .spoolman.u1_tools import U1Tools
from .spoolman.spoolman import Spoolman
from .spoolman.macros import Macros
from .spoolman.logs import Logs
from .spoolman.commands import Commands
from .spoolman.print_lifecycle import PrintLifecycle

EXTRUDERS_COUNT = 4
MAX_TOOLS_COUNT = 32
MAX_TOOLS_MAX_INDEX = MAX_TOOLS_COUNT-1

def filament_info_to_string(filament_info, level="info"):
    if not filament_info:
        return "- Missing Filament Info! -"

    vendor = filament_info.get("VENDOR")
    main = filament_info.get("MAIN_TYPE")
    sub = filament_info.get("SUB_TYPE")
    colour = filament_info.get("ARGB_COLOR")
    spool_id = filament_info.get("SPOOL_ID")
    sku = filament_info.get("SKU")

    base = f"{vendor} {main} {sub} (colour: #{colour}, Spoolman id: {spool_id}, sku: {sku})"

    if level != "debug":
        return base

    known_keys = {"VENDOR", "MAIN_TYPE", "SUB_TYPE", "ARGB_COLOR", "SPOOL_ID", "SKU"}

    extras = [
        f"{key}->{value}"
        for key, value in filament_info.items()
        if key not in known_keys
    ]

    if not extras:
        return base

    return base + "\nadditional filament info: " + ", ".join(extras)

def update_objects_list(original, updates):
    count = min(len(original), len(updates))

    for i in range(count):
        update = updates[i]
        if not update:
            continue

        for key, value in update.items():
            if value is not None:
                if original[i]:
                    original[i][key] = value

    return original

def is_untagged_filament(filament_info):
    vendor = filament_info.get("VENDOR")
    return vendor == "NONE" or vendor == "Generic"

class SpoolmanHelper:
    possible_modes = ["manual", "auto"]
    def __init__(self, config):
        self.printer = config if not hasattr(config, "get_printer") else config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

        self.logging = config.get("logging", "info") if hasattr(config, "get") else "info"
        mode = config.get("mode", "auto") if hasattr(config, "get") else "auto"
        if mode not in self.possible_modes:
            mode = "auto"
        self.mode = mode

        self.logs = Logs(self.printer, self)
        self.u1_tools = U1Tools(config, self.logs)
        self.spoolman = Spoolman(self.printer, self.logs)
        self.macros = Macros(self.printer, self.logs)
        self.commands = Commands(self.printer, self.logs, self)
        self.lifecycle = PrintLifecycle(self.printer, self.logs, self)

        self.spool_holders = [None] * EXTRUDERS_COUNT
        self.spools_by_id = {}

        self.u1_tools.update_map()
        self.printer.register_event_handler("klippy:ready", self._on_ready)

    def _on_ready(self):
        self.logs.log(f"Loaded! mode: {self.mode}, logs level: {self.logging} klippy:ready, detecting spools")
        self.detect_spools()

    def set_spool_for_channel(self, channel, filament_info):
        extruder = channel # renaming channel to extruder for sake of not getting crazy with naming

        self.logs.verbose(f"Received spool for extruder {extruder}")
        if not (0 <= extruder <= 3):
            self.logs.error("Extruder must be 0..3")
            return

        self.spool_holders[extruder] = filament_info
        if not is_untagged_filament(filament_info):
            self.apply_spool_for_extruder(extruder)

    def clear_spool_for_channel(self, channel):
        extruder = channel # renaming channel to extruder for sake of not getting crazy with naming
        self.logs.log(f"Clearing spool from extruder {extruder}")

        if extruder == None:
            self.logs.error(f"Null extruder ({extruder}) while clearling spool for channel {channel}")
            return

        tool = f"T{extruder}"
        self.macros.set_spool_id_for_tool(tool, None)
        holder = self.spool_holders[extruder]
        if holder != None:
            spool_id = holder.get("SPOOL_ID")
            if spool_id and self.spools_by_id[spool_id]:
                del self.spools_by_id[spool_id]

        self.spool_holders[extruder] = None

    def find_spool_for_tool(self, tool_id):
        macro_spool = self.get_spool_for_tool(tool_id)
        mapped_spool = self.get_mapped_spool_for_tool(tool_id)

        self.logs.verbose(f"Possible spools: macro->{macro_spool}, mapped->{mapped_spool}")

        match self.mode:
            case 'manual':
                result = macro_spool or mapped_spool
                self.logs.verbose(f"Tool T{tool_id} has filament {filament_info_to_string(result, self.logging)}")

            case 'auto':
                return mapped_spool if mapped_spool and "SPOOL_ID" in mapped_spool else macro_spool
            case _:
                return mapped_spool if mapped_spool and "SPOOL_ID" in mapped_spool else macro_spool

    def apply_spool_for_extruder(self, extruder):
        self.logs.verbose(f"Trying to bind spool to extruder {extruder}")
        spool = self.spool_holders[extruder]

        if not spool:
            self.logs.warn(f"No filament info for extruder {extruder}.  This is normal if your spool does not have an RFID tag, please manually configure a spool for T{extruder}")
            return

        self.logs.verbose(f"Resolving filament info {filament_info_to_string(spool, self.logging)} for extruder {extruder}. Looking for spool...")
        def on_resolve_spool(resolved, spool=spool):
            if resolved:    
                if not spool["SPOOL_ID"] and resolved["id"]:
                    spool["SPOOL_ID"] = resolved["id"]

                spool_id = (spool.get("SPOOL_ID")
                    or resolved["id"]
                )
            else:
                spool_id = spool.get("SPOOL_ID")

            if not spool_id:
                self.logs.warn(f"Unable to resolve spool id for extruder {extruder} and filament {filament_info_to_string(spool, self.logging)}. Please ensure your tag carries the needed information and/or refer to the documentation for further informations")
                return

            self.spools_by_id[spool_id] = spool

            self.logs.verbose(f"Got spool_id: {spool_id} for filament {filament_info_to_string(spool, self.logging)}")

            tool = f"T{extruder}"

            self.logs.log(f"Tool {tool} is using: {filament_info_to_string(spool, self.logging)}")
            self.macros.set_spool_id_for_tool(tool, spool_id)

        self.spoolman.resolve_spool(spool, on_resolve_spool)

    def get_spool_for_tool(self, tool_id):
        spool_id = self.macros.get_spool_id_for_tool(tool_id)
        if spool_id:
            return self.spools_by_id.get(spool_id, {"SPOOL_ID": spool_id})

    def get_mapped_spool_for_tool(self, tool_id):
        self.logs.verbose(f"Resvolving extruder for T{tool_id} thruought tools mapping")
        extruder = self.u1_tools.extruder_for_tool(tool_id)

        if extruder is None:
            self.logs.warn(f"Cannot find mapped extruder for T{tool_id}. This shuold not happen, please save your `configuration -> /snapmaker/print_task.json` for further analisys")
            return None

        spool = self.spool_holders[extruder]
        if is_untagged_filament(spool):
            self.logs.verbose(f"Filament for T{extruder} is untagged, falling back to manually configured one")
            spool = self.get_spool_for_tool(extruder)

        if spool is None:
            self.logs.warn(f"Cannot find filament info for T{tool_id} on extruder {extruder}. This is normal if your spool does not have an RFID tag, please manually configure a spool for T{tool_id} or refer to logs and documentation for further informations")
            return None

        self.logs.verbose(f"Found filament for requested tool T{tool_id} on extruder {extruder}: {filament_info_to_string(spool, self.logging)}")
        return spool

    def set_active_tool(self, tool_id):
        spool = self.find_spool_for_tool(tool_id)

        self.logs.verbose(f"Spool for requested tool: {spool}")

        if not (spool and spool.get("SPOOL_ID")):
            self.logs.warn("Cannot set active spool for tool T{tool_id}: unalble to resolve spool id. Please check logs, your tags, and configuration.")
            return

        self.logs.log(f"Tracking: {filament_info_to_string(spool, self.logging)}")
        self.spoolman.set_active_spool(spool["SPOOL_ID"])

    def sync_spools_tools(self):
        def manual_mode():
            for tool_id in len(MAX_TOOLS_COUNT):
                spool_id = self.macros.get_spool_id_for_tool(tool_id)
                def on_spool(spool):
                    self.spools_by_id[spool_id] = spool
                self.spoolman.resolve_spool({"SPOOL_ID": spool_id}, on_spool)

        def auto_mode():
            self.u1_tools.update_map()

        if self.mode == 'manual':
            manual_mode()
        else:
            auto_mode()

    def detect_spools(self):
        spools = self.u1_tools.get_spools_config()
        self.logs.debug(f"detect_spools spools: {spools}, holders: {self.spool_holders}")
        update_objects_list(self.spool_holders, spools)
        for extruder in range(len(spools)):
            self.macros.detect_spool(extruder)
            self.apply_spool_for_extruder(extruder)

    def dump(self, raw):
        if raw:
            self.logs.log(f"\nspool_holders: {json.dumps(self.spool_holders, indent=2)}\nspools_by_id: {self.spools_by_id}");
            return

        self.logs.log("Dumping Spool Holders: ")
        for spool in self.spool_holders:
            self.logs.log(filament_info_to_string(spool, self.logging))

    def clear_spool_ids(self):
        self.spools_by_id = {}

def load_config(config):
    return SpoolmanHelper(config)
