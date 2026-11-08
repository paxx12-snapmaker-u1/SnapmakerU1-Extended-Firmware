# Remote Screen 2 - Simple HTTP Framebuffer Server

A lightweight Python HTTP server that exposes the framebuffer as PNG snapshots and accepts touch input.

## Features

- Reads from `/dev/fb0` and converts to PNG
- Simple web viewer that refreshes at ~2Hz
- Touch input support via `/dev/input/event0`

## Endpoints

- `GET /` - HTML viewer with auto-refresh and touch support
- `GET /snapshot` - Current framebuffer as PNG image
- `GET /stream` - PNG frames pushed whenever the screen changes (`multipart/mixed` stream)
- `POST /touch?x=&y=` - Send touch event at coordinates

## Configuration

Edit `/etc/init.d/S99fb-http` to change:

- `PORT` - HTTP port (default: 8092)
- `BIND` - Bind address (default: 0.0.0.0)
- `FB_DEVICE` - Framebuffer device (default: /dev/fb0)
- `TOUCH_DEVICE` - Touch input device (default: /dev/input/event0)
- `HTML_TEMPLATE` - Path to HTML template file (default: /usr/local/share/fb-http-server/index.html)

## Dependencies

- Python 3
- Pillow (installed via pip)
