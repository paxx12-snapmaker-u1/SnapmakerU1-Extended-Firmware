---
title: Spoolman Integration Design
---

# Spoolman Integration Design

This document explains the integration approach for Spoolman and why it uses
`SET_PRINT_FILAMENT_CONFIG` rather than the `filament_detect` path.

## Two filament configuration paths

The Snapmaker U1 firmware has two distinct paths for setting per-channel filament metadata:

### `filament_detect` (RFID/hardware path)

`filament_detect` is owned by the RFID subsystem. When a physical spool card is scanned,
the RFID daemon calls `POST /printer/filament_detect/set` (or the equivalent WebSocket
method) with fields like `VENDOR`, `MAIN_TYPE`, `SKU`, `CARD_UID`, and `OFFICIAL`.

Setting `OFFICIAL=1` locks the channel: the firmware treats the filament as Snapmaker-branded
and disables manual editing. The `SKU` and `CARD_UID` fields are also only meaningful in
the RFID context — they encode the physical card identity.

### `SET_PRINT_FILAMENT_CONFIG` (runtime configuration path)

`SET_PRINT_FILAMENT_CONFIG` is the gcode command for setting filament metadata without
asserting hardware authority. It accepts `VENDOR`, `FILAMENT_TYPE`, `FILAMENT_SUBTYPE`,
`FILAMENT_COLOR_RGBA`, and `FILAMENT_SPOOL_ID`, and never marks the channel as official.

A `FILAMENT_SPOOL_ID` guard prevents silent overwrites: once a spool ID is set, further
calls require `FORCE=1` unless they also supply a `FILAMENT_SPOOL_ID`.

## Why Spoolman uses `SET_PRINT_FILAMENT_CONFIG`

Spoolman is a metadata registry, not a hardware detection system. It knows filament
material, vendor, colour, and tracking ID — but not the physical RFID card UID or
whether a spool is Snapmaker-branded. Routing through `filament_detect` would:

- Incorrectly flag channels as having official/scanned filament.
- Require patching `filament_detect.py` to accept `SPOOL_ID` as a passthrough field,
  coupling the RFID path to Spoolman semantics.
- Bypass the `FILAMENT_SPOOL_ID` guard that prevents accidental overwrites.

`SET_PRINT_FILAMENT_CONFIG FORCE=1` is the correct path: it sets vendor, type, colour,
and spool ID as runtime metadata without touching the official/RFID state.

Keeping the two paths separate also enables future divergence detection. Because
`filament_detect` holds the raw RFID scan result and `print_task_config` holds the
Spoolman-sourced metadata independently, the firmware can later compare the two — for
example, flagging when the material or colour read from an RFID card does not match what
Spoolman records for the associated spool ID.

## `spoollink` agent flow

1. Moonraker agent (`spoollink.py`) connects to Moonraker, identifies as an agent, and
   registers `spoollink_resolve_spool` as a remote method.
2. On every (re)connect, `spoollink` calls `GET api/v1/field/spool` and
   `GET api/v1/field/filament` to verify the `card_uids` and `variant` custom fields exist,
   creating them via `POST api/v1/field/{entity}/{key}` if missing.  This mirrors the
   behaviour of the iOS/Android spool-link apps on first connection.
3. Klipper (or AFC) calls `spoollink_resolve_spool` with `channel`, `spool_id`, and/or `card_uid`.
4. `spoollink` resolves the spool from Spoolman:
   - By ID: `GET api/v1/spool/{id}`
   - By card UID: `GET api/v1/spool?limit=1000&allow_archived=true`, then filter client-side
     on `extra.card_uids` (comma-separated, JSON-encoded uppercase hex UIDs).
5. If both `spool_id` and `card_uid` are given and the card is not yet in the spool's
   `card_uids`, `spoollink` appends it via `PATCH api/v1/spool/{id}` with
   `{"extra": {"card_uids": "\"UID1,UID2\""}}` (JSON-encoded string, as required by
   Spoolman's custom field API).
6. On success, `spoollink` sends `printer.gcode.script` over the existing WebSocket
   with `SET_PRINT_FILAMENT_CONFIG CONFIG_EXTRUDER={channel} VENDOR=... FILAMENT_TYPE=...
   FILAMENT_SUBTYPE={variant} FILAMENT_COLOR_RGBA=...FF FILAMENT_SPOOL_ID={id} FORCE=1`,
   where `variant` is decoded from `filament.extra.variant`.
7. Klipper processes the gcode, stores the metadata in `print_task_config`, and notifies
   subscribers. `AFC_lane.get_status()` surfaces `spool_id` to Fluidd/Mainsail.

## Spoolman custom fields

| Entity | Key | Purpose |
|--------|-----|---------|
| `spool` | `card_uids` | Comma-separated uppercase hex NFC UIDs associated with the spool |
| `filament` | `variant` | Filament subtype / variant (e.g. "Silk", "Matte") |

Both fields use Spoolman's double-serialised string convention: the inner value is JSON-encoded
before storage, so a single UID `"AABBCCDD"` is stored as `"\"AABBCCDD\""`.  Spoollink decodes
these on read and re-encodes on write, matching the iOS/Android apps.

## Patch surface

Only `print_task_config.py` is patched (patch `03-add-spool-id-support.patch`):

- Adds `filament_spool_id` array to `DEFAULT_PRINT_TASK_CONFIG`.
- Stores `SPOOL_ID` when called from the `filament_detect` path (for future RFID cards
  that carry a spool ID in their payload).
- Adds `FILAMENT_SPOOL_ID` parameter to `SET_PRINT_FILAMENT_CONFIG`.
- Guards manual editing when a spool ID is active and spoollink is connected.

`filament_detect.py` is not patched. The `filament_detect` path remains unchanged;
it does not accept or propagate `SPOOL_ID`.
