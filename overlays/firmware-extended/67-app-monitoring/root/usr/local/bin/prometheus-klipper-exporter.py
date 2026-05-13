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
            if normalized and normalized != "modules":
                objects.append(normalized)
    return objects


def object_labels(name):
    kind, instance = parse_object_name(name)
    values = {"object": kind}
    if instance:
        values["name"] = instance
    return values


def emit_standard(name, data):
    lines = []
    kind, instance = parse_object_name(name)
    if kind == "extruder" and instance is None:
        values = {"extruder": name}
        for field in ("power", "pressure_advance", "smooth_time", "target", "temperature"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_extruder_{field}", value, values))
    elif kind == "fan" and instance is None:
        for field in ("rpm", "speed"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_fan_{field}", value))
    elif kind == "fan_generic" and instance:
        values = {"fan": instance}
        for field in ("rpm", "speed"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_generic_fan_{field}", value, values))
    elif kind == "heater_fan" and instance:
        values = {"fan": instance}
        for field in ("rpm", "speed"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_heater_fan_{field}", value, values))
    elif kind == "heater_bed" and instance is None:
        for field in ("power", "target", "temperature"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_heater_bed_{field}", value))
    elif kind == "temperature_sensor" and instance:
        values = {"sensor": instance}
        for field in ("temperature", "measured_max_temp", "measured_min_temp", "estimated_expansion"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_temperature_sensor_{field}", value, values))
    elif kind == "print_stats" and instance is None:
        for field in ("filament_used", "print_duration", "total_duration"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_print_{field}", value))
        if "state" in data:
            lines.append(sample("klipper_print_state", 1, {"state": data.get("state")}))
    elif kind == "system_stats" and instance is None:
        for field in ("cputime", "memavail", "sysload"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_system_{field}", value))
    elif kind == "toolhead" and instance is None:
        for field in ("estimated_print_time", "max_accel", "max_velocity", "print_time", "square_corner_velocity", "stalls"):
            value = number(data.get(field))
            if value is not None:
                lines.append(sample(f"klipper_toolhead_{field}", value))
    return lines


def emit_flattened(prefix, base_labels, value, path=()):
    lines = []
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
            lines.extend(emit_flattened(prefix, {**base_labels, "index": str(index)}, item, path))
    elif isinstance(value, dict):
        for key, item in sorted(value.items()):
            lines.extend(emit_flattened(prefix, base_labels, item, path + (key,)))
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
    query_objects = parse_object_list(objects)
    if query_objects:
        status = fetch_json(target, object_query_path(query_objects), apikey)["result"]["status"]
    else:
        status = {}
    lines.append(sample("klipper_objects", len(status)))
    for name, data in sorted(status.items()):
        lines.extend(emit_standard(name, data))
        lines.extend(emit_flattened("klipper", object_labels(name), data))
    return "\n".join(add_help_and_type(lines)) + "\n"


class Handler(BaseHTTPRequestHandler):
    default_target = DEFAULT_TARGET
    default_objects = []
    apikey = None

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/-/healthy":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK\n")
            return
        if parsed.path not in ("/probe", "/metrics"):
            self.send_response(404)
            self.end_headers()
            return
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
    parser.add_argument("-moonraker.objects", default=os.environ.get("MOONRAKER_OBJECTS", ""), dest="default_objects")
    args = parser.parse_args()
    Handler.default_target = args.default_target
    Handler.default_objects = parse_object_list([args.default_objects])
    Handler.apikey = args.apikey
    server = ThreadingHTTPServer(listen_address(args.listen_address), Handler)
    print(f"Serving prometheus-klipper-exporter on {args.listen_address}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
