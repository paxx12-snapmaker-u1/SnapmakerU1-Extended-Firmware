---
title: Faulty Toolhead Bypass
---

# Faulty Toolhead Bypass

This firmware adds two Firmware Config recovery options for Snapmaker U1
printers with a damaged toolhead:

- **Bypass Thermistor**: for error `0003-0523-0000-0002`, when a toolhead's
  MCU is alive and talking over USB but its thermistor is faulty. Based on
  Snapmaker's official guide:
  https://wiki.snapmaker.com/en/snapmaker_u1/troubleshooting/bypass_a_faulty_toolhead
- **Bypass MCU (Disconnected)**: for when a toolhead's MCU itself is dead or
  its USB cable is disconnected entirely, so Klipper can't talk to it at
  all. Stock Klipper treats this as fatal for the whole printer - without
  this bypass, one dead toolhead board takes down all four. It repoints
  that toolhead's `[mcu]` at a software simulator
  (see [Toolhead MCU Simulator](../overlays/firmware-extended/41-feature-toolhead-mcu-simulator/README.md))
  instead of the real board, so Klipper can still boot.

## Warning

Both bypasses are only intended to let the printer boot and keep using the
remaining toolheads until the damaged part is replaced or reconnected.

- **Bypass Thermistor**: do not heat or print with the faulty toolhead while
  the bypass is enabled.
- **Bypass MCU (Disconnected)**: the toolhead's stepper motor and heater are
  fully inert while this bypass is enabled - it cannot move, extrude, or
  heat at all, not just "don't heat with it."
- Disable the bypass again after the repair is complete.

## Configure

1. Enable Advanced Mode on the printer screen.
2. Open `http://<printer-ip>/firmware-config/`.
3. Go to **Settings -> Troubleshooting**.
4. Use the row for each affected toolhead:
   `Faulty Toolhead 1`, `Faulty Toolhead 2`, `Faulty Toolhead 3`, or
   `Faulty Toolhead 4`.
5. Change that row from **Normal** to **Bypass Thermistor** (MCU alive,
   thermistor bad) or **Bypass MCU (Disconnected)** (MCU dead or cable
   unplugged).
6. Firmware Config will restart the toolhead simulator (if applicable) and
   Klipper automatically.

To undo it after replacing the faulty part, set each affected toolhead row back
to **Normal**.

## What Bypass Thermistor Changes

For the selected toolhead, the override:

- remaps the extruder temperature `sensor_pin` to `PC5`
- raises the extruder `max_temp` to `999`
- sets the extruder `max_power` to `0.000001` because Klipper rejects `0`
- sets the matching nozzle cooling fan `heater_temp` to `999`
- sets the matching nozzle cooling fan `fan_speed` to `0`
- changes the last `stepped_temp_table` entry from `260, 0.9` to `260, 0`

Toolhead mapping:

- Toolhead 1 -> `[extruder]` and `[heater_fan e0_nozzle_fan]`
- Toolhead 2 -> `[extruder1]` and `[heater_fan e1_nozzle_fan]`
- Toolhead 3 -> `[extruder2]` and `[heater_fan e2_nozzle_fan]`
- Toolhead 4 -> `[extruder3]` and `[heater_fan e3_nozzle_fan]`

## What Bypass MCU (Disconnected) Changes

For the selected toolhead, the override:

- repoints `[mcu eN]`'s `serial` at a dummy MCU simulator process instead
  of the (missing) physical USB serial device
- raises the extruder `max_temp` to `999`
- sets the extruder `max_power` to `0.000001` because Klipper rejects `0`
- sets the matching nozzle cooling fan `heater_temp` to `999`
- sets the matching nozzle cooling fan `fan_speed` to `0`
- changes the last `stepped_temp_table` entry from `260, 0.9` to `260, 0`

Toolhead mapping:

- Toolhead 1 -> `[mcu e0]`, `[extruder]`, `[heater_fan e0_nozzle_fan]`
- Toolhead 2 -> `[mcu e1]`, `[extruder1]`, `[heater_fan e1_nozzle_fan]`
- Toolhead 3 -> `[mcu e2]`, `[extruder2]`, `[heater_fan e2_nozzle_fan]`
- Toolhead 4 -> `[mcu e3]`, `[extruder3]`, `[heater_fan e3_nozzle_fan]`

See [Toolhead MCU Simulator](../overlays/firmware-extended/41-feature-toolhead-mcu-simulator/README.md)
for how the simulator itself works.

## Implementation Note

Inference from the official guide and the stock U1 `printer.cfg`:

- The official guide says to change the temperature sensor pin suffix to `PC5`.
- Stock U1 configs use per-toolhead MCU pin names (`e0:PA2`, `e1:PA2`,
  `e2:PA2`, `e3:PA2`).
- This firmware therefore implements the remap as `e0:PC5`, `e1:PC5`,
  `e2:PC5`, or `e3:PC5` for the selected toolhead.

The active override is installed into:

```text
/oem/printer_data/config/extended/klipper/
```

Filename pattern:

- `faulty_toolhead1.cfg` / `faulty_toolhead1_mcu.cfg`
- `faulty_toolhead2.cfg` / `faulty_toolhead2_mcu.cfg`
- `faulty_toolhead3.cfg` / `faulty_toolhead3_mcu.cfg`
- `faulty_toolhead4.cfg` / `faulty_toolhead4_mcu.cfg`

Only one override is active per toolhead at a time - switching between
**Bypass Thermistor** and **Bypass MCU (Disconnected)** removes the other
override's marker file. Different toolheads can have different bypasses
enabled at the same time.
