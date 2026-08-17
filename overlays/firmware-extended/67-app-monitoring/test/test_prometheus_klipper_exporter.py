#!/usr/bin/env python3
import importlib.util
import pathlib
import re

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT_DIR / "root/usr/local/bin/prometheus-klipper-exporter.py"

spec = importlib.util.spec_from_file_location("prometheus_klipper_exporter", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def metric_lines(output):
    return [line for line in output.splitlines() if line and not line.startswith("#")]


def assert_metric(line):
    assert re.match(r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^{}]*\})? [-+]?([0-9]+(\.[0-9]+)?|NaN|Inf|-Inf)$', line), line


def test_output():
    calls = []

    def fetch_json(target, path, apikey):
        calls.append(path)
        return {
            "result": {
                "status": {
                    "heater_bed": {"power": 0.5, "target": 60.0, "temperature": 55.0},
                    "temperature_sensor cavity": {"temperature": 42.0, "measured_max_temp": 50.0, "measured_min_temp": 21.0},
                    "fan_generic cavity_fan": {"rpm": 1200.0, "speed": 0.75},
                    "filament_detect": {
                        "info": [
                            {"MAIN_TYPE": "PLA", "OFFICIAL": True, "WEIGHT": 1000},
                            {"MAIN_TYPE": "NONE", "OFFICIAL": False, "WEIGHT": 0},
                        ],
                        "state": [1, 0],
                    },
                    "print_stats": {"filament_used": 12.5, "print_duration": 100.0, "state": "printing", "total_duration": 120.0},
                    "system_stats": {"cputime": 10.0, "memavail": 2048, "sysload": 0.25},
                    "toolhead": {"max_accel": 20000.0, "max_velocity": 500.0, "print_time": 99.0, "stalls": 0},
                    "extruder": {"power": 0.3, "pressure_advance": 0.02, "smooth_time": 0.04, "target": 220.0, "temperature": 215.0},
                }
            }
        }

    old_fetch_json = exporter.fetch_json
    old_time = exporter.time.time
    try:
        exporter.fetch_json = fetch_json
        exporter.time.time = lambda: 1234567890
        output = exporter.scrape("example:7125", None, ["modules", "heater_bed", "temperature_sensor cavity", "fan_generic cavity_fan", "filament_detect", "print_stats", "system_stats", "toolhead", "extruder"])
    finally:
        exporter.fetch_json = old_fetch_json
        exporter.time.time = old_time

    lines = metric_lines(output)
    for line in lines:
        assert_metric(line)

    assert len(calls) == 1
    assert "modules" not in calls[0]
    assert "heater_bed" in calls[0]
    assert "temperature_sensor%20cavity" in calls[0]
    assert "klipper_up 1" in lines
    assert "klipper_scrape_time 1234567890" in lines
    assert "klipper_objects 8" in lines
    assert "klipper_heater_bed_temperature 55.0" in lines
    assert "klipper_temperature_sensor_temperature{sensor=\"cavity\"} 42.0" in lines
    assert "klipper_generic_fan_speed{fan=\"cavity_fan\"} 0.75" in lines
    assert "klipper_print_state{state=\"printing\"} 1" in lines
    assert "klipper_info_main_type{object=\"filament_detect\",index=\"0\",info_main_type=\"PLA\"} 1" in lines


def test_parse_object_name():
    assert exporter.parse_object_name("tmc22") == ("tmc22", None)
    assert exporter.parse_object_name("tmc2209 stepper_x") == ("tmc2209", "stepper_x")


def test_parse_object_list():
    assert exporter.parse_object_list(["modules,heater_bed", "toolhead"]) == ["heater_bed", "toolhead"]
    assert exporter.parse_object_list([]) == []


def test_scrape_without_objects():
    output = exporter.scrape("example:7125", None, [])
    lines = metric_lines(output)
    assert "klipper_up 1" in lines
    assert any(line.startswith("klipper_scrape_time ") for line in lines)
    assert "klipper_objects 0" in lines
    assert len(lines) == 3


def test_object_labels():
    assert exporter.object_labels("tmc22") == {"object": "tmc22"}
    assert exporter.object_labels("tmc2209 stepper_x") == {"object": "tmc2209", "name": "stepper_x"}


def test_emit_standard_uses_parsed_kind_instance():
    lines = exporter.emit_standard("temperature_sensor cavity", {"temperature": 42.0})
    assert "klipper_temperature_sensor_temperature{sensor=\"cavity\"} 42.0" in lines


