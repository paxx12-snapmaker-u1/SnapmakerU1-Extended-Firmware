#!/usr/bin/env python3
"""Full-screen touch GUI rendered to /dev/fb0, configured via YAML."""

import argparse
import ctypes
import fcntl
import glob
import mmap
import os
import select
import struct
import subprocess
import sys
import time

import yaml
from PIL import Image, ImageDraw, ImageFont


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

EV_KEY = 0x01
EV_ABS = 0x03
BTN_TOUCH = 0x14A
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_TRACKING_ID = 0x39
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
EVIOCGABS_BASE = 0x80184540

EVENT_FMT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)

FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/TTF/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
]

MONO_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
    '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
]

PAD = 30
BUTTON_HEIGHT = 32
ITEM_SPACING = 16
TITLE_SIZE = 44
LABEL_SIZE = 30
BUTTON_SIZE = 32
MONO_SIZE = 22
TIMEOUT_BAR_H = 6
RENDER_INTERVAL = 0.25

MODAL_STDERR_LINES = 10
MODAL_STDERR_CHARS = 72

BG = (20, 20, 30)
TITLE_FG = (180, 220, 255)
LABEL_FG = (200, 200, 210)
BTN_BG = (40, 65, 110)
BTN_BORDER = (70, 115, 190)
BTN_FG = (240, 240, 255)
BTN_PRESSED_BG = (90, 140, 220)
BTN_PRESSED_BORDER = (140, 190, 255)
TBAR_BG = (35, 35, 45)
TBAR_FG = (60, 110, 200)

TEXT_BG = (0, 0, 0)
TEXT_BORDER = (60, 60, 75)
TEXT_PAD = 8



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

    def render(self, image):
        image = image.resize((self.width, self.height))
        raw = self._img_to_raw(image)
        if raw is None:
            return
        self._write_raw(raw, 0, 0, self.width, self.height)

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
            self._read_abs_range(ABS_MT_POSITION_X, ABS_MT_POSITION_Y)
            log(f'touch: {device} range={self.max_x}x{self.max_y}')
        except OSError as e:
            log(f'touch: cannot open {device}: {e}')
            self.fd = None
        self.cur_x, self.cur_y = None, None
        self._touching = False

    def _read_abs_range(self, code_x, code_y):
        buf = bytearray(24)
        try:
            fcntl.ioctl(self.fd, EVIOCGABS_BASE + code_x, buf)
            self.max_x = struct.unpack('iiiii', buf[:20])[2] or self.max_x
            fcntl.ioctl(self.fd, EVIOCGABS_BASE + code_y, buf)
            self.max_y = struct.unpack('iiiii', buf[:20])[2] or self.max_y
        except OSError:
            try:
                fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_X, buf)
                self.max_x = struct.unpack('iiiii', buf[:20])[2] or self.max_x
                fcntl.ioctl(self.fd, EVIOCGABS_BASE + ABS_Y, buf)
                self.max_y = struct.unpack('iiiii', buf[:20])[2] or self.max_y
            except OSError:
                pass

    def read_tap(self, timeout):
        """Block up to `timeout` seconds and return (nx, ny) on finger-lift or None."""
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
                if code in (ABS_X, ABS_MT_POSITION_X):
                    self.cur_x = value
                elif code in (ABS_Y, ABS_MT_POSITION_Y):
                    self.cur_y = value
                elif code == ABS_MT_TRACKING_ID:
                    if value >= 0:
                        self._touching = True
                    elif self._touching and self.cur_x is not None and self.cur_y is not None:
                        self._touching = False
                        return self._normalize(self.cur_x, self.cur_y)
            elif ev_type == EV_KEY and code == BTN_TOUCH:
                if value == 1:
                    self._touching = True
                elif self._touching and self.cur_x is not None and self.cur_y is not None:
                    self._touching = False
                    return self._normalize(self.cur_x, self.cur_y)

    def _normalize(self, x, y):
        return (
            min(1.0, max(0.0, x / self.max_x)),
            min(1.0, max(0.0, y / self.max_y)),
        )

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


def _load_font(size, mono=False):
    paths = MONO_FONT_PATHS if mono else FONT_PATHS
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    if mono:
        return _load_font(size, mono=False)
    return ImageFont.load_default()


