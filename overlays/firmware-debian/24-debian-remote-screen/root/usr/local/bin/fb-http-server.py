#!/usr/bin/env python3

import argparse
import ctypes
import fcntl
import hashlib
import mmap
import os
import struct
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from urllib.parse import urlparse, parse_qs

try:
    from PIL import Image
except ImportError:
    Image = None

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

class FbVarScreeninfo(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_uint32),
        ("yres", ctypes.c_uint32),
        ("xres_virtual", ctypes.c_uint32),
        ("yres_virtual", ctypes.c_uint32),
        ("xoffset", ctypes.c_uint32),
        ("yoffset", ctypes.c_uint32),
        ("bits_per_pixel", ctypes.c_uint32),
        ("grayscale", ctypes.c_uint32),
        ("red", ctypes.c_uint32 * 3),
        ("green", ctypes.c_uint32 * 3),
        ("blue", ctypes.c_uint32 * 3),
        ("transp", ctypes.c_uint32 * 3),
        ("nonstd", ctypes.c_uint32),
        ("activate", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("accel_flags", ctypes.c_uint32),
        ("pixclock", ctypes.c_uint32),
        ("left_margin", ctypes.c_uint32),
        ("right_margin", ctypes.c_uint32),
        ("upper_margin", ctypes.c_uint32),
        ("lower_margin", ctypes.c_uint32),
        ("hsync_len", ctypes.c_uint32),
        ("vsync_len", ctypes.c_uint32),
        ("sync", ctypes.c_uint32),
        ("vmode", ctypes.c_uint32),
        ("rotate", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 4),
    ]

class FbFixScreeninfo(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_char * 16),
        ("smem_start", ctypes.c_ulong),
        ("smem_len", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("type_aux", ctypes.c_uint32),
        ("visual", ctypes.c_uint32),
        ("xpanstep", ctypes.c_uint16),
        ("ypanstep", ctypes.c_uint16),
        ("ywrapstep", ctypes.c_uint16),
        ("line_length", ctypes.c_uint32),
        ("mmio_start", ctypes.c_ulong),
        ("mmio_len", ctypes.c_uint32),
        ("accel", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 2),
    ]

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00
BTN_TOUCH = 0x14a
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2f
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36

def log(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)

class Framebuffer:
    def __init__(self, device='/dev/fb0'):
        self.device = device
        self.fd = None
        self.mm = None
        self.width = 0
        self.height = 0
        self.bpp = 0
        self.line_length = 0
        self._cache_hash = None
        self._cache_raw = None
        self._cache_png = None
        self._cache_time = 0
        self._open()

    def _open(self):
        self.fd = os.open(self.device, os.O_RDONLY)
        vinfo = FbVarScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, vinfo)
        finfo = FbFixScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, finfo)

        self.width = vinfo.xres
        self.height = vinfo.yres
        self.bpp = vinfo.bits_per_pixel
        self.line_length = finfo.line_length

        size = finfo.line_length * vinfo.yres
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        log(f"Framebuffer: {self.width}x{self.height} @ {self.bpp}bpp, line_length={self.line_length}")

    def get_snapshot(self, client_etag=None):
        self.mm.seek(0)
        raw = self.mm.read(self.line_length * self.height)
        raw_hash = hashlib.md5(raw).hexdigest()[:16]
        if client_etag and client_etag == raw_hash:
            return raw_hash, None
        if raw_hash == self._cache_hash and self._cache_png:
            return raw_hash, self._cache_png
        if Image is None:
            return raw_hash, b''
        if self.bpp == 32:
            img = Image.frombytes('RGBA', (self.width, self.height), raw, 'raw', 'BGRA', self.line_length)
            img = img.convert('RGB')
        elif self.bpp == 16:
            img = Image.frombytes('RGB', (self.width, self.height), raw, 'raw', 'BGR;16', self.line_length)
        else:
            img = Image.frombytes('RGB', (self.width, self.height), raw, 'raw', 'BGR', self.line_length)
        buf = BytesIO()
        img.save(buf, 'PNG', compress_level=6)
        png_data = buf.getvalue()
        self._cache_hash = raw_hash
        self._cache_png = png_data
        return raw_hash, png_data

    def close(self):
        if self.mm:
            self.mm.close()
        if self.fd:
            os.close(self.fd)

