---
title: SnapAce / Anycubic ACE Pro
---

# SnapAce / Anycubic ACE Pro

SnapAce support integrates an Anycubic ACE Pro with the Snapmaker U1 as an
optional filament storage, feed-assist, retract, and dryer device.

This feature is based on the GPL-3.0 SnapAce project by BlackFrogKok:
https://github.com/BlackFrogKok/SnapAce

## Status

SnapAce support is experimental and disabled by default. It targets Anycubic
ACE Pro only. ACE 2 Pro support is not claimed unless protocol compatibility is
confirmed.

## Hardware Setup

Connect the ACE Pro to the U1 over USB using the wiring described by the
SnapAce project. After enabling the feature, verify the serial device path in:

```bash
/oem/printer_data/config/extended/klipper/ace.cfg
```

The default serial path is:

```ini
serial: /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
```

## Enabling

Open Firmware Config at `http://<printer-ip>/firmware-config/`, then enable:

**Snapmaker Components > SnapAce / Anycubic ACE Pro**

The setting copies the default `ace.cfg` into the extended Klipper config
directory and restarts Klipper.

When SnapAce is disabled, the editable config file does not exist. Firmware
Config creates it at enable time from this built-in template:

```bash
/usr/local/share/firmware-config/tweaks/klipper/ace.cfg
```

After enabling, edit or inspect the active config here:

```bash
/oem/printer_data/config/extended/klipper/ace.cfg
```

## Verifying the ACE Serial Path

Before running ACE feed or retract commands, verify that Linux sees the ACE Pro
USB serial device:

```bash
ls -l /dev/serial/by-id/
```

The default `ace.cfg` expects:

```ini
serial: /dev/serial/by-id/usb-ANYCUBIC_ACE_1-if00
```

If your ACE Pro appears with a different name, update the `serial:` value in
`/oem/printer_data/config/extended/klipper/ace.cfg` and restart Klipper.

## Configuration

Tune these values in `extended/klipper/ace.cfg`:

```ini
feed_speed: 80
retract_speed: 80
retract_length: 650
feed_length: 600
max_dryer_temperature: 70
```

`feed_length` should leave filament about 5-6 cm from the toolhead after the ACE
loading procedure.

## Commands

- `ACE_FEED INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]`
- `ACE_RETRACT INDEX=<0-3> LENGTH=<mm> [SPEED=<mm/s>]`
- `ACE_ENABLE_FEED_ASSIST INDEX=<0-3>`
- `ACE_DISABLE_FEED_ASSIST INDEX=<0-3>`
- `ACE_START_DRYING TEMP=<C> DURATION=<minutes>`
- `ACE_STOP_DRYING`

Convenience macros are also provided:

- `ACE_DRYING_ON`
- `ACE_DRYING_OFF`

## Disabling

Set **SnapAce / Anycubic ACE Pro** back to **Disabled** in Firmware Config. This
removes `extended/klipper/ace.cfg` and restarts Klipper, returning filament
feeding to the stock U1 path.

## Limitations

- Requires real ACE Pro hardware validation.
- If SnapAce is enabled and the serial path is wrong or the ACE Pro is
  disconnected, ACE commands report errors and Klipper keeps retrying the ACE
  connection.
- RFID metadata read by ACE is mapped into `SET_PRINT_FILAMENT_CONFIG` when ACE
  reports an RFID slot update.
