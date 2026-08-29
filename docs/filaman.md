---
title: FilaMan Integration
---

# FilaMan Integration

Native spool tracking and usage reporting via
[FilaMan](https://github.com/ManuelW77/FilaMan), a self-hosted filament
management system.

## What It Provides

- Full support for the U1's multiple toolheads: each extruder tracks
  its own active spool, usage, and sensor state independently.
- Tracks the active spool per extruder and reports filament usage to
  FilaMan as printing progresses, per toolhead.
- Converts extruded length to weight using the spool's filament density,
  falling back to a configurable PLA default when it is missing.
- Assign spools per toolhead directly from the FilaMan card in fluidd
  (requires the [modified fluidd](https://github.com/ManuelW77/fluidd)
  carrying the FilaMan module), or via GCode macro/HTTP for
  slicer-driven or scripted assignment.
- Watches Klipper filament sensors per extruder and releases a spool
  automatically when that toolhead runs empty.
- Restores the printer's own filament display after a power cycle by
  re-pushing every assigned spool once Klipper becomes ready, and by
  answering the firmware's own filament-detect requests directly.

## Enabling

Edit `/oem/printer_data/config/extended/moonraker/05_filaman.cfg`,
uncomment the `[filaman]` section, set `server` to your FilaMan
instance URL, and restart Moonraker to apply. Whether the component
loaded is reported in `moonraker.log` and by
`GET /server/filaman/status`.

## Configuration

See the
[FilaMan Moonraker component README](https://github.com/ManuelW77/FilaMan-Moonraker-Komponente#configuration)
for the full list of options (`api_key`, `sync_rate`, filament sensor
mapping, runout handling, `moonraker.secrets` support, ...) and GCode
macro examples for assigning spools from a slicer or the printer's
console.

## Limitations

- FilaMan must be reachable from the printer over HTTP.
- Requires a separately hosted FilaMan instance; none is bundled with
  the firmware.

For the component implementation and full documentation, see the
[FilaMan Moonraker component repository](https://github.com/ManuelW77/FilaMan-Moonraker-Komponente).
