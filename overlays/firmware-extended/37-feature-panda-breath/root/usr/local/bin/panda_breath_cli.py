#!/usr/bin/env python3
# WebSocket is implemented manually using stdlib `socket` and `struct` to avoid
# any dependency on external packages (e.g. `websockets`, `aiohttp`).

import argparse
import base64
import json
import os
import socket
import struct
import sys
from time import sleep

host = "PandaBreath.local"
port = 80
sock = None
settings = {}
debug = False


def _debug_print(prefix, text):
    if debug:
        print(f"{prefix} {text}", file=sys.stderr)


def ws_open(path="/ws", timeout=10.):
    global sock, settings
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((
        "GET {path} HTTP/1.1\r\n"
        "Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).format(path=path, host=host, port=port, key=key).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(1024)
        if not chunk:
            raise ConnectionError("WS handshake: connection closed")
        buf += chunk
    status_line = buf.split(b"\r\n")[0]
    if b"101" not in status_line:
        raise ConnectionError("WS handshake failed: %s" % status_line.decode(errors="replace"))
    sock = s
    settings = ws_recv_json()


def ws_send(text):
    _debug_print(">>", text)
    payload = text.encode("utf-8")
    length = len(payload)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    if length < 126:
        header = struct.pack("!BB", 0x81, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 0xFE, length)
    else:
        header = struct.pack("!BBQ", 0x81, 0xFF, length)
    sock.sendall(header + mask + masked)


def ws_recv(match=None, timeout=30):
    def recv_exact(n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WS: connection closed mid-frame")
            buf.extend(chunk)
        return bytes(buf)

    sock.settimeout(timeout)
    while True:
        header = recv_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", recv_exact(8))[0]
        mask_key = recv_exact(4) if masked else None
        payload = recv_exact(length)
        if masked:
            payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise ConnectionError("WS: server closed connection")
        if opcode == 0x1:
            text = payload.decode("utf-8")
            _debug_print("<<", text)
            if match is None or match(text):
                return text


def ws_send_json(obj):
    ws_send(json.dumps(obj))


def ws_recv_json(match=None, timeout=30):
    return json.loads(ws_recv(
        match=None if match is None else lambda text: match(json.loads(text)),
        timeout=timeout,
    ))


def ws_close():
    global sock
    try:
        sock.close()
    except Exception:
        pass
    sock = None


def unbind(device_settings):
    state = device_settings.get("printer", {}).get("state", 0)
    if state == 0:
        print("Device is already disconnected.")
        return
    print(f"Disconnecting printer (state={state})...")
    ws_send('{"printer":{"disconnect":1}}')
    ws_recv_json(match=lambda r: r.get("printer", {}).get("state") == 0)
    print()
    print("Unbind successful.")


def bind_klipper(printer_ip, printer_port, firmware_version, device_settings):
    firmware = device_settings.get("settings", {}).get("fw_version", "")
    if firmware != firmware_version:
        print(f"Error: expected firmware {firmware_version}, got '{firmware}'", file=sys.stderr)
        sys.exit(1)
    print(f"Firmware OK: {firmware}")
    print()

    # state 1: invalid info, 2: connecting, 3: connected, 4: ip err, 5: sn err, 6: access code, 7: unknown err

    if device_settings.get("printer", {}).get("state", 0) in [1, 2, 3, 4, 5, 6]:
        print("Disconnecting any existing printer ...")
        ws_send('{"printer":{"disconnect":1}}')
        ws_recv_json(match=lambda r: r.get("printer", {}).get("state") == 0)
        sleep(1)

    if device_settings.get("settings", {}).get("printer_type") != 2:
        print("Setting printer type to Klipper...")
        ws_send_json({"settings": {"printer_type": 2}})
        resp = ws_recv_json(match=lambda r: r.get("response", {}).get("type") == "printer_type")
        if resp.get("response", {}).get("ok") != 1:
            print("Error: printer_type change was not acknowledged", file=sys.stderr)
            sys.exit(1)
        print("Printer type set to Klipper.")
        print()
        sleep(1)

    print(f"Binding to {printer_ip}:{printer_port} ...")
    ws_send_json({"printer": {"name": "Klipper", "ip": printer_ip, "port": printer_port}})
    resp = ws_recv_json(match=lambda r: "state" in r.get("printer", {}) and r.get("printer", {}).get("state") != 2)
    state = resp.get("printer", {}).get("state")
    if state == 3:
        print("Device reported successful connection.")
        print("Bind successful.")
        return
    if state == 4:
        print("Error: printer IP address error", file=sys.stderr)
        sys.exit(1)
    if state == 1:
        print("Error: invalid printer info", file=sys.stderr)
        sys.exit(1)
    print(f"Device reported state {state}: unknown error", file=sys.stderr)
    sys.exit(1)


def main():
    global host, port, debug

    parser = argparse.ArgumentParser(description="Panda Breath CLI")
    parser.add_argument("--host", default=host, help="Panda Breath host")
    parser.add_argument("--port", type=int, default=port, help="Panda Breath port")
    parser.add_argument("--debug", action="store_true", help="Log sent/received WS frames to stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print device firmware version")

    sub.add_parser("unbind", help="Disconnect the bound printer from Panda Breath")

    p = sub.add_parser("bind-klipper", help="Bind Panda Breath to a Klipper instance")
    p.add_argument("--printer-ip", required=True, help="Klipper printer IP")
    p.add_argument("--printer-port", type=int, default=80, help="Klipper printer port")
    p.add_argument("--version", default="V1.0.3", help="Required firmware version")

    args = parser.parse_args()
    host = args.host
    port = args.port
    debug = args.debug

    print(f"Connecting to ws://{host}:{port}/ws ...")
    ws_open()
    try:
        if args.command == "version":
            print(settings.get("settings", {}).get("fw_version", "unknown"))
        elif args.command == "unbind":
            unbind(settings)
        elif args.command == "bind-klipper":
            bind_klipper(args.printer_ip, args.printer_port, args.version, settings)
    finally:
        ws_close()


if __name__ == "__main__":
    main()
