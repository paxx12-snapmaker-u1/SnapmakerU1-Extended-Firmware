# 01-schedule-print

Adds a Moonraker component that starts a print job at a user-specified time.

## Features

- Schedule a print via REST API or the built-in web UI at `http://<printer-ip>/schedule-print/`
- Timezone selection — any IANA timezone, no `tzdata` dependency required on the printer
- Survives Moonraker restarts: the scheduled job is persisted to
  `printer_data/config/extended/schedule_print.json` (on the persistent `/oem` partition)
  and re-armed on the `server:klippy_ready` event
- Activated by default via `05_schedule_print.cfg`

## Architecture

A Moonraker component with `eventloop.delay_callback` was chosen over cron because:

- This firmware uses BusyBox init (no systemd, no systemd timers)
- The rootfs is an OverlayFS; writes to `/` are not persistent across firmware resets
- There is no persistent cron on this system
- Moonraker's event loop is already running and provides reliable timer callbacks

Timezone conversion is handled entirely client-side (browser `Intl` API). The server
receives an ISO 8601 timestamp with UTC offset and stores a Unix timestamp — no timezone
logic is needed on the printer.

## API

```
POST /server/schedule_print
  Body (JSON): {"filename": "my_file.gcode", "time": "2026-06-27T07:30:00+02:00", "timezone": "Europe/Paris"}
  Response: current status (see GET)

GET /server/schedule_print
  Response: {"scheduled": {"filename": "...", "target_ts": 1234567890, "timezone": "...", "seconds_remaining": 3600}}
            {"scheduled": null}  — when nothing is scheduled

DELETE /server/schedule_print
  Response: {"cancelled": true|false}
```

## Files

| Path | Purpose |
|------|---------|
| `root/home/lava/moonraker/moonraker/components/schedule_print.py` | Moonraker component |
| `root/usr/local/share/firmware-config/extended/moonraker/05_schedule_print.cfg` | Activates `[schedule_print]` |
| `root/usr/local/share/schedule-print/html/index.html` | Web UI |
| `root/etc/nginx/fluidd.d/schedule-print.conf` | Nginx location `/schedule-print/` |
