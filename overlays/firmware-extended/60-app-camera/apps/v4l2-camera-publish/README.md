# v4l2-camera-publish

A libv4l2 plugin that publishes the frames `unisrv` captures from one V4L2 device to a Unix socket, for `capture-raw-mpp` (v4l2-mpp) to encode. `unisrv` keeps owning the real camera.

## Description

`unisrv` captures through `librockit`, which uses libv4l2 with buffers it allocates itself and queues as dma-buf fds (`V4L2_MEMORY_DMABUF`). libv4l2 gives each device to the first plugin in `/usr/lib/libv4l/plugins` whose `init()` accepts the fd. `libv4l-camera-publish.so` accepts only `V4L2_CAMERA_PUBLISH_DEVICE`, and only in `unisrv`, then loads Rockchip's `libv4l-mplane.so` itself and forwards every call to it, so capture behaves as on stock firmware. The plugin name must sort before `libv4l-mplane.so`.

After each successful `VIDIOC_DQBUF`, the plugin publishes the frame:

- At most one frame is in flight. Frames captured until its ack arrives are not published.
- The ack says whether the service wants frames. Without demand only a header is sent, so an unused camera costs a few small messages per frame.
- A `VIDIOC_QBUF` of the buffer still in flight waits up to `V4L2_CAMERA_PUBLISH_TIMEOUT` for the ack, so the camera does not overwrite a frame being encoded. After that the buffer is requeued anyway, and the frame may tear.
- If the service is not running, capture is unaffected and the plugin retries once per second.

## Environment Variables

The plugin is only active when `V4L2_CAMERA_PUBLISH_DEVICE` is set.

| Variable | Default | Description |
|----------|---------|-------------|
| `V4L2_CAMERA_PUBLISH_DEVICE` | (empty) | Device path to publish |
| `V4L2_CAMERA_PUBLISH_PATH` | (empty) | Unix socket of `capture-raw-mpp` |
| `V4L2_CAMERA_PUBLISH_MODE` | `dmabuf` | `dmabuf` passes the buffer fd (zero copy), `data` sends the frame bytes |
| `V4L2_CAMERA_PUBLISH_TIMEOUT` | `50` | Longest wait in milliseconds in the capture path |
| `V4L2_CAMERA_PUBLISH_NEXT_PLUGIN` | `/usr/lib/libv4l/plugins/libv4l-mplane.so` | Plugin that handles the device |
| `V4L2_CAMERA_PUBLISH_DEBUG` | `0` | Set to `1` to enable debug logging to stderr |

## Usage

This is the `paxx12` internal camera mode: the lmd hook sets the variables above, and `S99v4l2-mpp-mipi` runs `capture-raw-mpp`. To try it by hand, see `test/run-camera-publish.sh`.

## Protocol

Documented in `apps/capture-raw-mpp/README.md` of v4l2-mpp, added by `v4l2-mpp-patches/01-add-capture-raw-mpp.patch`.
