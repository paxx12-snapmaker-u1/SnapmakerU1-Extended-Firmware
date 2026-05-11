#!/usr/bin/env python3

import argparse
import glob
import json
import os
import subprocess
import time
import fcntl
import yaml
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

def deep_merge(base, override):
    """Deep merge override into base, modifying base in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base

def load_functions_from_dir(functions_dir):
    """Load and deep merge all YAML files from a directory in sorted order."""
    config = {'links': {}, 'settings': {}, 'actions': {}, 'status': {}, 'upgrade_url': {}, 'upgrade_upload': {}}

    if not os.path.isdir(functions_dir):
        log(f"Functions directory not found: {functions_dir}")
        return config

    yaml_files = sorted(glob.glob(os.path.join(functions_dir, '*.yaml')))
    yaml_files += sorted(glob.glob(os.path.join(functions_dir, '*.yml')))
    yaml_files = sorted(set(yaml_files))

    for yaml_file in yaml_files:
        try:
            with open(yaml_file, 'r') as f:
                data = yaml.safe_load(f)
                if data:
                    deep_merge(config, data)
            log(f"Loaded functions from: {os.path.basename(yaml_file)}")
        except Exception as e:
            log(f"Error loading {yaml_file}: {e}")

    return config

def log(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def shell_to_cmd(shell, *args):
    cmd = ["/bin/bash", "-c", shell]
    if args:
        cmd.append("--")
        cmd.extend(args)
    return cmd

class FirmwareConfigHandler(SimpleHTTPRequestHandler):
    html_dir = None
    functions = {'settings': {}, 'links': {}, 'actions': {}, 'status': {}, 'upgrade_url': {}, 'upgrade_upload': {}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self.html_dir, **kwargs)

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self.handle_status()
        elif path == "/api/settings":
            self.handle_get_settings()
        elif path == "/api/links":
            self.handle_get_links()
        elif path == "/api/actions":
            self.handle_get_actions()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/upgrade/url":
            self.handle_upgrade_url()
        elif parsed.path == "/api/upgrade/upload":
            self.handle_upgrade_upload()
        elif parsed.path.startswith("/api/settings/"):
            path_parts = parsed.path[14:].split('/')
            if len(path_parts) == 2:
                option = path_parts[0]
                value = path_parts[1]
                self.handle_update_setting(option, value)
            else:
                self.send_error(404, "Invalid settings path")
        elif parsed.path.startswith("/api/action/"):
            path_parts = parsed.path[12:].split('/')
            action = path_parts[0] if path_parts else None
            is_download = len(path_parts) > 1 and path_parts[1] == 'download'

            if not action:
                self.send_error(404, "Action not specified")
            elif is_download:
                self.handle_action_download(action)
            else:
                self.handle_action(action)
        else:
            self.send_error(404, "Not Found")

    def _start_text_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _write_stream_chunk(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def _finish_text_stream(self):
        pass

    def _stream_command(self, cmd, stop_token=None):
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        try:
            for line in iter(process.stdout.readline, ""):
                self._write_stream_chunk(line)
                if stop_token and stop_token in line:
                    return 0, True
            process.wait()
            return process.returncode, False
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    def handle_action(self, action):
        try:
            cfg = self._get_action_config(action)
            if not cfg:
                self.send_error(400, f"Unknown action: {action}")
                return

            log(f"Action: {action}")

            if cfg.get("background"):
                self._start_text_stream()
                self._write_stream_chunk(f"=== {action} ===\n")
                self._write_stream_chunk(f"{cfg['message']}\n")
                subprocess.Popen(cfg["cmd"], start_new_session=True)
                self._write_stream_chunk(f"\nSUCCESS: Completed successfully\n")
                self._finish_text_stream()
            else:
                self._start_text_stream()
                self._write_stream_chunk(f"=== {cfg.get('label', action)} ===\n")
                self._write_stream_chunk(f"{cfg['message']}\n")
                self._write_stream_chunk(f"\n")
                exit_code, _ = self._stream_command(cfg["cmd"])
                if exit_code == 0:
                    self._write_stream_chunk(f"\nSUCCESS: Completed successfully (exit code: 0)\n")
                else:
                    self._write_stream_chunk(f"\nERROR: Failed with exit code: {exit_code}\n")
                self._finish_text_stream()
        except Exception as e:
            log(f"Action error: {e}")
            self.send_error(500, str(e))

    def handle_action_download(self, action):
        try:
            cfg = self._get_action_config(action)
            if not cfg:
                self.send_error(400, f"Unknown action: {action}")
                return

            download_file = cfg.get("download_file")

            if not download_file:
                self.send_error(404, f"Action {action} does not provide a download file")
                return

            if not os.path.exists(download_file):
                self.send_error(404, f"Download file not found: {download_file}")
                return

            filename = os.path.basename(download_file)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            with open(download_file, "rb") as f:
                chunk_size = 64 * 1024
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            log(f"Action error: {e}")
            self.send_error(500, str(e))

    def send_json(self, data):
        response = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response))
        self.end_headers()
        self.wfile.write(response)

    def _run_status_cmd(self, cmd):
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def _check_condition(self, cmd):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def handle_status(self):
        try:
            status_config = self.functions.get('status', {})
            result = {}

            for section_key, section_cfg in status_config.items():
                if_cmd = section_cfg.get('if_cmd')
                if if_cmd and not self._check_condition(if_cmd):
                    continue

                section_items = []
                has_value = False
                for item in section_cfg.get('items', []):
                    value = self._run_status_cmd(item['cmd']) if 'cmd' in item else None
                    if value:
                        has_value = True
                    section_items.append({
                        'label': item.get('label', ''),
                        'value': value
                    })

                if if_cmd and not has_value:
                    continue

                result[section_key] = {
                    'title': section_cfg.get('title', section_key),
                    'hidden': section_cfg.get('hidden', False),
                    'items': section_items
                }

            self.send_json(result)
        except Exception as e:
            log(f"Status error: {e}")
            self.send_error(500, str(e))

    def _get_setting_config(self, setting_key):
        settings = self.functions.get('settings', {})
        for group_key, group_cfg in settings.items():
            items = group_cfg.get('items', {})
            if setting_key in items:
                return items[setting_key]
        return None

    def handle_get_settings(self):
        try:
            settings = self.functions.get('settings', {})
            result = {}
            for group_key, group_cfg in settings.items():
                group_label = group_cfg.get('label', group_key)
                items = group_cfg.get('items', {})
                settings_list = []
                for setting_id, config in items.items():
                    try:
                        cmd_result = subprocess.run(
                            config["get_cmd"],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        current_value = cmd_result.stdout.strip() if cmd_result.returncode == 0 else config.get("default", "")
                    except Exception:
                        current_value = config.get("default", "")

                    options_data = {}
                    for opt_key, opt_val in config["options"].items():
                        opt_info = {"label": opt_val["label"]}
                        if "confirm" in opt_val:
                            opt_info["confirm"] = opt_val["confirm"]
                        if opt_val.get("hidden"):
                            opt_info["hidden"] = True
                        options_data[opt_key] = opt_info

                    settings_list.append({
                        "id": setting_id,
                        "label": config["label"],
                        "description": config.get("description"),
                        "help_url": config.get("help_url"),
                        "current": current_value,
                        "hidden": config.get("hidden", False),
                        "options": options_data
                    })
                if settings_list:
                    result[group_key] = {
                        "label": group_label,
                        "items": settings_list
                    }

            self.send_json(result)
        except Exception as e:
            log(f"Get settings error: {e}")
            self.send_error(500, str(e))

    def handle_get_links(self):
        try:
            links = self.functions.get('links', {})
            settings_cache = {}

            for _, link_config in links.items():
                condition = link_config.get("condition")
                if condition:
                    setting_key = condition["setting"]
                    if setting_key not in settings_cache:
                        setting_config = self._get_setting_config(setting_key)
                        if setting_config:
                            try:
                                result = subprocess.run(
                                    setting_config["get_cmd"],
                                    capture_output=True,
                                    text=True,
                                    timeout=5
                                )
                                settings_cache[setting_key] = result.stdout.strip() if result.returncode == 0 else setting_config.get("default", "")
                            except Exception:
                                settings_cache[setting_key] = setting_config.get("default", "")

            links_list = []
            for _, link_config in links.items():
                condition = link_config.get("condition")

                if condition is None:
                    links_list.append({
                        "url": link_config["url"],
                        "icon": link_config["icon"],
                        "label": link_config["label"]
                    })
                else:
                    setting_key = condition["setting"]
                    required_value = condition["value"]
                    current_value = settings_cache.get(setting_key)

                    if current_value == required_value:
                        links_list.append({
                            "url": link_config["url"],
                            "icon": link_config["icon"],
                            "label": link_config["label"]
                        })

            self.send_json(links_list)
        except Exception as e:
            log(f"Get links error: {e}")
            self.send_error(500, str(e))

    def _get_action_config(self, action_key):
        actions = self.functions.get('actions', {})
        for group_key, group_cfg in actions.items():
            items = group_cfg.get('items', {})
            if action_key in items:
                return items[action_key]
        return None

    def handle_get_actions(self):
        try:
            actions_cfg = self.functions.get('actions', {})
            result = {}
            for group_key, group_cfg in actions_cfg.items():
                group_label = group_cfg.get('label', group_key)
                items = group_cfg.get('items', {})
                actions_list = []
                for action_id, cfg in items.items():
                    actions_list.append({
                        "id": action_id,
                        "label": cfg.get("label", action_id),
                        "description": cfg.get("description"),
                        "help_url": cfg.get("help_url"),
                        "confirm": cfg.get("confirm", False),
                        "background": cfg.get("background", False),
                        "download_file": cfg.get("download_file"),
                        "hidden": cfg.get("hidden", False)
                    })
                if actions_list:
                    result[group_key] = {
                        "label": group_label,
                        "items": actions_list
                    }

            self.send_json(result)
        except Exception as e:
            log(f"Get actions error: {e}")
            self.send_error(500, str(e))

    def handle_update_setting(self, setting_key, value):
        stream_started = False
        try:
            config = self._get_setting_config(setting_key)
            if not config:
                self.send_error(400, "Invalid setting key")
                return

            if value not in config["options"]:
                self.send_error(400, f"Invalid value. Must be one of: {', '.join(config['options'].keys())}")
                return

            option_config = config["options"][value]

            log(f"Updating setting {setting_key} to {value}")

            self._start_text_stream()
            stream_started = True

            self._write_stream_chunk(f"=== Updating {config['label']} ===\n")
            self._write_stream_chunk(f"Setting: {option_config['label']}\n")
            self._write_stream_chunk(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._write_stream_chunk(f"{'=' * 40}\n\n")

            # Execute the command for this option
            self._write_stream_chunk(f"Applying changes...\n")
            rc, _ = self._stream_command(option_config["cmd"])

            self._write_stream_chunk(f"\n{'=' * 40}\n")
            if rc == 0:
                self._write_stream_chunk(f"SUCCESS: Setting updated successfully\n")
            else:
                self._write_stream_chunk(f"ERROR: Command completed with exit code {rc}\n")
            self._finish_text_stream()

        except Exception as e:
            log(f"Update setting error: {e}")
            try:
                if stream_started:
                    try:
                        self._write_stream_chunk(f"\nError: {e}\n")
                        self._finish_text_stream()
                    except Exception:
                        pass
                else:
                    self.send_error(500, str(e))
            except Exception:
                pass

    def stream_multipart_to_file(self, field_name, target_path):
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[9:].strip('"')
                break
        if not boundary or content_length == 0:
            return None, 0
        boundary_bytes = f"--{boundary}".encode()
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        try:
            os.unlink(target_path)
        except FileNotFoundError:
            pass
        tf = open(target_path, "wb")
        try:
            fcntl.flock(tf, fcntl.LOCK_EX)
            in_file_content = False
            file_size = 0
            buf = b""
            chunk_size = 64 * 1024
            remaining = content_length
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                chunk = self.rfile.read(read_size)
                if not chunk:
                    break
                remaining -= len(chunk)
                buf += chunk
                if not in_file_content:
                    header_end = buf.find(b"\r\n\r\n")
                    if header_end != -1:
                        header_part = buf[:header_end]
                        if field_name.encode() in header_part:
                            in_file_content = True
                            buf = buf[header_end + 4:]
                        else:
                            buf = buf[header_end + 4:]
                if in_file_content:
                    end_pos = buf.find(boundary_bytes)
                    if end_pos != -1:
                        data = buf[:end_pos]
                        if data.endswith(b"\r\n"):
                            data = data[:-2]
                        tf.write(data)
                        file_size += len(data)
                        break
                    else:
                        safe_len = len(buf) - len(boundary_bytes) - 4
                        if safe_len > 0:
                            tf.write(buf[:safe_len])
                            file_size += safe_len
                            buf = buf[safe_len:]
            tf.close()
            return target_path, file_size
        except Exception:
            tf.close()
            try:
                os.unlink(target_path)
            except Exception:
                pass
            raise

    def handle_upgrade_url(self):
        cfg = self.functions.get('upgrade_url', {})
        if not cfg:
            self.send_error(404, "URL upgrade not configured")
            return

        shell_template = cfg.get('shell')
        stop_token = cfg.get('stop_token')

        stream_started = False
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                self.send_error(400, "Empty request body")
                return

            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                url = data.get('url', '').strip()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
                return

            if not url:
                self.send_error(400, "Missing 'url' parameter")
                return

            log(f"Upgrade from URL: {url}")
            self._start_text_stream()
            stream_started = True
            self._write_stream_chunk(f"=== Upgrade Started ===\n")
            self._write_stream_chunk(f"URL: {url}\n")
            self._write_stream_chunk(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._write_stream_chunk(f"{'=' * 40}\n\n")

            rc, stopped = self._stream_command(shell_to_cmd(shell_template, url), stop_token=stop_token)
            self._write_stream_chunk(f"\n{'=' * 40}\n")
            if rc == 0 or stopped:
                self._write_stream_chunk("SUCCESS: Completed successfully.\n")
            else:
                self._write_stream_chunk(f"ERROR: Failed with exit code {rc}\n")
            self._finish_text_stream()
        except Exception as e:
            log(f"Upgrade URL error: {e}")
            try:
                if stream_started:
                    self._write_stream_chunk(f"\nError: {e}\n")
                    self._finish_text_stream()
                else:
                    self.send_error(500, str(e))
            except Exception:
                pass

    def handle_upgrade_upload(self):
        cfg = self.functions.get('upgrade_upload', {})
        if not cfg:
            self.send_error(404, "Upload upgrade not configured")
            return

        upload_path = cfg.get('upload_path', '/tmp/upload_file')
        shell_template = cfg.get('shell')
        stop_token = cfg.get('stop_token')

        stream_started = False
        file_path = None
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_error(400, "Expected multipart/form-data")
                return

            file_path, file_size = self.stream_multipart_to_file("file", upload_path)
            if not file_path:
                self.send_error(400, "No file in request")
                return

            log(f"Uploaded: {file_path} ({file_size} bytes)")
            self._start_text_stream()
            stream_started = True
            self._write_stream_chunk(f"=== Upgrade Started ===\n")
            self._write_stream_chunk(f"File: {file_path}\n")
            self._write_stream_chunk(f"Size: {file_size} bytes\n")
            self._write_stream_chunk(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._write_stream_chunk(f"{'=' * 40}\n\n")

            try:
                rc, stopped = self._stream_command(shell_to_cmd(shell_template, file_path), stop_token=stop_token)
                self._write_stream_chunk(f"\n{'=' * 40}\n")
                if rc == 0 or stopped:
                    self._write_stream_chunk("SUCCESS: Completed successfully.\n")
                else:
                    self._write_stream_chunk(f"ERROR: Failed with exit code {rc}\n")
            except Exception as e:
                self._write_stream_chunk(f"\nError: {e}\n")
            finally:
                try:
                    os.unlink(file_path)
                except Exception:
                    pass
            self._finish_text_stream()
        except Exception as e:
            log(f"Upgrade upload error: {e}")
            try:
                if stream_started:
                    self._write_stream_chunk(f"\nError: {e}\n")
                    self._finish_text_stream()
                else:
                    self.send_error(500, str(e))
            except Exception:
                pass

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_html_dir = os.path.join(script_dir, "html")
    installed_html_dir = "/usr/local/share/firmware-config/html"

    if os.path.isdir(local_html_dir):
        default_html_dir = local_html_dir
    else:
        default_html_dir = installed_html_dir

    # Determine default functions directory
    local_functions_dir = os.path.join(script_dir, "functions")
    installed_functions_dir = "/usr/local/share/firmware-config/functions"

    if os.path.isdir(local_functions_dir):
        default_functions_dir = local_functions_dir
    else:
        default_functions_dir = installed_functions_dir

    parser = argparse.ArgumentParser(description="Firmware Config")
    parser.add_argument("-p", "--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address")
    parser.add_argument("--html-dir", default=default_html_dir, help="Path to HTML directory")
    parser.add_argument("--functions-dir", default=default_functions_dir, help="Path to directory containing YAML function files (loaded in sorted order)")
    args = parser.parse_args()

    if not os.path.isdir(args.functions_dir):
        log(f"ERROR: Functions directory not found: {args.functions_dir}")
        return 1

    functions = load_functions_from_dir(args.functions_dir)

    FirmwareConfigHandler.html_dir = os.fspath(args.html_dir)
    FirmwareConfigHandler.functions = functions

    server = ThreadingHTTPServer((args.bind, args.port), FirmwareConfigHandler)
    log(f"Firmware Tool Control Server running on http://{args.bind}:{args.port}")
    log(f"  HTML directory: {args.html_dir}")
    log(f"  Functions dir: {args.functions_dir}")
    log(f"")
    log(f"Available endpoints:")
    log(f"  GET  /                         - Web interface")
    log(f"  GET  /api/status               - System and network information")
    log(f"  GET  /api/settings             - Get current settings")
    log(f"  GET  /api/links                - Get available quick links")
    log(f"  GET  /api/actions              - Get available actions")
    log(f"  POST /api/upgrade                   - Upload file or download from URL and install firmware")
    log(f"  POST /api/settings/<option>/<value> - Update a setting")
    log(f"  POST /api/action/<action>           - Execute action")
    log(f"  POST /api/action/<action>/download  - Download action result file")
    log(f"")
    log(f"Available actions: {', '.join(functions.get('actions', {}).keys())}")
    log(f"Available settings: {', '.join(functions.get('settings', {}).keys())}")
    log(f"Available links: {', '.join(functions.get('links', {}).keys())}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")

    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
