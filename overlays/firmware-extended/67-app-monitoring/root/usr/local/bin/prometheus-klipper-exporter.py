#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_TARGET = "localhost:7125"
DEFAULT_OBJECTS = [
    "extruder",
    "heater_bed",
    "temperature_sensor",
    "fan",
    "fan_generic",
    "heater_fan",
    "print_stats",
    "system_stats",
    "toolhead",
]

STANDARD_MAPPING = {
    "extruder": {
        "instance": False,
        "labels": {"kind": "extruder", "extruder": "{name}"},
        "fields": {
            "temperature": "klipper_temperature_value",
            "target": "klipper_temperature_target",
            "power": "klipper_power_value",
            "pressure_advance": "klipper_extruder_pressure_advance",
            "smooth_time": "klipper_extruder_smooth_time",
        },
    },
    "fan": {
        "instance": False,
        "labels": {"kind": "fan", "fan": "fan"},
        "fields": {
            "rpm": "klipper_fan_rpm",
            "speed": "klipper_fan_speed",
        },
    },
    "fan_generic": {
        "instance": True,
        "labels": {"kind": "fan_generic", "fan": "{instance}"},
        "fields": {
            "rpm": "klipper_fan_rpm",
            "speed": "klipper_fan_speed",
        },
    },
    "heater_bed": {
        "instance": False,
        "labels": {"kind": "heater_bed"},
        "fields": {
            "temperature": "klipper_temperature_value",
            "target": "klipper_temperature_target",
            "power": "klipper_power_value",
        },
    },
    "heater_fan": {
        "instance": True,
        "labels": {"kind": "heater_fan", "fan": "{instance}"},
        "fields": {
            "rpm": "klipper_fan_rpm",
            "speed": "klipper_fan_speed",
        },
    },
    "print_stats": {
        "instance": False,
        "labels": {},
        "fields": {
            "filament_used": "klipper_print_filament_used",
            "print_duration": "klipper_print_print_duration",
            "total_duration": "klipper_print_total_duration",
        },
    },
    "system_stats": {
        "instance": False,
        "labels": {},
        "fields": {
            "cputime": "klipper_system_cputime",
            "memavail": "klipper_system_memavail",
            "sysload": "klipper_system_sysload",
        },
    },
    "temperature_sensor": {
        "instance": True,
        "labels": {"kind": "temperature_sensor", "sensor": "{instance}"},
        "fields": {
            "temperature": "klipper_temperature_value",
            "measured_max_temp": "klipper_temperature_max",
            "measured_min_temp": "klipper_temperature_min",
            "estimated_expansion": "klipper_temperature_sensor_estimated_expansion",
        },
    },
    "toolhead": {
        "instance": False,
        "labels": {},
        "fields": {
            "estimated_print_time": "klipper_toolhead_estimated_print_time",
            "max_accel": "klipper_toolhead_max_accel",
            "max_velocity": "klipper_toolhead_max_velocity",
            "print_time": "klipper_toolhead_print_time",
            "square_corner_velocity": "klipper_toolhead_square_corner_velocity",
            "stalls": "klipper_toolhead_stalls",
        },
    },
}

REQUEST_TIMEOUT = 5

def metric_name(*parts):
    name = "_".join(str(part) for part in parts if part != "")
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower()
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or name[0].isdigit():
        name = "value_" + name
    return name


def label_value(value):
    return str(value).replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


def labels(values):
    if not values:
        return ""
    return "{" + ",".join(f'{key}="{label_value(value)}"' for key, value in values.items()) + "}"


def sample(name, value, values=None):
    return f"{name}{labels(values)} {value}"


