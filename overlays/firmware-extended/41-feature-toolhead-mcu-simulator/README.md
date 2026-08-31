# Toolhead MCU Simulator

This overlay adds a "Bypass MCU (Disconnected)" mode alongside the existing
[Faulty Toolhead Bypass](../34-feature-faulty-toolhead/README.md), for the
case that bypass doesn't cover: a toolhead's MCU is dead or its USB cable is
disconnected entirely, not just a bad thermistor. Stock Klipper treats any
`[mcu]` connect failure as fatal for the whole printer, so today one dead
toolhead board takes down all four.

## How it works

- `klipper-dummy-at32f403a` is a new Klipper MCU board target,
  `dummy_at32f403a`, built from
  [snapmaker/u1-klipper](https://github.com/snapmaker/u1-klipper) (the
  source that already builds the real toolhead firmware) with a patch
  (`klipper-patches/01_add_board.patch`) that adds a self-contained
  `src/dummy_at32f403a/` board directory. It runs as a normal Linux process
  on the printer's SBC (like Klipper's own `src/linux` target) instead of
  real embedded firmware, and declares the exact same pin and I2C bus names
  as the real toolhead MCU (`PA0`-`PI15`, `i2c1`, `i2c1a`, ...), so a
  printer.cfg override needs no pin renaming.
- Every GPIO/ADC/I2C access is tracked in memory only - nothing touches
  real hardware. Return values for digital/analog reads and I2C register
  reads are configurable via a plain text file
  (`root/usr/local/share/toolhead-sim/pins.conf`), read at process startup,
  editable on-device without a firmware rebuild.
- `S59toolhead-sim` starts one `klipper-dummy-at32f403a` process per
  toolhead whose MCU is marked dead, before `S60klipper` starts.
- `faulty_toolheadN_mcu.cfg` repoints that toolhead's `[mcu eN] serial:` at
  the simulator's pty instead of the (missing) real USB serial device, plus
  the same heater-safety clamps as the existing thermistor bypass
  (`max_temp: 999`, `max_power: 0.000001`, fan forced off) as defense in
  depth.
- Every toolhead's printer.cfg also has an `[inductance_coil extruderN]`
  (Z-offset probing) and a `[power_loss_check eN]` section, so klippy
  validates a handful of MCU commands for those against the connecting
  MCU's dictionary regardless of which MCU backs the toolhead.
  `src/dummy_at32f403a/inductance_coil.c` and `power_loss_check.c` are
  from-scratch no-op implementations of just those commands/responses (not
  reuses of Snapmaker's `src/stm32/*` files) so the dummy MCU identifies
  cleanly instead of Klipper shutting it down with `Unknown command: ...`.
- Toolhead 1's printer.cfg also has a
  `[sensor_accelerometer_identify e0_accelerometer]` that probes `spi1` for
  a `lis2dw`/`sc7a20` chip at klippy connect time, so `HAVE_GPIO_SPI` is
  selected and `src/dummy_at32f403a/spi.c` declares the same `spi_bus`
  names as the real toolhead firmware. Every transfer reads back zero
  bytes - the identify probe just fails to match a known chip and klippy
  logs `No sensor identified`, which is not fatal.

### Shipped `pins.conf` defaults

The default `root/usr/local/share/toolhead-sim/pins.conf` sets three pins
so a bypassed toolhead reads a plausible, non-conflicting idle state
instead of whatever an all-zero fallback would decode to:

- `PA2` (the real `sensor_pin` for every extruder) - raw `4037`, which
  decodes to `~0C` on the `NTC_100K_3950_PRECISE` curve used by all four
  extruders (`pullup_resistor` 4700, `ADC_MAX` 4095) - just above each
  extruder's `min_temp: 0` so it reads as a plausible cold sensor without
  tripping a MINTEMP fault.
- `PB1`/`PA3` (`active_pin`/`grab_valid_pin` on `[park_detector extruderN]`
  in printer.cfg) - raw `4090` each. These are analog "buttons": a pin
  reads "active"/"valid" whenever its pullup-derived resistance falls
  inside a configured range (`active_analog_range 0-47000`,
  `grab_valid_analog_range 0-390`), and the unconfigured `adc.c` default
  (`1024`) falls inside the wide `active_pin` range - which conflicts with
  the real `park_pin` switch (on the mainboard, outside this simulator)
  reading "parked", and trips extruder.py's "conflicting status ... both
  parked and picked states" (`TTF`/`TTT`) check. Raw `4090` pushes the
  derived resistance to ~3.8M ohms, outside both ranges, so the toolhead
  reads a clean "parked, not active, not grabbed" state instead.

## Warning

Unlike the thermistor-only bypass, this mode makes the toolhead's stepper
motor and heater fully inert - it cannot move, extrude, or heat at all
while this bypass is enabled, not just "don't heat with it." Only use it to
let the printer boot and keep using the other toolheads until the faulty
part is repaired or reconnected.

Toolhead mapping:

- Toolhead 1 -> `[mcu e0]`, `[extruder]`, `[heater_fan e0_nozzle_fan]`
- Toolhead 2 -> `[mcu e1]`, `[extruder1]`, `[heater_fan e1_nozzle_fan]`
- Toolhead 3 -> `[mcu e2]`, `[extruder2]`, `[heater_fan e2_nozzle_fan]`
- Toolhead 4 -> `[mcu e3]`, `[extruder3]`, `[heater_fan e3_nozzle_fan]`
