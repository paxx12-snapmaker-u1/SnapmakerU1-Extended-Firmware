# stream-snap-mqtt

Snapmaker U1 camera interface service.

MQTT-based camera control service for Snapmaker U1 3D printer. Handles timelapse recording, monitoring snapshots, and camera control commands via JSON-RPC over MQTT.

## Features

- Timelapse recording with automatic video generation (ffmpeg)
- Live monitoring with periodic snapshots
- JSON-RPC over MQTT for camera commands
- Automatic timelapse recovery on restart

## MQTT Methods

- `camera.start_timelapse` - Start timelapse recording
- `camera.stop_timelapse` - Stop and generate video
- `camera.start_monitor` - Start periodic snapshots
- `camera.stop_monitor` - Stop monitoring
- `camera.take_a_photo` - Take single snapshot
