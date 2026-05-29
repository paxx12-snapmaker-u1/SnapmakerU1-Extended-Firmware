---
title: Spoolman Integration
---

# Spoolman Integration

Automatic filament metadata sync and spool tracking via
[Spoolman](https://github.com/Donkie/Spoolman).

For design rationale and implementation details see
[design/spoolman.md](design/spoolman.md).
For AFC lane status that surfaces `spool_id` see [AFC-Lite](afc-lite.md).

## What It Provides

- Resolves a Spoolman spool by ID or RFID card UID and applies its
  metadata (vendor, material, variant, colour) to a Klipper extruder
  channel via `SET_PRINT_FILAMENT_CONFIG`.
- Associates RFID card UIDs with spools by appending to
  `extra.card_uids` — the same custom field used by the spool-link
  iOS/Android apps.
- Tracks the active spool in Moonraker so Spoolman can update
  remaining filament weight.

## How It Works

The `spoollink` Moonraker agent connects over WebSocket, identifies as
an agent, and registers the `spoollink_resolve_spool` remote method.

When called with `CHANNEL` and optionally `SPOOL_ID`:

1. Fetches the spool from Spoolman by ID and/or by scanning all spools
   for a matching `extra.card_uids` entry (client-side filter).
2. If a `SPOOL_ID` is given and the card UID is not yet in the spool's
   `card_uids` list, appends it via `PATCH api/v1/spool/{id}`.
3. Applies vendor, material, variant, and colour to Klipper via
   `SET_PRINT_FILAMENT_CONFIG ... FILAMENT_SPOOL_ID=... FORCE=1`.

A `[delayed_gcode]` macro (`_spoollink_sync_active_spool`) runs every
10 s, reads `print_task_config.filament_spool_id` for the active
extruder, and calls `spoolman_set_active_spool` in Moonraker.

## Enabling

Enable via Fluidd/Mainsail settings under
**Snapmaker Components > Spoolman Integration**.

To disable, set the same toggle to **Disabled**.

## GCode Commands

### `SET_SPOOL_ID`

```
SET_SPOOL_ID CHANNEL=0 SPOOL_ID=5
```

Reads the RFID card UID from `printer.filament_detect.info[CHANNEL].CARD_UID`,
resolves the spool from Spoolman, binds the card UID to the spool if
not already associated, and applies the filament metadata to the channel.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CHANNEL` | `0` | Physical extruder index (0–3) |
| `SPOOL_ID` | `0` | Spoolman spool ID; if omitted, lookup is by card UID only |

## Spoolman Custom Fields

Two custom fields are created in Spoolman on first enable (via
`spoollink.py --test`):

| Entity | Key | Purpose |
|--------|-----|---------|
| `spool` | `card_uids` | Comma-separated uppercase hex NFC UIDs |
| `filament` | `variant` | Filament subtype / variant (e.g. `Silk`, `Matte`) |

Both use Spoolman's double-serialised string convention — the same
format as the spool-link iOS/Android apps.

## Limitations

- Spoolman must be reachable from the printer over HTTP.
- Card UID lookup fetches up to 1000 spools and filters client-side.
- Variant defaults to `Basic` for Snapmaker-branded filaments when not
  set in Spoolman; empty for all other vendors.
