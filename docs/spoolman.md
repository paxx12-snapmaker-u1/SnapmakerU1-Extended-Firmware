---
title: Spoolman Integration
---

# Spoolman Integration

Automatic filament metadata sync and spool tracking via
[Spoolman](https://github.com/Donkie/Spoolman).

## What It Provides

- Resolves a Spoolman spool by ID or RFID card UID and applies its
  metadata (vendor, material, variant, colour) to the extruder channel.
- Associates RFID card UIDs with spools automatically, so scanning a
  known card loads its filament without any manual step.
- Tracks the active spool in Moonraker so Spoolman can update remaining
  filament weight as you print.

## Enabling

Enable via Fluidd/Mainsail settings under
**Snapmaker Components > Spoolman Integration**, set the Spoolman host,
and reboot. Set the same toggle to **Disabled** to turn it off.

## GCode Commands

### `SET_SPOOL_ID`

Assign a Spoolman spool to an AFC lane. Reads the lane's RFID card UID,
binds it to the spool (so a later scan resolves automatically), and
applies the filament metadata to the channel:

```
SET_SPOOL_ID LANE=E0 SPOOL_ID=5
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LANE` | — | AFC lane name (e.g. `E0`) |
| `SPOOL_ID` | `0` | Spoolman spool ID; `0` clears the assignment |

## Limitations

- Spoolman must be reachable from the printer over HTTP.
- Variant defaults to `Basic` for Snapmaker-branded filaments when not
  set in Spoolman; empty for all other vendors.

For the wire format, custom fields, and component flow see the
[design notes](design/spoolman.md). For AFC lane status that surfaces
`spool_id` see [AFC-Lite](afc-lite.md).
