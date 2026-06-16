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
- **Filament Assist Source** — ACE Feed Assist / U1 Feeders / Off

ACE also appears in the shared RFID settings:

- **RFID Hardware** — Snapmaker / External / ACE
- **RFID Software** — Snapmaker / OpenRFID / OpenRFID (generic) / ACE

The ACE options in RFID Hardware and RFID Software only appear when Anycubic ACE is enabled.

### ACE Model

- `Auto Detect` - detect a supported ACE model from `/dev/serial/by-id`
- `Anycubic ACE Pro` - force ACE Pro JSON protocol
- `Anycubic ACE 2 Pro` - force ACE 2 Pro protobuf protocol

### Filament Assist Source

Only one assist source can be active:

- `ACE Feed Assist` - ACE handles feed assist while printing
- `U1 Feeders` - U1 feeders handle the assist path
- `Off` - no feed assist

This avoids unsafe combinations where both ACE and U1 feeder assist try to
control the same filament path.

### RFID Hardware / RFID Software

RFID detection is split into hardware (physical reader) and software (tag
decoder). Select ACE as the hardware reader to use the ACE slot RFID
reader. Select ACE as the software decoder to use ACE-format NTAG tags
(written via U1-RFID app with Ace Format).

Valid combinations:

| Hardware | Software | Effect |
|---|---|---|
| Snapmaker | Snapmaker | U1 built-in reader, stock decoder |
| Snapmaker | OpenRFID | U1 built-in reader, OpenRFID decoder (Bambu/Creality) |
| External | *(hidden)* | External reader, self-contained |
| ACE | ACE | ACE slot reader, ACE-format tags |
| ACE | OpenRFID | ACE slot reader, OpenRFID decoder (future) |

## USB Detection

With `device_model: auto`, the model and serial path are detected automatically
— no manual configuration needed. If you need to verify or troubleshoot:

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

A manual `serial:` override is only needed if your ACE enumerates with a
non-standard USB id. In that case update the path in:

```bash
/oem/printer_data/config/extended/klipper/ace.cfg
```

and restart Klipper.

## Configuration

Important values in `extended/klipper/ace.cfg`:

```ini
device_model: auto
assist_source: snapmaker
enable_feed_assist: False
enable_feeder_mode: True
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
