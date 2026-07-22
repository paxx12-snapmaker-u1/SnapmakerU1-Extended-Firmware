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
from time import monotonic, sleep

host = "PandaBreath.local"
port = 80
debug = False


def _debug_print(prefix, text):
    if debug:
        print(f"{prefix} {text}", file=sys.stderr)


def ws_open(path="/ws", timeout=10.):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((
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
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("WS handshake: connection closed")
        buf += chunk
    status_line = buf.split(b"\r\n")[0]
    if b"101" not in status_line:
        raise ConnectionError("WS handshake failed: %s" % status_line.decode(errors="replace"))
    settings = ws_recv_json(sock, match=lambda r: "settings" in r)
    return sock, settings


def ws_send(sock, text):
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


def ws_recv(sock, match=None, sock_timeout=5, timeout=60):
    deadline = monotonic() + timeout

    def recv_exact(n):
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("WS: no response received")
            sock.settimeout(min(sock_timeout, remaining))
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WS: connection closed mid-frame")
            buf.extend(chunk)
        return bytes(buf)

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


def ws_send_json(sock, obj):
    ws_send(sock, json.dumps(obj))


def ws_recv_json(sock, match=None, sock_timeout=5, timeout=60):
    return json.loads(ws_recv(
        sock,
        match=None if match is None else lambda text: match(json.loads(text)),
        sock_timeout=sock_timeout,
        timeout=timeout,
    ))


def ws_close(sock):
    try:
        sock.close()
    except Exception:
        pass


def ws_open_retry(n=1, delay=1):
    for attempt in range(n):
        try:
            return ws_open()
        except Exception as e:
            _debug_print("ws_open_retry", f"attempt {attempt + 1} failed: {e}, retrying in {delay}s")
            sleep(delay)
    return ws_open()


def ws_conn(fn, *args):
    sock, settings = ws_open_retry()
    try:
        return fn(sock, settings, *args)
    finally:
        ws_close(sock)


# Stock OEM firmware versions this integration is known to work with over the
# WebSocket transport. 1.0.4 only adds a Home Assistant MQTT interface; the
# WebSocket protocol used here is unchanged from 1.0.3, so both are accepted.
SUPPORTED_FW_VERSIONS = ("V1.0.3", "V1.0.4")


def check_fw_version(sock, settings, allowed):
    firmware = settings.get("settings", {}).get("fw_version", "")
    if firmware not in allowed:
        print(
            f"Error: unsupported firmware '{firmware}' "
            f"(supported: {', '.join(allowed)})",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Firmware OK: {firmware}")
    print()


PRINTER_STATE_DISCONNECTED = 0
PRINTER_STATE_INVALID_INFO = 1
PRINTER_STATE_CONNECTING = 2
PRINTER_STATE_CONNECTED = 3
PRINTER_STATE_IP_ERROR = 4
PRINTER_STATE_SN_ERROR = 5
PRINTER_STATE_ACCESS_CODE_ERROR = 6
PRINTER_STATE_UNKNOWN_ERROR = 7

PRINTER_TYPE_BAMBU = 1
PRINTER_TYPE_KLIPPER = 2

PRINTER_STATES_ACTIVE = {
    PRINTER_STATE_CONNECTING,
    PRINTER_STATE_CONNECTED,
    PRINTER_STATE_IP_ERROR,
    PRINTER_STATE_SN_ERROR,
    PRINTER_STATE_ACCESS_CODE_ERROR,
}


def set_printer_type(sock, settings, printer_type):
    if settings.get("settings", {}).get("printer_type") == printer_type:
        return
    print(f"Setting printer type to {printer_type}...")
    ws_send_json(sock, {"settings": {"printer_type": printer_type}})
    resp = ws_recv_json(sock, match=lambda r: r.get("response", {}).get("type") == "printer_type")
    if resp.get("response", {}).get("ok") != 1:
        print("Error: printer_type change was not acknowledged", file=sys.stderr)
        sys.exit(1)
    print(f"Printer type set to {printer_type}.")
    print()


def bind_printer(sock, settings, printer_ip, printer_port):
    print(f"Binding to {printer_ip}:{printer_port} ...")
    ws_send_json(sock, {"printer": {"name": "Klipper", "ip": printer_ip, "port": printer_port}})
    resp = ws_recv_json(sock, match=lambda r: "state" in r.get("printer", {}) and r.get("printer", {}).get("state") != PRINTER_STATE_CONNECTING)
    state = resp.get("printer", {}).get("state")
    if state == PRINTER_STATE_CONNECTED:
        print("Bind successful.")
        return
    if state == PRINTER_STATE_IP_ERROR:
        print("Error: printer IP address error", file=sys.stderr)
        sys.exit(1)
    if state == PRINTER_STATE_INVALID_INFO:
        print("Error: invalid printer info", file=sys.stderr)
        sys.exit(1)
    print(f"Device reported state {state}: unknown error", file=sys.stderr)
    sys.exit(1)


def print_fw_version(sock, settings):
    print(settings.get("settings", {}).get("fw_version", "unknown"))


def unbind_printer(sock, settings):
    state = settings.get("printer", {}).get("state", 0)
    if state in (PRINTER_STATE_DISCONNECTED, PRINTER_STATE_INVALID_INFO):
        return
    print(f"Disconnecting printer (state={state})...")
    ws_send_json(sock, {"printer": {"disconnect": 1}})
    ws_recv_json(sock, match=lambda r: r.get("printer", {}).get("state") == PRINTER_STATE_DISCONNECTED)
    print("Unbind successful.")
    print()


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
    p.add_argument(
        "--version", action="append", metavar="VERSION",
        help="Accepted firmware version (repeatable); default: "
             + ", ".join(SUPPORTED_FW_VERSIONS))

    args = parser.parse_args()
    host = args.host
    port = args.port
    debug = args.debug

    print(f"Connecting to ws://{host}:{port}/ws ...")
    if args.command == "version":
        ws_conn(print_fw_version)
    elif args.command == "unbind":
        ws_conn(unbind_printer)
    elif args.command == "bind-klipper":
        ws_conn(check_fw_version, args.version or list(SUPPORTED_FW_VERSIONS))
        ws_conn(unbind_printer)
        ws_conn(set_printer_type, PRINTER_TYPE_KLIPPER)
        # unbind again in case printer_type change caused a reconnect (previously saved Klipper)
        ws_conn(unbind_printer)
        ws_conn(bind_printer, args.printer_ip, args.printer_port)


if __name__ == "__main__":
    main()
