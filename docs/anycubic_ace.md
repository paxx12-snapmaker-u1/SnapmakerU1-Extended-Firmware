---
title: Anycubic ACE Pro / ACE 2 Pro
---

# Anycubic ACE Pro / ACE 2 Pro

Anycubic ACE support integrates ACE Pro and ACE 2 Pro units with the Snapmaker
U1 as optional filament storage, feed assist, retract, dryer, RFID/status, and
gate-status devices.

This feature is disabled by default and is experimental.

## Upstream References

This integration is a clean Extended Firmware port informed by these projects:

- BlackFrogKok/SnapAce, commit `34a06e87bcd59ca3ebc845ed32a794627505437c`
- DnG-Crafts/U1-Ace, commit `f845339800445269069a60a55c9e517911c3f2f4`
- hakimio/U1-Ace `ace2`, commit `97e94b11f6f9b52e045dc89919f69405dda1d9cf`
- utkabobr/DuckACE
- printers-for-people/ACEResearch

## Supported Models

The user-facing models are:

- Anycubic ACE Pro
- Anycubic ACE 2 Pro

Internally, ACE Pro uses a JSON serial protocol at `115200` baud. ACE 2 Pro
uses a protobuf serial protocol at `230400` baud.

## Config Lifecycle

When Anycubic ACE is disabled, the editable config file does not exist.
Firmware Config creates it at enable time from this built-in template:

```bash
/usr/local/share/firmware-config/tweaks/klipper/ace.cfg
```

After enabling, edit or inspect the active config here:

```bash
/oem/printer_data/config/extended/klipper/ace.cfg
```

## Firmware Config Settings

Open Firmware Config at `http://<printer-ip>/firmware-config/`, then use:

**Snapmaker Components > Anycubic ACE**

Settings in this group:

- **Anycubic ACE** — Disabled / Enabled
- **ACE Model** — Auto Detect / Anycubic ACE Pro / Anycubic ACE 2 Pro
- **Filament Assist Source** — Anycubic ACE / Snapmaker U1 Feeders / Off
- **RFID / Filament Metadata** — Existing U1 Settings / Anycubic ACE Slots / Ignore RFID

### ACE Model

- `Auto Detect` - detect a supported ACE model from `/dev/serial/by-id`
- `Anycubic ACE Pro` - force ACE Pro JSON protocol
- `Anycubic ACE 2 Pro` - force ACE 2 Pro protobuf protocol

### Filament Assist Source

Only one assist source can be active:

- `Anycubic ACE` - ACE handles feed assist while printing
- `Snapmaker U1 Feeders` - U1 feeders handle the assist path
- `Off` - no feed assist

This avoids unsafe combinations where both ACE and U1 feeder assist try to
control the same filament path.

### RFID / Filament Metadata

- `Existing U1 Settings` - keep current U1/OpenRFID metadata behavior
- `Anycubic ACE Slots` - ACE slot RFID updates filament metadata
- `Ignore RFID` - ACE integration does not apply RFID metadata

`Existing U1 Settings` is the safest default because Extended Firmware already
has RFID and OpenRFID settings.

## Verifying USB Detection

Before enabling movement commands, verify that Linux sees the ACE device:

```bash
lsusb
ls -l /dev/serial/by-id/
```

Expected ACE Pro path:

```bash
/dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
```

Expected ACE 2 Pro path pattern:

```bash
/dev/serial/by-id/usb-1a86_USB_Single_Serial_*
```

If the ACE appears with a different path, update `serial:` in:

```bash
/oem/printer_data/config/extended/klipper/ace.cfg
```

and restart Klipper.

## Configuration

Important values in `extended/klipper/ace.cfg`:

```ini
device_model: auto
assist_source: ace
enable_feed_assist: True
enable_feeder_mode: False
rfid_source: existing

feed_length_slot1: 1000
load_length_slot1: 850
retract_length_slot1: 3000
max_dryer_temperature: 55
```

Tune all four slot lengths for your tube routing. Start with short manual
movement tests before running full load/unload workflows.

## Commands

- `ACE_FEED INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]`
- `ACE_RETRACT INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]`
- `ACE_ENABLE_FEED_ASSIST INDEX=<0-3>`
- `ACE_DISABLE_FEED_ASSIST INDEX=<0-3>`
- `ACE_START_DRYING TEMP=<C> DURATION=<minutes>`
- `ACE_STOP_DRYING`
- `ACE_GET_STATUS`
- `ACE_GET_TEMP`

Convenience macros are also provided:

- `ACE_DRYING_ON`
- `ACE_DRYING_OFF`

## Disabling

Set **Anycubic ACE** back to **Disabled** in Firmware Config.
This removes `extended/klipper/ace.cfg` and restarts Klipper, returning filament
feeding to the stock U1 path.

## Limitations

- ACE 2 Pro must enumerate as a USB serial device before firmware can connect.
- ACE 2 Pro support is based on the published `hakimio/U1-Ace` ACE2 branch and
  needs physical validation on U1 hardware.
- RFID metadata ownership is intentionally modeled as one policy setting to
  avoid conflicting with existing Extended Firmware RFID/OpenRFID settings.