class TouchInput:
    def __init__(self, device='/dev/input/event0', fb_width=1024, fb_height=600):
        self.device = device
        self.fd = None
        self.fb_width = fb_width
        self.fb_height = fb_height
        self.touch_max_x = 1024
        self.touch_max_y = 600
        self._open()

    def _open(self):
        try:
            self.fd = os.open(self.device, os.O_WRONLY)
            self._get_abs_info()
            log(f"Touch device: {self.device}, range: {self.touch_max_x}x{self.touch_max_y}")
        except OSError as e:
            log(f"Failed to open touch device: {e}")
            self.fd = None

    def _get_abs_info(self):
        EVIOCGABS = lambda axis: 0x80184540 + axis
        try:
            buf = bytearray(24)
            fcntl.ioctl(self.fd, EVIOCGABS(ABS_MT_POSITION_X), buf)
            self.touch_max_x = struct.unpack('iiiii', buf[:20])[2]
            fcntl.ioctl(self.fd, EVIOCGABS(ABS_MT_POSITION_Y), buf)
            self.touch_max_y = struct.unpack('iiiii', buf[:20])[2]
        except OSError:
            try:
                fcntl.ioctl(self.fd, EVIOCGABS(ABS_X), buf)
                self.touch_max_x = struct.unpack('iiiii', buf[:20])[2]
                fcntl.ioctl(self.fd, EVIOCGABS(ABS_Y), buf)
                self.touch_max_y = struct.unpack('iiiii', buf[:20])[2]
            except OSError:
                pass

    def _write_event(self, ev_type, code, value):
        if self.fd is None:
            return
        tv_sec = int(time.time())
        tv_usec = int((time.time() % 1) * 1000000)
        event = struct.pack('llHHi', tv_sec, tv_usec, ev_type, code, value)
        os.write(self.fd, event)

    def _scale(self, x, y):
        touch_x = int(x * self.touch_max_x / self.fb_width)
        touch_y = int(y * self.touch_max_y / self.fb_height)
        return touch_x, touch_y

    def tap(self, x, y):
        if self.fd is None:
            log(f"Touch device not available, would tap at ({x}, {y})")
            return
        touch_x, touch_y = self._scale(x, y)
        log(f"Tap at ({x}, {y}) -> touch ({touch_x}, {touch_y})")
        self._write_event(EV_ABS, ABS_MT_SLOT, 0)
        self._write_event(EV_ABS, ABS_MT_TRACKING_ID, 1)
        self._write_event(EV_ABS, ABS_MT_POSITION_X, touch_x)
        self._write_event(EV_ABS, ABS_MT_POSITION_Y, touch_y)
        self._write_event(EV_KEY, BTN_TOUCH, 1)
        self._write_event(EV_SYN, SYN_REPORT, 0)
        time.sleep(0.05)
        self._write_event(EV_ABS, ABS_MT_TRACKING_ID, -1)
        self._write_event(EV_KEY, BTN_TOUCH, 0)
        self._write_event(EV_SYN, SYN_REPORT, 0)

    def touch_down(self, x, y):
        if self.fd is None:
            return
        touch_x, touch_y = self._scale(x, y)
        log(f"Touch down at ({x}, {y})")
        self._write_event(EV_ABS, ABS_MT_SLOT, 0)
        self._write_event(EV_ABS, ABS_MT_TRACKING_ID, 1)
        self._write_event(EV_ABS, ABS_MT_POSITION_X, touch_x)
        self._write_event(EV_ABS, ABS_MT_POSITION_Y, touch_y)
        self._write_event(EV_KEY, BTN_TOUCH, 1)
        self._write_event(EV_SYN, SYN_REPORT, 0)

    def touch_move(self, x, y):
        if self.fd is None:
            return
        touch_x, touch_y = self._scale(x, y)
        self._write_event(EV_ABS, ABS_MT_POSITION_X, touch_x)
        self._write_event(EV_ABS, ABS_MT_POSITION_Y, touch_y)
        self._write_event(EV_SYN, SYN_REPORT, 0)

    def touch_up(self):
        if self.fd is None:
            return
        log(f"Touch up")
        self._write_event(EV_ABS, ABS_MT_TRACKING_ID, -1)
        self._write_event(EV_KEY, BTN_TOUCH, 0)
        self._write_event(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self.fd:
            os.close(self.fd)

class ScreenHandler(BaseHTTPRequestHandler):
    framebuffer = None
    touch_input = None
    html_content = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/snapshot':
            self.handle_snapshot()
        elif path == '/' or path == '/index.html':
            self.handle_index()
        else:
            self.send_error(404, 'Not Found')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/touch':
            self.handle_touch(parsed.query)
        else:
            self.send_error(404, 'Not Found')

    def handle_index(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(self.html_content))
        self.end_headers()
        self.wfile.write(self.html_content)

    def handle_snapshot(self):
        try:
            client_etag = self.headers.get('If-None-Match', '').strip('"')
            etag, png_data = self.framebuffer.get_snapshot(client_etag)
            if png_data is None:
                self.send_response(304)
                self.send_header('ETag', f'"{etag}"')
                self.end_headers()
                return
            log(f"Snapshot: {len(png_data)} bytes, etag={etag}")
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', len(png_data))
            self.send_header('ETag', f'"{etag}"')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(png_data)
        except Exception as e:
            log(f"Snapshot error: {e}")
            self.send_error(500, str(e))

    def handle_touch(self, query):
        try:
            params = parse_qs(query)
            action = params.get('a', ['tap'])[0]
            x = int(params.get('x', [0])[0])
            y = int(params.get('y', [0])[0])
            if action == 'down':
                self.touch_input.touch_down(x, y)
            elif action == 'move':
                self.touch_input.touch_move(x, y)
            elif action == 'up':
                self.touch_input.touch_up()
            else:
                self.touch_input.tap(x, y)
            response = f'{{"status":"ok","a":"{action}","x":{x},"y":{y}}}'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            log(f"Touch error: {e}")
            response = f'{{"status":"error","message":"{e}"}}'.encode()
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(response))
            self.end_headers()
            self.wfile.write(response)

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_html = os.path.join(script_dir, 'index.html')
    parser = argparse.ArgumentParser(description='Framebuffer HTTP Server')
    parser.add_argument('-p', '--port', type=int, default=8092, help='HTTP port')
    parser.add_argument('--bind', default='0.0.0.0', help='Bind address')
    parser.add_argument('--fb', default='/dev/fb0', help='Framebuffer device')
    parser.add_argument('--touch', default='/dev/input/event0', help='Touch input device')
    parser.add_argument('--html', default=default_html, help='Path to index.html')
    args = parser.parse_args()
    
    if not os.path.isfile(args.html):
        log(f"ERROR: HTML file not found: {args.html}")
        return 1

    if Image is None:
        log("WARNING: PIL/Pillow not found, PNG capture will not work")
        log("Install with: pip install Pillow")
    
    with open(args.html, 'rb') as f:
        html_content = f.read()

    fb = Framebuffer(args.fb)
    touch = TouchInput(args.touch, fb.width, fb.height)

    ScreenHandler.framebuffer = fb
    ScreenHandler.touch_input = touch
    ScreenHandler.html_content = html_content

    server = ThreadingHTTPServer((args.bind, args.port), ScreenHandler)
    log(f"Server running on http://{args.bind}:{args.port}")
    log(f"  GET  /           - HTML viewer")
    log(f"  GET  /snapshot   - PNG snapshot")
    log(f"  POST /touch?x=&y= - Send touch event")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        fb.close()
        touch.close()

if __name__ == '__main__':
    main()
