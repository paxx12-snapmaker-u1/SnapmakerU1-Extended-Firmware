---
title: Add-on Extension Connector (CAN)
---

# Add-on Extension Connector (CAN)

The U1 has a 4-pin connector on the back labeled `GND`, `VCC`, `CANH` and
`CANL`. It is a working CAN bus port and can be used for your own devices,
including extra Klipper MCUs.

```
 GND  | VCC
------+------
 CANH | CANL
```

## How it works

There is no separate CAN chip. The main MCU (AT32F403A, reported by Klipper as
`stm32f103xe`) runs Snapmaker's Klipper build with a USB to CAN bridge. It
shows up in Linux as a gs_usb adapter:

```
$ lsusb
... ID 1d50:606f OpenMoko, Inc. Geschwister Schneider CAN adapter
```

and creates the `can0` interface.

Klipper talks to the main MCU over UART (`/dev/ttyS6`), not over CAN, so the
MCU itself is not a node on the bus. `canbus_query.py` will return nothing
until you connect another device.

## Bus settings

| Setting      | Value                                   |
|--------------|-----------------------------------------|
| Bitrate      | 500 kbit/s (fixed)                      |
| Sample point | 87.5%                                   |
| Frames       | Classic CAN, 11-bit and 29-bit IDs, no CAN FD |
| Termination  | Already on the printer side (~60 Ω between CANH and CANL) |

The bitrate is compiled into the main MCU firmware. Any bitrate you set with
`ip link` is accepted but ignored by the bridge, so every device on the bus
has to run at **500 kbit/s**. A mismatch (for example flashing a toolhead for
1 Mbit/s) looks like a dead bus: nothing ACKs and nothing is received.

The bus is already terminated on the printer side, so don't enable the
120 Ω terminator on your device for short cable runs.

## Bringing up can0

```bash
ip link set can0 down
ip link set can0 type can bitrate 500000
ip link set can0 txqueuelen 128
ip link set can0 up
```

The bitrate still has to be set or Linux won't bring the interface up. It just
has no effect on the actual bus speed.

Quick test with a USB-CAN adapter on a PC (terminator on the adapter off):

```bash
# PC
ip link set can0 type can bitrate 500000 sample-point 0.875
ip link set can0 up
candump -tz can0

# printer
cansend can0 123#DEADBEEF
```

If you send frames while nothing is connected, they stay queued (no ACK) and
all come out at once when a device is plugged in. Reset the queue with
`ip link set can0 down && ip link set can0 up`.

## Powering devices from VCC

`VCC` is off by default. It is switched by a SoC GPIO (`GPIO0_A5`, named
`power-ams` in the device tree), not by Klipper:

```bash
gpioset gpiochip0 5=1   # VCC on
gpioset gpiochip0 5=0   # VCC off
```

This is the same thing the stock touchscreen UI does when it powers up its own
CAN module, so the UI can switch it off again.

Don't use `gpioget` to check the state, it reconfigures the line as input and
may turn VCC off. Use `cat /sys/kernel/debug/gpio` instead.

When enabled, `VCC` is 24 V. According to Snapmaker support, drawing around
0.5 A from it can already cause stability problems, so keep the load well
below that. Anything heavier should have its own power supply (with a common
GND).

## Klipper MCUs on the connector

Flash the MCU with [U1-Klipper](https://github.com/Snapmaker/u1-klipper) set to
CAN bus at **500000**, the same for Katapult if you use it. Then:

```bash
python3 /home/lava/klipper/scripts/canbus_query.py can0
```

```ini
[mcu extra_mcu]
canbus_uuid: <uuid>
canbus_interface: can0
```

Things to keep in mind:

- The bridge lives in the main MCU, so `FIRMWARE_RESTART` or a main MCU error
  also drops everything on the CAN bus.
- For your own non-Klipper devices, avoid IDs `0x3F0`/`0x3F1` and
  `0x100`-`0x17F`, which Klipper uses.
- Bus errors are not reported back to Linux by the bridge, so
  `ip -s link show can0` won't show missing ACKs. Debug from the other end.
