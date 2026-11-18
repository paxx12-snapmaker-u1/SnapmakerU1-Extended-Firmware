# v4l2-imposter

A V4L2 wrapper library that intercepts V4L2 calls and fetches frames from a Unix socket instead of a real camera device.

## Description

This library provides drop-in replacements for libv4l2 functions (`v4l2_open`, `v4l2_close`, `v4l2_ioctl`, `v4l2_mmap`, `v4l2_munmap`). When loaded via `LD_PRELOAD`, it intercepts V4L2 operations and redirects frame capture to a socket-based frame provider.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `V4L2_IMPOSTER_DEBUG` | `0` | Set to `1` to enable debug logging to stderr |
| `V4L2_IMPOSTER_DEVICE` | `/dev/video0` | Target device path to intercept |
| `V4L2_IMPOSTER_SOCKET_PATH` | (empty) | Unix socket path for frame provider |
| `V4L2_IMPOSTER_SOCKET_TIMEOUT` | `1000` | Socket read timeout in milliseconds |
| `V4L2_IMPOSTER_WIDTH` | `1920` | Reported frame width |
| `V4L2_IMPOSTER_HEIGHT` | `1080` | Reported frame height |
| `V4L2_IMPOSTER_FORMAT` | `MJPEG` | Pixel format: `MJPEG`, `JPEG`, `YUYV`, or `NV12` |

## Usage

```sh
LD_PRELOAD=/path/to/libv4l2-imposter.so V4L2_IMPOSTER_SOCKET_PATH=/tmp/stream.sock your_application
```

## Protocol

The library connects to the Unix socket per frame and reads data until the socket is closed.