def test_help_type_lines_present():
    output = exporter.scrape("example:7125", None, [])
    assert "# HELP klipper_up klipper_up" in output
    assert "# TYPE klipper_up gauge" in output


def test_do_get_uses_default_objects_when_query_missing():
    old_scrape = exporter.scrape
    old_default_objects = exporter.Handler.default_objects
    old_default_target = exporter.Handler.default_target
    captured = {}

    def fake_scrape(target, apikey, objects):
        captured["target"] = target
        captured["objects"] = objects
        return "klipper_up 1\n"

    class LocalHandler(exporter.Handler):
        def __init__(self):
            self.path = "/probe"
            self.headers = {}
            self.wfile = type("W", (), {"write": lambda *_: None})()

        def send_response(self, code):
            pass

        def send_header(self, key, value):
            pass

        def end_headers(self):
            pass

    try:
        exporter.scrape = fake_scrape
        exporter.Handler.default_target = "localhost:7125"
        exporter.Handler.default_objects = ["heater_bed", "toolhead"]
        handler = LocalHandler()
        handler.do_GET()
    finally:
        exporter.scrape = old_scrape
        exporter.Handler.default_objects = old_default_objects
        exporter.Handler.default_target = old_default_target

    assert captured["target"] == "localhost:7125"
    assert captured["objects"] == ["heater_bed", "toolhead"]


def test_do_get_query_objects_override_defaults():
    old_scrape = exporter.scrape
    old_default_objects = exporter.Handler.default_objects
    old_default_target = exporter.Handler.default_target
    captured = {}

    def fake_scrape(target, apikey, objects):
        captured["target"] = target
        captured["objects"] = objects
        return "klipper_up 1\n"

    class LocalHandler(exporter.Handler):
        def __init__(self):
            self.path = "/probe?target=example:7125&objects=modules,heater_bed&objects=temperature_sensor%20cavity"
            self.headers = {}
            self.wfile = type("W", (), {"write": lambda *_: None})()

        def send_response(self, code):
            pass

        def send_header(self, key, value):
            pass

        def end_headers(self):
            pass

    try:
        exporter.scrape = fake_scrape
        exporter.Handler.default_target = "localhost:7125"
        exporter.Handler.default_objects = ["toolhead"]
        handler = LocalHandler()
        handler.do_GET()
    finally:
        exporter.scrape = old_scrape
        exporter.Handler.default_objects = old_default_objects
        exporter.Handler.default_target = old_default_target

    assert captured["target"] == "example:7125"
    assert captured["objects"] == ["modules,heater_bed", "temperature_sensor cavity"]


def test_main_sets_default_objects():
    old_argv = exporter.sys.argv
    old_default_objects = exporter.Handler.default_objects
    old_server = exporter.ThreadingHTTPServer

    class DummyServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def serve_forever(self):
            pass

    try:
        exporter.ThreadingHTTPServer = DummyServer
        exporter.sys.argv = [
            "prometheus-klipper-exporter.py",
            "-moonraker.objects",
            "modules,heater_bed,toolhead",
        ]
        exporter.main()
    finally:
        exporter.sys.argv = old_argv
        exporter.Handler.default_objects = old_default_objects
        exporter.ThreadingHTTPServer = old_server

    assert exporter.parse_object_list(["modules,heater_bed,toolhead"]) == ["heater_bed", "toolhead"]


def test_target_url():
    assert exporter.target_url("localhost:7125", "/printer/objects/list") == "http://localhost:7125/printer/objects/list"
    assert exporter.target_url("http://u1eth.home:7125", "/printer/objects/list") == "http://u1eth.home:7125/printer/objects/list"


if __name__ == "__main__":
    test_output()
    test_parse_object_name()
    test_parse_object_list()
    test_scrape_without_objects()
    test_object_labels()
    test_emit_standard_uses_parsed_kind_instance()
    test_help_type_lines_present()
    test_do_get_uses_default_objects_when_query_missing()
    test_do_get_query_objects_override_defaults()
    test_main_sets_default_objects()
    test_target_url()


def test_target_url():
    assert exporter.target_url("localhost:7125", "/printer/objects/list") == "http://localhost:7125/printer/objects/list"
    assert exporter.target_url("http://u1eth.home:7125", "/printer/objects/list") == "http://u1eth.home:7125/printer/objects/list"


if __name__ == "__main__":
    test_output()
    test_target_url()