def _deep_merge(base, override):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_config(config_dir):
    config = {'screens': {}}
    for path in sorted(glob.glob(os.path.join(config_dir, '*.yaml'))):
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            _deep_merge(config, data)
        except Exception as e:
            log(f'config: skip {path}: {e}')
    return config


def _hit(rect, nx, ny, w, h):
    x1, y1, x2, y2 = rect
    px, py = nx * w, ny * h
    return x1 <= px <= x2 and y1 <= py <= y2


def _label_lines(label):
    if isinstance(label, list):
        return [str(l) for l in label]
    return str(label).splitlines() or ['']


def _clip_lines(text, lines):
    if not text:
        return []
    result = [l for l in text.splitlines() if l.strip()]
    result = result[-lines:]
    return [l[:MODAL_STDERR_CHARS] for l in result]


class Screen:
    def __init__(self, name, payload, fb_size, fonts):
        self._name = name
        self._payload = payload
        self._fb_size = fb_size
        self._fonts = fonts
        self._buttons = []
        self._last_render = -1.0
        self._invalidated = True

    def name(self):
        return self._name

    def invalidate(self):
        self._invalidated = True

    def render(self, fb, pressed=None, progress=None):
        now = time.monotonic()
        if not self._invalidated and (now - self._last_render) < RENDER_INTERVAL:
            return None
        self._invalidated = False
        self._last_render = now

        img, buttons = self._build_screen(pressed, progress)

        self._buttons = buttons
        fb.render(img)
        return img

    def hit_test(self, nx, ny):
        w, h = self._fb_size
        for rect, item in self._buttons:
            if _hit(rect, nx, ny, w, h):
                return item
        return None

    def _build_screen(self, pressed, progress):
        w, h = self._fb_size
        font_title, font_label, font_btn, _ = self._fonts
        items = self._payload.get('items', [])
        title = self._payload.get('title', '')

        img = Image.new('RGB', (w, h), BG)
        draw = ImageDraw.Draw(img)
        buttons = []

        content_h = TIMEOUT_BAR_H + PAD
        if title:
            bbox = draw.textbbox((0, 0), title, font=font_title)
            content_h += (bbox[3] - bbox[1]) + ITEM_SPACING * 2
        line_h = draw.textbbox((0, 0), 'A', font=font_label)[3]
        for item in items:
            itype = item.get('type', 'label')
            n = len(_label_lines(item.get('label', '')))
            if itype == 'button':
                content_h += BUTTON_HEIGHT + ITEM_SPACING
            elif itype == 'text':
                content_h += line_h * n + TEXT_PAD * 2 + ITEM_SPACING
            else:
                content_h += line_h * n + ITEM_SPACING


        y = max(PAD, (h - TIMEOUT_BAR_H - content_h) // 2)

        if title:
            bbox = draw.textbbox((0, 0), title, font=font_title)
            tw = bbox[2] - bbox[0]
            draw.text(((w - tw) // 2, y), title, fill=TITLE_FG, font=font_title)
            y += (bbox[3] - bbox[1]) + ITEM_SPACING * 2

        for item in items:
            itype = item.get('type', 'label')
            label = item.get('label', '')
            if itype == 'button':
                x1, y1 = PAD, y
                x2, y2 = w - PAD, y + BUTTON_HEIGHT
                is_pressed = item is pressed
                draw.rectangle([x1, y1, x2, y2],
                               fill=BTN_PRESSED_BG if is_pressed else BTN_BG)
                draw.rectangle([x1, y1, x2, y2],
                               outline=BTN_PRESSED_BORDER if is_pressed else BTN_BORDER,
                               width=2)
                bbox = draw.textbbox((0, 0), label, font=font_btn)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                draw.text(((w - tw) // 2, y1 + (BUTTON_HEIGHT - th) // 2),
                          label, fill=BTN_FG, font=font_btn)
                buttons.append(((x1, y1, x2, y2), item))
                y += BUTTON_HEIGHT + ITEM_SPACING
            elif itype == 'text':
                lines = _label_lines(label)
                box_h = line_h * len(lines) + TEXT_PAD * 2
                draw.rectangle([PAD, y, w - PAD, y + box_h],
                               fill=TEXT_BG, outline=TEXT_BORDER, width=1)
                ty = y + TEXT_PAD
                for line in lines:
                    draw.text((PAD + TEXT_PAD, ty), line, fill=LABEL_FG, font=font_label)
                    ty += line_h
                y += box_h + ITEM_SPACING
            else:
                for line in _label_lines(label):
                    bbox = draw.textbbox((0, 0), line, font=font_label)
                    tw = bbox[2] - bbox[0]
                    draw.text(((w - tw) // 2, y), line, fill=LABEL_FG, font=font_label)
                    y += line_h
                y += ITEM_SPACING

        bar_y = h - TIMEOUT_BAR_H
        draw.rectangle([0, bar_y, w, h], fill=TBAR_BG)
        if progress is not None:
            bar_w = int(w * max(0.0, min(1.0, progress)))
            draw.rectangle([0, bar_y, bar_w, h], fill=TBAR_FG)

        return img, buttons


class RecoveryApp:
    def __init__(self, fb, touch, config, timeout):
        self.fb = fb
        self.touch = touch
        self.screens = config.get('screens', {})
        self.timeout = timeout
        self._fonts = (
            _load_font(TITLE_SIZE),
            _load_font(LABEL_SIZE),
            _load_font(BUTTON_SIZE),
            _load_font(MONO_SIZE, mono=True),
        )

    def _fb_size(self):
        return (self.fb.width, self.fb.height)

    def render_screen(self, name, payload = None, timeout = None):
        start = time.monotonic()
        timeout = timeout or self.timeout
        payload = payload or self.screens.get(name)
        screen_obj = Screen(name, payload, self._fb_size(), self._fonts)
        while True:
            now = time.monotonic()
            remaining = (start + timeout) - now
            if remaining <= 0:
                log('run: auto-close timeout expired')
                return None
            screen_obj.render(self.fb, progress=remaining / timeout)
            tap = self.touch.read_tap(RENDER_INTERVAL)
            if tap is None:
                continue
            item = screen_obj.hit_test(*tap)
            if item is None:
                continue
            screen_obj.invalidate()
            screen_obj.render(self.fb, pressed=item, progress=remaining / timeout)
            time.sleep(RENDER_INTERVAL)
            return item

    def run(self):
        log(f'run: timeout={self.timeout}s')

        screen = 'main'
        screen_payload = None

        while screen:
            item = self.render_screen(screen, screen_payload)
            if item is None:
                return None

            if 'cmd' in item:
                cmd = item['cmd']
                log(f'cmd: {cmd!r}')
                result = subprocess.run(
                    ['bash', '-c', cmd] if isinstance(cmd, str) else cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                log(f'cmd: exit={result.returncode} screen={item.get("screen", None)}')
                if not result.returncode:
                    screen_payload = {
                        'items': [
                            {'type': 'label', 'label': f'Command: {item.get("label", "")}'},
                            {'type': 'text', 'label': f'Exit code: {result.returncode}'},
                            {'type': 'text', 'label': _clip_lines(result.stdout.strip(), MODAL_STDERR_LINES)},
                            {'type': 'button', 'label': 'OK', 'screen': item.get('screen', screen)}
                        ]
                    }
                    screen = 'modal'
                    continue

            if item.get('screen') is not None:
                screen = item['screen']
                screen_payload = None
                continue

        log('run: exit')

def main():
    parser = argparse.ArgumentParser(description='Recovery screen for /dev/fb0')
    parser.add_argument('--fb', default='/dev/fb0', help='Framebuffer device')
    parser.add_argument('--touch', default='/dev/input/event0',
                        help='Touch input device (or "none" to disable)')
    parser.add_argument('--config-dir',
                        default='/usr/local/share/recovery-screen',
                        help='Directory with YAML config files')
    parser.add_argument('--timeout', type=float, default=60,
                        help='Auto-close timeout in seconds shown with progress bar')
    args = parser.parse_args()

    config = load_config(args.config_dir)
    if 'main' not in config.get('screens', {}):
        print('No "main" screen defined in config', file=sys.stderr)
        sys.exit(1)

    fb = Framebuffer(args.fb)
    touch = TouchReader(args.touch, fb.width, fb.height)
    try:
        RecoveryApp(fb, touch, config, args.timeout).run()
    finally:
        fb.clear()
        touch.close()
        fb.close()


if __name__ == '__main__':
    main()