def number(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def target_url(target, path):
    if not target:
        target = DEFAULT_TARGET
    if "://" not in target:
        target = "http://" + target
    parsed = urllib.parse.urlsplit(target)
    if not parsed.netloc:
        raise ValueError("invalid target")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def fetch_json(target, path, apikey):
    headers = {}
    if apikey:
        headers["X-Api-Key"] = apikey
    request = urllib.request.Request(target_url(target, path), headers=headers)
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.load(response)


def object_query_path(objects):
    return "/printer/objects/query?" + "&".join(urllib.parse.quote(obj, safe="") for obj in objects)


def object_list_path():
    return "/printer/objects/list"


def parse_object_name(name):
    if " " not in name:
        return name, None
    kind, instance = name.split(" ", 1)
    return kind, instance


def parse_object_list(values):
    objects = []
    for value in values or []:
        for item in value.split(","):
            normalized = item.strip()
            if normalized:
                objects.append(normalized)
    return objects


def resolve_query_objects(available, requested):
    resolved = []
    requested_set = set(requested)
    for name in sorted(available):
        kind, _ = parse_object_name(name)
        if name in requested_set or kind in requested_set:
            resolved.append(name)
    return resolved


def object_labels(name):
    kind, instance = parse_object_name(name)
    values = {"object": kind}
    if instance:
        values["name"] = instance
    return values


def resolve_mapping_labels(label_mapping, name, instance):
    values = {}
    for key, value in label_mapping.items():
        if value == "{name}":
            values[key] = name
        elif value == "{instance}":
            values[key] = instance
        else:
            values[key] = value
    return values


def emit_standard(name, data):
    lines = []
    consumed = set()
    kind, instance = parse_object_name(name)
    mapping = STANDARD_MAPPING.get(kind)
    if mapping:
        expects_instance = mapping["instance"]
        if (expects_instance and instance) or (not expects_instance and instance is None):
            values = resolve_mapping_labels(mapping["labels"], name, instance)
            for field, metric in mapping["fields"].items():
                value = number(data.get(field))
                if value is not None:
                    lines.append(sample(metric, value, values))
                    consumed.add(field)
    if kind == "print_stats" and instance is None and "state" in data:
        lines.append(sample("klipper_print_state", 1, {"state": data.get("state")}))
        consumed.add("state")
    return lines, consumed


def emit_flattened(prefix, base_labels, value, path=(), excluded_fields=None):
    lines = []
    excluded_fields = excluded_fields or set()
    if len(path) == 1 and path[0] in excluded_fields:
        return lines
    numeric = number(value)
    if numeric is not None:
        lines.append(sample(metric_name(prefix, *path), numeric, base_labels))
    elif isinstance(value, str) or value is None:
        if path:
            field = metric_name(*path)
            metric = metric_name(prefix, field)
            text = "none" if value is None else value
            lines.append(sample(metric, 1, {**base_labels, field: text}))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            lines.extend(emit_flattened(prefix, {**base_labels, "index": str(index)}, item, path, excluded_fields))
    elif isinstance(value, dict):
        for key, item in sorted(value.items()):
            lines.extend(emit_flattened(prefix, base_labels, item, path + (key,), excluded_fields))
    return lines


def add_help_and_type(lines):
    output = []
    seen = set()
    for line in lines:
        metric = line.split("{", 1)[0].split(" ", 1)[0]
        if metric not in seen:
            output.append(f"# HELP {metric} {metric}")
            output.append(f"# TYPE {metric} gauge")
            seen.add(metric)
        output.append(line)
    return output


def scrape(target, apikey, objects):
    lines = [sample("klipper_up", 1)]
    lines.append(sample("klipper_scrape_time", int(time.time())))
    requested_objects = parse_object_list(objects)
    available_objects = fetch_json(target, object_list_path(), apikey)["result"]["objects"]
    query_objects = resolve_query_objects(available_objects, requested_objects)
    if query_objects:
        status = fetch_json(target, object_query_path(query_objects), apikey)["result"]["status"]
    else:
        status = {}
    lines.append(sample("klipper_objects", len(status)))
    for name, data in sorted(status.items()):
        standard_lines, consumed_fields = emit_standard(name, data)
        lines.extend(standard_lines)
        lines.extend(emit_flattened("klipper", object_labels(name), data, excluded_fields=consumed_fields))
    return "\n".join(add_help_and_type(lines)) + "\n"


class Handler(BaseHTTPRequestHandler):
    default_target = DEFAULT_TARGET
    default_objects = []
    apikey = None

    def do_GET_healthy(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK\n")

    def do_GET_metrics(self):
        body = (sample("klipper_exporter_up", 1) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET_probe(self, parsed):
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("target", [self.default_target])[0]
        objects = params.get("objects") or self.default_objects
        try:
            body = scrape(target, self.apikey, objects).encode()
            self.send_response(200)
        except Exception as error:
            body = (sample("klipper_up", 0) + "\n" + sample("klipper_scrape_error", 1, {"error": type(error).__name__}) + "\n").encode()
            self.send_response(502)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/-/healthy":
            self.do_GET_healthy()
        elif parsed.path == "/metrics":
            self.do_GET_metrics()
        elif parsed.path == "/probe":
            self.do_GET_probe(parsed)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)


def listen_address(value):
    if value.startswith(":"):
        return "", int(value[1:])
    if ":" not in value:
        return "", int(value)
    host, port = value.rsplit(":", 1)
    return host, int(port)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-web.listen-address", default=":9101", dest="listen_address")
    parser.add_argument("-moonraker.apikey", default=os.environ.get("MOONRAKER_APIKEY"), dest="apikey")
    parser.add_argument("-moonraker.address", default=os.environ.get("MOONRAKER_ADDRESS", DEFAULT_TARGET), dest="default_target")
    parser.add_argument("-moonraker.objects", default=os.environ.get("MOONRAKER_OBJECTS"), dest="default_objects")
    args = parser.parse_args()
    Handler.default_target = args.default_target
    Handler.default_objects = parse_object_list([args.default_objects]) if args.default_objects else list(DEFAULT_OBJECTS)
    Handler.apikey = args.apikey
    server = ThreadingHTTPServer(listen_address(args.listen_address), Handler)
    print(f"Serving prometheus-klipper-exporter on {args.listen_address}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
