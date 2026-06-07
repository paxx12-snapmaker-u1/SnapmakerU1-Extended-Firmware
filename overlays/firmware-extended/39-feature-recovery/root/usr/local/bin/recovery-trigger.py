#!/usr/bin/env python3
"""Dot-tap trigger guard: require N taps before exit 0, else exit 1."""

import argparse
import ctypes
import fcntl
import mmap
import os
import select
import struct
import sys
import time

from PIL import Image, ImageDraw


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

EV_KEY = 0x01
EV_ABS = 0x03
BTN_TOUCH = 0x14A
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
EVIOCGABS_BASE = 0x80184540

EVENT_FMT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)

RENDER_INTERVAL = 0.25

BG = (20, 20, 30)
TRIGGER_DOT_R = 6
TRIGGER_DOT_GAP = 6
TRIGGER_DOT_PAD = 12
TRIGGER_DOT_EMPTY = (45, 45, 55)
TRIGGER_DOT_FILLED = (80, 120, 200)


class FbVarScreeninfo(ctypes.Structure):
    _fields_ = [
        ('xres', ctypes.c_uint32), ('yres', ctypes.c_uint32),
        ('xres_virtual', ctypes.c_uint32), ('yres_virtual', ctypes.c_uint32),
        ('xoffset', ctypes.c_uint32), ('yoffset', ctypes.c_uint32),
        ('bits_per_pixel', ctypes.c_uint32), ('grayscale', ctypes.c_uint32),
        ('red', ctypes.c_uint32 * 3), ('green', ctypes.c_uint32 * 3),
        ('blue', ctypes.c_uint32 * 3), ('transp', ctypes.c_uint32 * 3),
        ('nonstd', ctypes.c_uint32), ('activate', ctypes.c_uint32),
        ('height', ctypes.c_uint32), ('width', ctypes.c_uint32),
        ('accel_flags', ctypes.c_uint32), ('pixclock', ctypes.c_uint32),
        ('left_margin', ctypes.c_uint32), ('right_margin', ctypes.c_uint32),
        ('upper_margin', ctypes.c_uint32), ('lower_margin', ctypes.c_uint32),
        ('hsync_len', ctypes.c_uint32), ('vsync_len', ctypes.c_uint32),
        ('sync', ctypes.c_uint32), ('vmode', ctypes.c_uint32),
        ('rotate', ctypes.c_uint32), ('colorspace', ctypes.c_uint32),
        ('reserved', ctypes.c_uint32 * 4),
    ]


class FbFixScreeninfo(ctypes.Structure):
    _fields_ = [
        ('id', ctypes.c_char * 16), ('smem_start', ctypes.c_ulong),
        ('smem_len', ctypes.c_uint32), ('type', ctypes.c_uint32),
        ('type_aux', ctypes.c_uint32), ('visual', ctypes.c_uint32),
        ('xpanstep', ctypes.c_uint16), ('ypanstep', ctypes.c_uint16),
        ('ywrapstep', ctypes.c_uint16), ('line_length', ctypes.c_uint32),
        ('mmio_start', ctypes.c_ulong), ('mmio_len', ctypes.c_uint32),
        ('accel', ctypes.c_uint32), ('capabilities', ctypes.c_uint16),
        ('reserved', ctypes.c_uint16 * 2),
    ]


class Framebuffer:
    def __init__(self, device):
        self.fd = os.open(device, os.O_RDWR)
        vinfo = FbVarScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, vinfo)
        finfo = FbFixScreeninfo()
        fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, finfo)
        self.width = vinfo.xres
        self.height = vinfo.yres
        self.yoffset = vinfo.yoffset
        self.bpp = vinfo.bits_per_pixel
        self.line_length = finfo.line_length
        size = finfo.line_length * vinfo.yres_virtual
        self.mm = mmap.mmap(self.fd, size, mmap.MAP_SHARED,
                            mmap.PROT_READ | mmap.PROT_WRITE)
        log(f'fb: {self.width}x{self.height} {self.bpp}bpp line={self.line_length}')

    def _img_to_raw(self, img):
        if self.bpp == 32:
            img_rgba = img.convert('RGBA')
            r, g, b, a = img_rgba.split()
            return Image.merge('RGBA', (b, g, r, a)).tobytes()
        if self.bpp == 16:
            rgb = img.convert('RGB').tobytes()
            n = img.width * img.height
            buf = bytearray(n * 2)
            for i in range(n):
                rv, gv, bv = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
                struct.pack_into('<H', buf, i * 2,
                                 ((rv & 0xF8) << 8) | ((gv & 0xFC) << 3) | (bv >> 3))
            return bytes(buf)
        return None

    def _write_raw(self, raw, x, y, w, h):
        bpp_bytes = self.bpp // 8
        row_bytes = w * bpp_bytes
        for row in range(h):
            pos = (self.yoffset + y + row) * self.line_length + x * bpp_bytes
            self.mm.seek(pos)
            self.mm.write(raw[row * row_bytes:(row + 1) * row_bytes])

    def clear(self):
        img = Image.new('RGB', (self.width, self.height), (0, 0, 0))
        raw = self._img_to_raw(img)
        if raw is not None:
            self._write_raw(raw, 0, 0, self.width, self.height)

    def close(self):
        if self.mm:
            self.mm.close()
        if self.fd is not None:
            os.close(self.fd)


