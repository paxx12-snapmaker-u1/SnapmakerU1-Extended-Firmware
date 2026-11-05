# v4l2-camera-socket

A libv4l2 plugin that serves one V4L2 device in `unisrv` from a Unix socket of raw frames instead of the real camera.

## Description

libv4l2 offers every device it opens to the plugins in `/usr/lib/libv4l/plugins`, in name order; the first plugin whose `init()` accepts the fd gets all of its ioctls. `libv4l-camera-socket.so` accepts only `V4L2_CAMERA_SOCKET_DEVICE`, and only in `unisrv`. Every other device goes on to the next plugin (Rockchip's `libv4l-mplane.so`), as on stock firmware, so the plugin name must sort before it.

`unisrv` captures through `librockit`, which uses libv4l2 with buffers it allocates itself and queues as dma-buf fds (`V4L2_MEMORY_DMABUF`). Frames are read directly into them. The claimed fd is replaced with a pipe holding the indexes of queued buffers, so `poll()` on it works as on a real device.

## Environment Variables

The plugin is only active when `V4L2_CAMERA_SOCKET_DEVICE` is set.

| Variable | Default | Description |
|----------|---------|-------------|
| `V4L2_CAMERA_SOCKET_DEBUG` | `0` | Set to `1` to enable debug logging to stderr |
| `V4L2_CAMERA_SOCKET_DEVICE` | (empty) | Device path to serve from the socket |
| `V4L2_CAMERA_SOCKET_PATH` | (empty) | Unix socket path for frame provider |
| `V4L2_CAMERA_SOCKET_TIMEOUT` | `1000` | Socket read timeout in milliseconds |
| `V4L2_CAMERA_SOCKET_WIDTH` | `1920` | Reported frame width |
| `V4L2_CAMERA_SOCKET_HEIGHT` | `1080` | Reported frame height |
| `V4L2_CAMERA_SOCKET_FORMAT` | `NV12` | Pixel format: `MJPEG`, `JPEG`, `YUYV`, or `NV12` |

## Usage

```sh
cp libv4l-camera-socket.so /usr/lib/libv4l/plugins/
V4L2_CAMERA_SOCKET_DEVICE=/dev/video11 V4L2_CAMERA_SOCKET_PATH=/tmp/capture-mipi-raw.sock unisrv
```

## Protocol

The library connects to the Unix socket per frame and reads one frame.
