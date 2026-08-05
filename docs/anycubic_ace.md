---
title: Anycubic ACE Wiring and Test Guide
---

# Anycubic ACE Pro / ACE 2 Pro

This guide covers the cable connections and first-test procedure for the
experimental single-ACE mod on a Snapmaker U1.

The original Anycubic ACE Pro is sometimes called the ACE 1 Pro. It uses a
different host connection from the ACE 2 Pro:

- ACE Pro: direct USB data connection.
- ACE 2 Pro: USB-to-RS485 adapter connection.

This feature is experimental and has not yet been hardware-validated in the
current mod. It does not include RFID integration or Multi-ACE support.

> **Safety:** Verify the connector orientation and wiring with a multimeter.
> Leave every 5V/VCC wire disconnected. Connecting 5V can damage the ACE,
> adapter, printer, or host USB port.

## Build and enablement

The ACE mod is not part of the normal `extended` profile. Build an image with
the personal mod included:

~~~bash
./dev.sh make build PROFILE=extended-tareku99
~~~

After flashing that image:

1. Connect one ACE to the U1 by USB.
2. Enable **Advanced Mode** on the printer.
3. Open `http://<printer-ip>/firmware-config/`.
4. Select **Settings > Snapmaker Components**.
5. Set **Anycubic ACE (experimental)** to **Enabled**.

The setting checks for a compatible serial device, creates the active Klipper
configuration link, and restarts Klipper. The built-in driver detects the ACE
model and serial settings automatically.

The template is installed at:

~~~text
/usr/local/share/firmware-config/tweaks/klipper/ace.cfg
~~~

When enabled, the active configuration is:

~~~text
/oem/printer_data/config/extended/klipper/ace.cfg
~~~

Review the active configuration before loading filament. The four
`load_length_slotN` values must match the installed tube routing.

## ACE signal connector

The following view is from the front/mating side of the ACE signal connector,
with the clip or latch at the top:

~~~text
             clip/latch
                ||
      +-------------------+
      | [NC]  [D+]  [D-]  |
      | [NC]  [GND] [5V]  |
      +-------------------+
~~~

The `5V` position is shown for identification only. Do not connect it.

## ACE Pro wiring

The original ACE Pro uses a direct USB data path:

~~~text
U1 USB port
    |
    | USB 2.0 cable or USB-A breakout
    |
Micro-Fit 3.0 2x3P pigtail
    |
ACE Pro signal port
~~~

Connect only these wires:

| USB signal | ACE Pro connector |
|---|---|
| USB D- | D- |
| USB D+ | D+ |
| USB GND | GND |
| USB 5V | Leave disconnected |

Typical USB 2.0 colors are white for D-, green for D+, and black for GND, but
verify the actual cable rather than relying on colors.

## ACE 2 Pro wiring

The ACE 2 Pro requires an RS485 adapter. Do not connect it directly to a USB
data cable.

~~~text
U1 USB port
    |
    | USB
    |
USB-to-RS485 adapter
    |
    | RS485 A, B, and GND
    |
Micro-Fit 3.0 2x3P pigtail
    |
ACE 2 Pro signal port
~~~

Connect the adapter as follows:

| Adapter signal | ACE 2 Pro connector | Notes |
|---|---|---|
| GND | GND | Always connect |
| A, 485+, or D+ | Try D+ or D- | Swap with B if commands time out |
| B, 485-, or D- | Try D- or D+ | Swap with A if commands time out |
| VCC | Leave disconnected | Do not connect 5V |

RS485 adapter labels are not standardized. If the adapter enumerates but ACE
commands time out, keep GND connected and swap only the A/B pair.

CH343 adapters are preferred because they are closest to the original Anycubic
USB-to-RS485 path. CH340/CH341 adapters may also work and commonly enumerate
as USB ID `1a86:7523`.

## USB detection

With the ACE connected, inspect the serial devices:

~~~bash
ls -l /dev/serial/by-id/
~~~

The current driver looks for:

~~~text
ACE Pro:
/dev/serial/by-id/usb-ANYCUBIC*

ACE 2 Pro:
/dev/serial/by-id/usb-1a86_USB_Single_Serial_*
/dev/serial/by-id/usb-1a86_USB_Serial*
~~~

The mod includes a permission rule for CH340/CH341 devices with USB ID
`1a86:7523`. Reconnect the adapter or reboot the printer if the serial
device is not accessible.

## First connection test

After enabling the feature, run these commands from the printer console:

~~~gcode
ACE_GET_STATUS
ACE_GET_TEMP
ACE_DRYING_ON
ACE_GET_STATUS
ACE_GET_TEMP
ACE_DRYING_OFF
~~~

A successful connection should report the detected model, the corresponding
protocol and baud rate, connected status, and four gate states. ACE 2 Pro
should also report temperature, humidity, and dryer state.

The convenience macros are:

- `ACE_STATUS`
- `ACE_TEMP`
- `ACE_DRYING_ON`
- `ACE_DRYING_OFF`
- `ACE_REFRESH_CONNECTION`

If the ACE is unplugged and reconnected after Klipper starts, run
`ACE_REFRESH_CONNECTION` or use the Firmware Config refresh action.

## First filament-load test

Start with status and temperature tests, then test one slot at a time. Tune the
active configuration values for the actual tube lengths:

~~~ini
load_length_slot1: 2100
load_length_slot2: 2100
load_length_slot3: 2100
load_length_slot4: 2100
~~~

These are starting values in the current mod, not universal measurements.

The intended load sequence is:

1. The normal U1 load flow selects a slot.
2. The ACE feeds that slot.
3. The physical U1 filament sensor stops the ACE feed.
4. The normal U1 heat, extrude, and flush steps continue.

Stock U1 runout/UI gating can still prevent a load from starting before the ACE
has moved filament far enough. Unload behavior is not hardware-validated in
this first pass.

## Disabling the feature

Set **Anycubic ACE (experimental)** back to **Disabled** in Firmware Config.
The active ACE configuration link is removed and Klipper restarts, returning
filament loading to the stock U1 path.

## Wiring checklist

- Power down before changing connector wiring.
- Verify the six-pin connector orientation before inserting the pigtail.
- Connect GND.
- Leave ACE 5V/VCC disconnected.
- Do not connect ACE 2 Pro directly to USB data lines.
- If ACE 2 Pro enumerates but does not answer, swap only RS485 A and B.
- Test status and temperature before attempting a filament load.
