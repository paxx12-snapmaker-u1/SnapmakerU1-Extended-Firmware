#!/usr/bin/env python3

import os
import sys
import json
import signal
import socket
import subprocess
import threading
import argparse
import time
from datetime import datetime
from pathlib import Path

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

global_lock = threading.Lock()
snapshot_interval = -1

args = None
client = None

def log(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

def timestamp_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def date_index():
    return datetime.now().strftime("%Y%m%d%H%M%S")

def send_response(request_id, result):
    resp = {"jsonrpc": "2.0", "id": request_id, "result": result}
    client.publish(args.response_topic, json.dumps(resp))
    log(f"Response: {resp}")

def send_error(request_id, code, message):
    resp = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    client.publish(args.response_topic, json.dumps(resp))
    log(f"Error: {resp}")

def send_notification(method, params):
    notif = {"jsonrpc": "2.0", "method": method, "params": params}
    client.publish(args.notification_topic, json.dumps(notif))
    log(f"Notification: {notif}")

def read_socket(sock_path, chunk_size=65536):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    try:
        chunks = []
        while True:
            chunk = sock.recv(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        sock.close()

def send_detect_request(sock_path, image_path):
    if not sock_path:
        return {"error": "Detection socket not configured"}
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
        request = {"image": image_path}
        sock.sendall(json.dumps(request).encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        response = b''.join(chunks).decode('utf-8')
        return json.loads(response)
    except Exception as e:
        log(f"Detection request failed: {e}")
        return {"error": str(e)}

def take_snapshot(filepath, max_age=None):
    if max_age is not None and Path(filepath).exists():
        age = time.time() - Path(filepath).stat().st_mtime
        if age < max_age:
            log(f"Snapshot is fresh ({age:.2f}s old), skipping capture")
            return True
    if not args.jpeg_sock:
        log(f"No snapshot socket configured")
        return False
    try:
        data = read_socket(args.jpeg_sock)
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        tmp_path = filepath + ".tmp"
        with open(tmp_path, 'wb') as f:
            f.write(data)
        os.replace(tmp_path, filepath)
        log(f"Snapshot saved: {filepath} ({len(data)} bytes)")
        return True
    except Exception as e:
        log(f"Snapshot failed: {e}")
        return False

def handle_start_timelapse(request_id, params):
    with global_lock:
        current_link = Path(args.timelapse_dir) / "current"
        if current_link.exists() or current_link.is_symlink():
            if args.auto_close_timelapses:
                log("Finishing previous timelapse before starting new one")
                finish_timelapse()
            else:
                send_error(request_id, -1, "Timelapse already active")
                return

        idx = date_index()
        tl_dir = Path(args.timelapse_dir) / idx
        tl_dir.mkdir(parents=True, exist_ok=True)

        config = {
            "frame_rate": params.get("frame_rate", 24),
            "gcode_name": params.get("gcode_name", "unknown"),
            "gcode_path": params.get("gcode_path", ""),
            "mode": params.get("mode", "classic"),
        }
        with open(tl_dir / "config.json", "w") as f:
            json.dump(config, f, indent=4)

        with open(tl_dir / "state", "w") as f:
            f.write("READY")

        current_link.symlink_to(idx)

    send_response(request_id, {"state": "success"})
    send_notification("notify_camera_status_change", [{"timelapse": True, "timestamp": timestamp_str()}])

def publish_timelapse(gcode_name, date_index, video_path, thumb_path):
    if not args.publish_dir:
        return
    publish_dir = Path(args.publish_dir)
    publish_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{gcode_name}_{date_index}"
    video_link = publish_dir / f"{base_name}.mp4"
    thumb_link = publish_dir / f"{base_name}.jpg"

    for link in [video_link, thumb_link]:
        if link.exists() or link.is_symlink():
            link.unlink()

    if video_path and Path(video_path).exists():
        video_link.symlink_to(video_path)
        log(f"Published: {video_link}")

    if thumb_path and Path(thumb_path).exists():
        thumb_link.symlink_to(thumb_path)
        log(f"Published: {thumb_link}")

def read_timelapse_data():
    timelapse_json_path = Path(args.timelapse_dir) / "timelapse.json"
    timelapse_data = {"count": 0, "instances": []}
    if timelapse_json_path.exists():
        try:
            with open(timelapse_json_path, "r") as f:
                timelapse_data = json.load(f)
        except Exception as e:
            log(f"Failed to read timelapse.json: {e}")
    return timelapse_data

def write_timelapse_data(timelapse_data):
    timelapse_json_path = Path(args.timelapse_dir) / "timelapse.json"
    with open(timelapse_json_path, "w") as f:
        json.dump(timelapse_data, f, indent=4)

def add_instance_to_timelapse_json(instance):
    timelapse_data = read_timelapse_data()

    timelapse_data["instances"] = [i for i in timelapse_data.get("instances", []) if i.get("date_index") != instance.get("date_index")]
    timelapse_data["instances"].append(instance)
    timelapse_data["count"] = len(timelapse_data["instances"])

    write_timelapse_data(timelapse_data)

def finish_timelapse():
    current_link = Path(args.timelapse_dir) / "current"
    if not current_link.exists():
        return False
    if not current_link.is_symlink():
        return False

    read_link = current_link.resolve()
    current_link.unlink()

    threading.Thread(target=generate_video, args=(read_link,), daemon=True).start()
    return True

def handle_stop_timelapse(request_id, params):
    with global_lock:
        if finish_timelapse():
            send_response(request_id, {"state": "success"})
            send_notification("notify_camera_status_change", [{"timelapse": False, "timestamp": timestamp_str()}])
        else:
            send_error(request_id, -1, "No active timelapse")

def generate_video(tl_dir):
    frame_count = 0
    try:
        with open(tl_dir / "indexNext", "r") as f:
            frame_count = int(f.read().strip())
    except Exception as e:
        log(f"Failed to read frame count: {e}")

    if frame_count <= 0:
        log(f"Invalid frame count {frame_count}, cleaning up {tl_dir}")
        try:
            import shutil
            shutil.rmtree(tl_dir)
            log(f"Removed timelapse folder: {tl_dir}")
        except Exception as cleanup_err:
            log(f"Failed to cleanup {tl_dir}: {cleanup_err}")
        return

    config_path = tl_dir / "config.json"
    if not config_path.exists():
        log(f"No config found in {tl_dir}, skipping video generation")
        return
    with open(config_path, "r") as f:
        config = json.load(f)

    with open(tl_dir / "state", "w") as f:
        f.write("GENERATING")

    # Get video parameters
    frame_rate = config.get("frame_rate", 24)
    mode = config.get("mode", "classic")
    video_name = f"timelapse_f{frame_count}_r{frame_rate}_{mode}.mp4"
    video_path = tl_dir / video_name

    log(f"Generating video: {video_path}")

    send_notification("notify_camera_generate_video_progress", [{"percent": 0, "state": "generating"}])

    # Generate video using ffmpeg
    try:
        input_pattern = str(tl_dir / "%d.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(frame_rate),
            "-i", input_pattern,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            str(video_path)
        ]
        log(f"Running: {' '.join(cmd)}")
        with open(tl_dir / "gen_video.log", "w") as logf:
            result = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=300)

        if result.returncode != 0:
            log(f"ffmpeg failed with code {result.returncode}")
    except Exception as e:
        log(f"Video generation failed: {e}")

    send_notification("notify_camera_generate_video_progress", [{"percent": 60, "state": "generating"}])

    # Create thumbnail
    thumb_path = tl_dir / "thumbnail.jpg"
    last_frame = tl_dir / f"{frame_count - 1}.jpg"
    if last_frame.exists():
        try:
            cmd = ["ffmpeg", "-y", "-i", str(last_frame), "-vf", "scale=320:-1", str(thumb_path)]
            with open(tl_dir / "gen_thumbnail.log", "w") as logf:
                subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=30)
        except Exception as e:
            log(f"Thumbnail generation failed: {e}")

    send_notification("notify_camera_generate_video_progress", [{"percent": 80, "state": "generating"}])

    # Add instance to timelapse.json
    duration_secs = frame_count / max(frame_rate, 1)
    duration_str = f"{int(duration_secs // 60):02d}:{int(duration_secs % 60):02d}"
    add_instance_to_timelapse_json({
        "date_index": tl_dir.name,
        "gcode_name": config.get("gcode_name", ""),
        "gcode_path": config.get("gcode_path", ""),
        "generate_date": datetime.now().strftime("%Y-%m-%d"),
        "thumbnail_path": str(thumb_path) if thumb_path.exists() else "",
        "timelapse_dir": str(tl_dir),
        "video_duration": duration_str,
        "video_path": str(video_path) if video_path.exists() else "",
    })

    publish_timelapse(config.get("gcode_name", ""), tl_dir.name, str(video_path), str(thumb_path))

    # Mark as finished
    with open(tl_dir / "state", "w") as f:
        f.write("FINISHED")

    send_notification("notify_camera_generate_video_progress", [{"percent": 80, "state": "generating"}])

    # Remove jpg (only keep video and thumbnail)
    for frame_idx in range(1, frame_count):
        jpg_file = tl_dir / f"{frame_idx}.jpg"
        try:
            jpg_file.unlink()
        except Exception as e:
            log(f"Failed to remove {jpg_file}: {e}")

    # Send completion notification
    send_notification("notify_camera_generate_video_completed", [])
    log(f"Video generation completed: {video_path}")

def finish_all_timelapses():
    timelapse_data = read_timelapse_data()
    valid_instances = {inst.get("date_index") for inst in timelapse_data.get("instances", [])}

    current_link = Path(args.timelapse_dir) / "current"
    if current_link.exists():
        current_link.unlink()
        log(f"Removed current symlink")

    for tl_dir in Path(args.timelapse_dir).iterdir():
        if not tl_dir.is_dir():
            continue

        state_file = tl_dir / "state"
        if not state_file.exists():
            log(f"Deleting timelapse without state file: {tl_dir}")
            errors = delete_timelapse_instance(tl_dir.name)
            if errors:
                for error in errors:
                    log(error)
            continue
        with open(state_file, "r") as f:
            state = f.read().strip()

        if state in ("READY", "ACTIVATED", "GENERATING"):
            log(f"Finishing timelapse in {tl_dir}")
            generate_video(tl_dir)
        elif state == "FINISHED" and tl_dir.name not in valid_instances:
            log(f"Deleting orphaned finished timelapse: {tl_dir}")
            errors = delete_timelapse_instance(tl_dir.name)
            if errors:
                for error in errors:
                    log(error)

def handle_get_timelapse_instance(request_id, params):
    page_index = params.get("page_index", 1)
    page_rows = params.get("page_rows", 10)

    timelapse_data = read_timelapse_data()
    instances = timelapse_data.get("instances", [])
    total_count = len(instances)

    start_idx = (page_index - 1) * page_rows
    end_idx = start_idx + page_rows
    page_instances = instances[start_idx:end_idx]

    result = {
        "count": len(page_instances),
        "instances": page_instances,
        "page_index": page_index,
        "page_rows": page_rows,
        "state": "success",
        "total_count": total_count
    }

    send_response(request_id, result)

def handle_get_status(request_id, params):
    monitoring = snapshot_interval >= 0

    current_link = Path(args.timelapse_dir) / "current"
    timelapse = current_link.exists() and current_link.is_symlink()

    interface_type = "unknown"
    if args.jpeg_sock:
        sock_lower = args.jpeg_sock.lower()
        if "mipi" in sock_lower:
            interface_type = "MIPI"
        elif "usb" in sock_lower:
            interface_type = "USB"

    result = {
        "interface_type": interface_type,
        "monitoring": monitoring,
        "state": "success",
        "timelapse": timelapse
    }

    send_response(request_id, result)

def delete_timelapse_instance(date_index):
    errors = []
    tl_dir = Path(args.timelapse_dir) / date_index

    current_link = Path(args.timelapse_dir) / "current"
    if current_link.exists() and current_link.is_symlink():
        if current_link.resolve() == tl_dir:
            errors.append(f"Cannot delete active timelapse: {date_index}")
            return errors

    if tl_dir.exists() and tl_dir.is_dir():
        try:
            import shutil
            shutil.rmtree(tl_dir)
            log(f"Deleted timelapse directory: {tl_dir}")
        except Exception as e:
            errors.append(f"Failed to delete directory {tl_dir}: {e}")

    try:
        timelapse_data = read_timelapse_data()
        timelapse_data["instances"] = [i for i in timelapse_data.get("instances", []) if i.get("date_index") != date_index]
        timelapse_data["count"] = len(timelapse_data["instances"])
        write_timelapse_data(timelapse_data)
        log(f"Removed instance {date_index} from timelapse.json")
    except Exception as e:
        errors.append(f"Failed to update timelapse.json: {e}")

    if args.publish_dir:
        publish_dir = Path(args.publish_dir)
        for file in publish_dir.glob(f"*_{date_index}.*"):
            try:
                file.unlink()
                log(f"Removed published file: {file}")
            except Exception as e:
                errors.append(f"Failed to remove {file}: {e}")

    return errors

def handle_delete_timelapse_instance(request_id, params):
    date_index = params.get("date_index")
    if not date_index:
        send_error(request_id, -32602, "Missing required parameter: date_index")
        return

    errors = delete_timelapse_instance(date_index)
    if errors:
        send_error(request_id, -32603, "; ".join(errors))
    else:
        send_response(request_id, {"state": "success"})

def handle_take_photo(request_id, params):
    filepath = params.get("filepath")
    index_next = None
    frame_num = None
    reason = params.get("reason", "manual")

    if reason == "printing":
        current_link = Path(args.timelapse_dir) / "current"
        if not current_link.exists() or not current_link.is_symlink():
            send_error(request_id, -1, "No active timelapse for printing snapshot")
            return

        tl_dir = current_link.resolve()
        state_file = tl_dir / "state"
        if state_file.exists():
            with open(state_file, "r") as f:
                state = f.read().strip()
            if state == "READY":
                with open(state_file, "w") as f:
                    f.write("ACTIVATED")

        try:
            index_next = str(current_link / "indexNext")
            with open(index_next, "r") as f:
                frame_num = int(f.read().strip())
        except Exception as e:
            frame_num = 1
        filepath = str(current_link / f"{frame_num}.jpg")

    if not filepath:
        filepath = "/tmp/snapshot.jpg"

    if not take_snapshot(filepath):
        send_error(request_id, -1, "Failed to take photo")
        return

    send_response(request_id, {"state": "success"})

    if frame_num is not None:
        try:
            with open(index_next, "w") as f:
                f.write(str(frame_num + 1))
        except Exception as e:
            log(f"Failed to update indexNext: {e}")

def snapshot_interval_loop(filepath):
    log(f"Snapshot interval loop started: filepath={filepath}")

    while True:
        if snapshot_interval < 0:
            time.sleep(0.5)
            continue

        interval = min(max(snapshot_interval, 0.5), 2.0)
        time.sleep(interval)

        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            take_snapshot(filepath)
        except Exception as e:
            log(f"Snapshot interval error: {e}")

def ensure_monitor_symlink():
    if not args.publish_dir:
        return

    monitor_path = Path(args.publish_dir) / "monitor.jpg"
    try:
        if monitor_path.is_symlink():
            if monitor_path.resolve() == Path(args.monitor_output):
                return
            monitor_path.unlink()
        monitor_path.symlink_to(args.monitor_output)
        log(f"Monitor symlink created: {monitor_path} -> {args.monitor_output}")
    except Exception as e:
        log(f"Failed to create monitor symlink: {e}")

def handle_start_monitor(request_id, params):
    global snapshot_interval
    domain = params.get("domain", "lan")
    snapshot_interval = params.get("interval", 1)

    ensure_monitor_symlink()

    send_response(request_id, {"state": "success", "url": "/files/camera/monitor.jpg"})
    send_notification("notify_camera_status_change", [{"monitor_domain": domain, "monitoring": True, "timestamp": timestamp_str()}])

def handle_stop_monitor(request_id, params):
    global snapshot_interval
    domain = params.get("domain", "lan")
    snapshot_interval = -1
    send_response(request_id, {"state": "success"})
    send_notification("notify_camera_status_change", [{"monitor_domain": domain, "monitoring": False, "timestamp": timestamp_str()}])

def handle_upload_timelapse_instance(request_id, params):
    instance_id = params.get("instance_id")
    if not instance_id:
        send_error(request_id, -1, "Missing instance_id parameter")
        return
    send_error(request_id, -1, "Upload not implemented")

def handle_detect_capture(request_id, params):
    image_path = args.monitor_output
    if not take_snapshot(image_path, max_age=1.0):
        send_error(request_id, -1, "Failed to take snapshot")
        return

    if not args.label_to_socket:
        send_error(request_id, -1, "No detection sockets configured")
        return

    all_detections = {}
    requested_labels = params.get("labels", [])
    if not requested_labels:
        requested_labels = list(args.label_to_socket.keys())

    socket_to_labels = {}
    for label in requested_labels:
        socket_path = args.label_to_socket.get(label)
        if not socket_path:
            log(f"No socket configured for label '{label}'")
            continue
        if socket_path not in socket_to_labels:
            socket_to_labels[socket_path] = []
        socket_to_labels[socket_path].append(label)

    for socket_path, labels in socket_to_labels.items():
        label_params = {label: params[label] for label in labels if label in params}

        detect_result = send_detect_request(socket_path, image_path)
        if "error" in detect_result:
            log(f"Detection error from {socket_path}: {detect_result['error']}")
            continue

        detections = detect_result.get("detections", {})
        for class_name, objects in detections.items():
            if class_name in labels:
                label_params = params.get(class_name, {})
                threshold = label_params.get("threshold", 0.0)

                filtered_objects = [obj for obj in objects if obj["confidence"] >= threshold]

                if filtered_objects:
                    if class_name not in all_detections:
                        all_detections[class_name] = []
                    all_detections[class_name].extend(filtered_objects)

    result = {"state": "success", "err_code": 0}
    for class_name, objects in all_detections.items():
        max_prob = max((obj["confidence"] for obj in objects), default=0.0)
        obj_areas = sum(obj["area"] for obj in objects)
        obj_cnts = len(objects)
        obj_probs = sum(obj["confidence"] for obj in objects)
        result[class_name] = {
            "max_prob": max_prob,
            "obj_areas": obj_areas,
            "obj_cnts": obj_cnts,
            "obj_probs": obj_probs
        }

    for label in requested_labels:
        if label not in result:
            result[label] = {
                "max_prob": 0.0,
                "obj_areas": 0.0,
                "obj_cnts": 0,
                "obj_probs": 0.0
            }

    send_response(request_id, result)

METHODS = {
    "camera.start_timelapse": handle_start_timelapse,
    "camera.stop_timelapse": handle_stop_timelapse,
    "camera.start_monitor": handle_start_monitor,
    "camera.stop_monitor": handle_stop_monitor,
    "camera.take_a_photo": handle_take_photo,
    "camera.get_timelapse_instance": handle_get_timelapse_instance,
    "camera.delete_timelapse_instance": handle_delete_timelapse_instance,
    "camera.upload_timelapse_instance": handle_upload_timelapse_instance,
    "camera.get_status": handle_get_status,
    "camera.detect_capture": handle_detect_capture,
}

def on_connect(c, userdata, flags, rc):
    if rc == 0:
        log(f"Connected to MQTT broker")
        c.subscribe(args.request_topic)
        log(f"Subscribed to {args.request_topic}")
    else:
        log(f"Connection failed: {rc}")

def on_message(c, userdata, msg):
    request_id = None
    try:
        payload = msg.payload.decode('utf-8')
        log(f"Request: {payload}")
        req = json.loads(payload)

        request_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method in METHODS:
            METHODS[method](request_id, params)
        else:
            send_error(request_id, -32601, f"Method not found: {method}")
    except json.JSONDecodeError as e:
        log(f"Invalid JSON: {e}")
        if request_id is not None:
            send_error(request_id, -32700, f"Parse error: {str(e)}")
    except Exception as e:
        log(f"Error handling message: {e}")
        if request_id is not None:
            send_error(request_id, -32603, f"Internal error: {str(e)}")

def main():
    global args, client

    parser = argparse.ArgumentParser(description='V4L2-MPP MQTT Camera Service')
    parser.add_argument('--mqtt-host', default='localhost', help='MQTT broker host')
    parser.add_argument('--mqtt-port', type=int, default=1883, help='MQTT broker port')
    parser.add_argument('--mqtt-user', default=None, help='MQTT username')
    parser.add_argument('--mqtt-pass', default=None, help='MQTT password')
    parser.add_argument('--jpeg-sock', default=None, help='JPEG snapshot socket')
    parser.add_argument('--detect-sock', action='append', help='Detection socket in format /path/sock:label1,label2 (can be used multiple times)')
    parser.add_argument('--request-topic', default='camera/request', help='MQTT request topic')
    parser.add_argument('--response-topic', default='camera/response', help='MQTT response topic')
    parser.add_argument('--notification-topic', default='camera/notification', help='MQTT notification topic')
    parser.add_argument('--timelapse-dir', default='/userdata/.tmp_timelapse', help='Timelapse output directory')
    parser.add_argument('--publish-dir', default=None, help='Directory to create symlinks for timelapse videos')
    parser.add_argument('--monitor-output', default='/tmp/.monitor.jpg', help='Monitor output path')
    parser.add_argument('--auto-close-timelapses', default=True, action='store_true', help='Auto-close previous timelapse when starting new one')
    args = parser.parse_args()

    args.label_to_socket = {}
    if args.detect_sock:
        for sock_spec in args.detect_sock:
            if ':' not in sock_spec:
                log(f"Error: The '{sock_spec}' does not have labels")
                sys.exit(1)
            socket_path, labels_str = sock_spec.split(':', 1)
            labels = [l.strip() for l in labels_str.split(',') if l.strip()]
            for label in labels:
                if label in args.label_to_socket:
                    log(f"Error: Duplicate label '{label}' found")
                    sys.exit(1)
                args.label_to_socket[label] = socket_path

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    if args.mqtt_user:
        client.username_pw_set(args.mqtt_user, args.mqtt_pass)

    def shutdown(signum, frame):
        log("Shutting down...")
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log(f"Connecting to {args.mqtt_host}:{args.mqtt_port}")
    client.connect(args.mqtt_host, args.mqtt_port, 60)

    log("Ensuring monitor symlink")
    ensure_monitor_symlink()

    log("Finishing any active timelapse on startup")
    threading.Thread(target=finish_all_timelapses, daemon=True).start()

    log("Start snapshot interval thread")
    threading.Thread(target=snapshot_interval_loop, args=(args.monitor_output,), daemon=True).start()

    log("Starting MQTT loop")
    client.loop_forever()

if __name__ == '__main__':
    main()