class TouchReader:
    def __init__(self, device, fb_width, fb_height):
        self.fb_width = fb_width
        self.fb_height = fb_height
        self.max_x = fb_width
        self.max_y = fb_height
        self.fd = None
        if not device or device.lower() == 'none':
            return
        try:
            self.fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
            self._read_abs_range()
            log(f'touch: {device} range={self.max_x}x{self.max_y}')
        except OSError as e:
            log(f'touch: cannot open {device}: {e}')
            self.fd = None
        self.cur_x, self.cur_y = None, None
        self._touching = False

    def _read_abs_range(self):
        buf = bytearray(24)
        try:
            fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_MT_POSITION_X, buf)
            self.max_x = struct.unpack('iiiii', buf[:20])[2] or self.max_x
            fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_MT_POSITION_Y, buf)
            self.max_y = struct.unpack('iiiii', buf[:20])[2] or self.max_y
        except OSError:
            try:
                fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_X, buf)
                self.max_x = struct.unpack('iiiii', buf[:20])[2] or self.max_x
                fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_Y, buf)
                self.max_y = struct.unpack('iiiii', buf[:20])[2] or self.max_y
            except OSError:
                pass

    def read_event(self, timeout):
        """Block up to `timeout` seconds; return 'down', 'up', or None."""
        if self.fd is None:
            time.sleep(min(timeout, RENDER_INTERVAL))
            return None
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], min(remaining, RENDER_INTERVAL))
            if not r:
                return None
            try:
                data = os.read(self.fd, EVENT_SIZE)
            except BlockingIOError:
                continue
            if len(data) < EVENT_SIZE:
                continue
            _, _, ev_type, code, value = struct.unpack(EVENT_FMT, data)
            log(f'event: type={ev_type:#x} code={code:#x} value={value}')
            if ev_type == EV_ABS:
                if code == ABS_MT_TRACKING_ID:
                    if value >= 0 and not self._touching:
                        self._touching = True
                        return 'down'
                    elif value < 0 and self._touching:
                        self._touching = False
                        return 'up'
            elif ev_type == EV_KEY and code == BTN_TOUCH:
                if value == 1 and not self._touching:
                    self._touching = True
                    return 'down'
                elif value == 0 and self._touching:
                    self._touching = False
                    return 'up'

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


def render_dots(fb, n, filled):
    d = TRIGGER_DOT_R * 2
    w = n * d + (n - 1) * TRIGGER_DOT_GAP
    h = d
    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)
    for i in range(n):
        x0 = i * (d + TRIGGER_DOT_GAP)
        color = TRIGGER_DOT_FILLED if i < filled else TRIGGER_DOT_EMPTY
        draw.ellipse([x0, 0, x0 + d - 1, d - 1], fill=color)
    x = fb.width - w - TRIGGER_DOT_PAD
    y = fb.height - h - TRIGGER_DOT_PAD
    raw = fb._img_to_raw(img)
    if raw is not None:
        fb._write_raw(raw, x, y, w, h)


def run(fb, touch, tap_count, tap_timeout):
    log(f'trigger: waiting for {tap_count} taps, {tap_timeout}s between taps')
    taps = 0
    deadline = time.monotonic() + tap_timeout
    render_dots(fb, tap_count, 0)
    while True:
        now = time.monotonic()
        if now >= deadline:
            log('trigger: tap timeout expired')
            return False
        event = touch.read_event(min(deadline - now, RENDER_INTERVAL))
        if event == 'down':
            taps += 1
            render_dots(fb, tap_count, taps)
            log(f'trigger: tap {taps}/{tap_count}')
            if taps >= tap_count:
                log('trigger: activated')
                return True
            deadline = time.monotonic() + tap_timeout


def main():
    parser = argparse.ArgumentParser(description='Dot-tap trigger guard for recovery screen')
    parser.add_argument('--fb', default='/dev/fb0', help='Framebuffer device')
    parser.add_argument('--touch', default='/dev/input/event0',
                        help='Touch input device (or "none" to disable)')
    parser.add_argument('--tap-count', type=int, default=3,
                        help='Number of taps required to activate (0 = pass immediately)')
    parser.add_argument('--tap-timeout', type=float, default=1,
                        help='Seconds to wait for the next tap before giving up')
    args = parser.parse_args()

    if args.tap_count <= 0:
        sys.exit(0)

    if not os.path.exists(args.fb):
        log(f'trigger: {args.fb} not available, passing')
        sys.exit(0)

    fb = Framebuffer(args.fb)
    touch = TouchReader(args.touch, fb.width, fb.height)
    try:
        activated = run(fb, touch, args.tap_count, args.tap_timeout)
    finally:
        fb.clear()
        touch.close()
        fb.close()

    sys.exit(0 if activated else 1)


if __name__ == '__main__':
    main()
