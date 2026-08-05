---
title: Anycubic ACE Wiring and Test Guide
---

# Anycubic ACE Pro / ACE 2 Pro

This guide covers the experimental MultiACE-derived integration for one to
four Anycubic ACE units on a Snapmaker U1. It supports the original ACE Pro
(also called ACE 1 Pro) and ACE 2 Pro through their different host protocols:

- ACE Pro: direct USB serial and the V1 JSON protocol.
- ACE 2 Pro: USB-to-RS485 serial adapter and the V2 protobuf protocol.

The firmware-side implementation is disabled until it is enabled in Firmware
Config. It has not been hardware-validated in this branch. The procedures
below are the test plan, not a report of successful hardware results.

## Implementation basis and attribution

The runtime is adapted from [decay71/multiACE](https://github.com/decay71/multiACE),
pinned for this draft to commit
[c9c22e391cee89bc7d7894ce4a25876a59565cbc](https://github.com/decay71/multiACE/tree/c9c22e391cee89bc7d7894ce4a25876a59565cbc).
The repository is GPLv3, matching this firmware project. The overlay keeps
MultiACE's protocol split, per-device state, stable device ordering, feed
assist, retry, RFID, and head-source mapping, while using Firmware Config to
activate and restore the Klipper modules.

The following projects and contributors informed the surrounding research and
hardware work:

- [paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware)
  is the extended U1 firmware project this mod targets.
- [printers-for-people/ACEResearch](https://github.com/printers-for-people/ACEResearch)
  provides ACE raw data research by Jookia.
- [utkabobr/DuckACE](https://github.com/utkabobr/DuckACE) provides ACE keep-alive
  and response-handling research by utkabobr.
- [BlackFrogKok/SnapAce](https://github.com/BlackFrogKok/SnapAce) documents the
  feeder bypass locations by BlackFrogKok.
- [hakimio/U1-Ace](https://github.com/hakimio/U1-Ace) and
  [DnG-Crafts/U1-Ace](https://github.com/DnG-Crafts/U1-Ace) are additional
  U1/ACE integration references.

The runtime code in this overlay is based on MultiACE; the other repositories
are references and attribution, not runtime dependencies.

## Build and enablement

The mod is not included in the normal `extended` profile. Build an image with:

~~~bash
./dev.sh make build PROFILE=extended-tareku99
~~~

The pull request workflow also produces a separate `extended-tareku99-build`
artifact. No hardware test is performed by the workflow.

After flashing:

1. Connect the ACE hardware and power it from its own supply.
2. Enable **Advanced Mode** on the printer.
3. Open `http://<printer-ip>/firmware-config/`.
4. Select **Settings > Snapmaker Components**.
5. Set **Anycubic ACE (experimental)** to **Enabled**.

When enabled, Firmware Config:

- verifies that an ACE serial device is visible;
- switches the MultiACE-derived `*_ace.py` modules into the active Klipper
  paths;
- links the ACE include;
- restarts Klipper.

When disabled, it restores the stock U1 modules, removes the ACE include, and
restarts Klipper. This makes the stock U1 path the default and recovery path.

The main configuration template is installed at:

~~~text
/usr/local/share/firmware-config/tweaks/klipper/ace.cfg
~~~

The active include is:

~~~text
/oem/printer_data/config/extended/klipper/ace.cfg
~~~

The persistent MultiACE state is bundled at:

~~~text
/home/lava/printer_data/config/extended/multiace/ace_vars.cfg
~~~

The default configuration expects one ACE:

~~~ini
[ace]
ace_device_count: 1
enable_ace_v2: true
~~~

Set `ace_device_count: 2`, `3`, or `4` before enabling the feature when
using multiple units. Keep the default until the first device-ordering and
tube-routing checks are complete. The V2 adapter allow-list is intentionally
strict by default; add `v2_extra_usb_ids: 1a86:7523` only when a generic
CH340/CH341 adapter is known to be the intended ACE connection.

## ACE signal connector

The following view is from the front/mating side of the ACE signal connector,
with the clip or latch at the top:

![Anycubic ACE signal connector pinout](images/ace-signal-pinout.png)

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

Connect only:

| USB signal | ACE Pro connector |
|---|---|
| USB D- | D- |
| USB D+ | D+ |
| USB GND | GND |
| USB 5V | Leave disconnected |

Typical USB 2.0 colors are white for D-, green for D+, and black for GND, but
verify the actual cable instead of relying on colors.

## ACE 2 Pro wiring

The ACE 2 Pro uses an RS485 adapter. Do not connect it directly to USB data
lines.

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
| A, 485+, or D+ | D+ or D- | Swap with B if commands time out |
| B, 485-, or D- | D- or D+ | Swap with A if commands time out |
| VCC | Leave disconnected | Do not connect 5V |

RS485 labels are not standardized. If the adapter enumerates but the ACE does
not answer, keep GND connected and swap only the A/B pair.

CH343 adapters are preferred because they are closest to the original Anycubic
host path. Common CH340/CH341 adapters may be enabled with the
`v2_extra_usb_ids` setting described above.

## USB detection

Inspect serial devices over SSH:

~~~bash
ls -l /dev/serial/by-id/
~~~

The MultiACE-derived runtime recognizes:

~~~text
ACE Pro:
/dev/serial/by-id/usb-ANYCUBIC*

ACE 2 Pro:
/dev/serial/by-id/usb-1a86_USB_Single_Serial_*
/dev/serial/by-id/usb-1a86_USB_Serial*
~~~

The mod includes a udev permission rule for USB ID `1a86:7523`. Reconnect
the adapter or reboot if the serial node is not accessible.

## Commands

After enabling the feature, the first read-only checks are:

~~~gcode
ACE_LIST
ACE_HEAD_STATUS
A_INFO ACE=0
A_STATUS ACE=0
A_TEMP ACE=0
~~~

Useful management commands include:

- `ACE_SWITCH TARGET=0` — select the active ACE.
- `ACE_LOAD_HEAD HEAD=0` — load a head from the active ACE.
- `ACE_UNLOAD_HEAD HEAD=0` — unload a head back to its mapped ACE.
- `ACE_UNLOAD_ALL_HEADS` — unload all heads with recorded ACE sources.
- `ACE_DRY ACE=0 TEMP=55 DURATION=240` — start drying on one ACE.
- `ACE_STOP_DRYING` — stop drying on the active ACE.
- `ACEG__Status` — convenience macro for head/device status.
- `ACEG__List` — convenience macro for the device list.
- `ACEF__Mode_Normal` and `ACEF__Mode_Multi` — switch between stock and
  MultiACE operation; Klipper requires a restart after a module switch.

The optional MultiACE web service and online updater are not bundled in this
firmware overlay. Fluidd/Mainsail console commands and Firmware Config remain
the supported control path for this draft.

## First hardware test plan

This is intentionally left for a real U1 and ACE setup:

1. Enable the feature with one ACE connected.
2. Run `ACE_LIST`, `ACE_HEAD_STATUS`, `A_INFO ACE=0`, and `A_STATUS ACE=0`.
3. Confirm all four slot states and temperature/RFID responses.
4. Test one loaded slot at a time with `ACE_LOAD_HEAD`, then
   `ACE_UNLOAD_HEAD`.
5. Confirm the U1 runout sensor stops feeding and that a no-filament gate
   produces a recoverable error.
6. Test RFID metadata and manual/no-RFID spool handling.
7. Only then set `ace_device_count` above one and verify stable ACE ordering,
   `ACE_SWITCH`, per-ACE drying, cross-ACE unload, and retry/recovery.
8. Disable the feature and confirm the stock U1 modules are restored.

Do not treat a successful build or static Python check as hardware validation.

## Safety checklist

- Power down before changing connector wiring.
- Verify the six-pin connector orientation before inserting the pigtail.
- Connect GND.
- Leave ACE 5V/VCC disconnected.
- Do not connect ACE 2 Pro directly to USB data lines.
- If ACE 2 Pro enumerates but does not answer, swap only RS485 A and B.
- Test status and temperature before attempting a filament load.
- Keep this pull request in draft until the complete test plan has been run.
