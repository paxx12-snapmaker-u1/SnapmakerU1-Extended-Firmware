---
title: Panda Breath Chamber Heater
---

# Panda Breath Chamber Heater

Integrates the [BIQU Panda Breath](https://biqu.equipment/products/biqu-panda-breath-smart-air-filtration-and-heating-system-with-precise-temperature-regulation) smart chamber heater and air filter with Klipper on the Snapmaker U1.

The Panda Breath is a 300 W PTC chamber heater with HEPA/carbon air filtration and WiFi control. This firmware reverse-engineers its WebSocket API and exposes the device as a standard Klipper `heater_generic`, so slicer chamber temperature commands (`M141`/`M191`) work out of the box.

## Risks and Warranty

### Warranty

Installing and operating the Panda Breath significantly raises sustained operating temperatures inside the enclosure. This accelerates wear on electronics, motors, and other components beyond their rated conditions. Any damage attributable to elevated thermal stress is unlikely to be covered under warranty. **Use at your own risk.**

### Motherboard Overheating

The U1 motherboard has insufficient thermal headroom for sustained elevated chamber temperatures. There are documented cases of motherboard overheating causing mid-print failures. The RK3562 main processor begins thermal throttling at 85 °C, degrading Klipper real-time performance and causing motion or communication errors.

**Additional active cooling on the motherboard is required before using Panda Breath.**

Printable cooling solutions available on MakerWorld:

- [Snapmaker U1 MCU Mainboard Cooler Fan Holder](https://makerworld.com/pl/models/2396929-snapmaker-u1-mcu-mainboard-cooler-fanholder) — covers the full motherboard including the RK3562 main processor (**recommended**).
- [Cooling of drivers on Snapmaker U1 (6015)](https://makerworld.com/pl/models/2464667-cooling-of-drivers-on-snapmaker-u1-6015) — cools the stepper drivers only; does **not** cover the RK3562 main processor and is not sufficient on its own.

See also: [Quick overview on fan mods applied for Snapmaker U1](https://www.reddit.com/r/SnapmakerU1/comments/1tlk27r/quick_overview_on_fan_mods_applied_for_snapmaker/) — community thread covering additional fan mod approaches (to be evaluated).

## Prerequisites

1. **Install motherboard cooling** — see [Risks and Warranty](#risks-and-warranty) above.
2. Panda Breath device running firmware **v1.0.3 or v1.0.4**. (v1.0.4 only adds an
   optional Home Assistant MQTT interface; the WebSocket control path this integration
   uses is unchanged, so both versions work identically here.)
3. **Static DHCP leases** for both the printer and the Panda Breath device on your router. The printer IP is embedded into the Panda Breath device during setup and must not change on reboot. Klipper connects to the Panda Breath by IP address on every print.
4. Power-cycle the Panda Breath and wait at least 5 seconds before enabling.

## Enabling

Enable via the Firmware Config web interface at `http://<printer-ip>/firmware-config/` under **Tweaks > Panda Breath Chamber Heater**.

Three modes are available:

| Mode | Description |
|------|-------------|
| **Auto** (recommended) | Klipper heats the chamber to target, then hands off hold and cool-down to Panda native auto mode. Uses the WebSocket control path. Requires firmware v1.0.3 or v1.0.4. |
| **MQTT** (v1.0.4+) | The Panda talks to the printer's own MQTT broker on a dedicated, password-protected listener; Klipper controls the chamber heater directly via `heater_generic`. Requires firmware **v1.0.4 or newer**. |
| **Manual** (advanced) | Pure `heater_generic` control over the WebSocket path throughout. Legacy fallback; less safe if the device or network is lost during a print. |

During setup the web interface will ask for the Panda Breath IP address and will automatically bind the device to the printer.

In **MQTT** mode the setup also provisions a locked-down mosquitto listener (port 1885, ACL-scoped to `panda_breath/#` and `homeassistant/#`) with a generated password, and binds the Panda to that broker automatically over its WebSocket API — there are no manual web-UI steps on the Panda.

## Home Assistant

The stock v1.0.4+ firmware ships a full Home Assistant MQTT integration: on connect it publishes autodiscovery configs under `homeassistant/#` for the chamber temperature, target/filter/heater/drying temperatures, mode and drying-mode selects, and `work_on`/`drying_running` switches. Because MQTT mode uses the printer's own broker (and the ACL permits `homeassistant/#`), **any Home Assistant instance pointed at that broker discovers the Panda automatically** — no extra configuration on the device. Klipper and Home Assistant can watch the same broker at once.

Note that Klipper remains the controller: the safety reconciliation (below) will force the chamber **off** if it is switched on from Home Assistant or the device button while Klipper is not commanding heat. So Home Assistant is ideal for **monitoring**; use `M141` (or a Klipper macro) to actually drive the chamber.

## Safety and failure modes

The Panda Breath is an autonomous device: it holds its own heating state and will keep heating on its own. The integration enforces one invariant — **the chamber only heats while Klipper is commanding it** — through several layers:

- **Reconciliation:** Klipper continuously checks the device's reported state. If the device is heating while Klipper is *not* commanding heat — because it auto-resumed from NVS after a power cycle, was switched on from Home Assistant or the physical button, or missed an off command — Klipper forces it off within about a second.
- **Idle timeout:** the printer's `[idle_timeout]` runs `TURN_OFF_HEATERS`, which turns the chamber off after the configured idle period (300 s by default) even if it was simply left on.
- **Reboot:** the broker listener is stored in persistent `printer_data` and re-applied on every boot, so the link comes back after a power cycle. The Panda auto-resumes heating from NVS, but Klipper reclaims control and turns it off on boot (a print that resumes will re-command it deliberately).

**Inherent limitation (MQTT/WiFi):** if the Panda's WiFi or the broker link fails *while the chamber is heating*, Klipper cannot reach the device to turn it off. The Panda will keep heating to its last commanded target until connectivity returns (at which point the reconciliation/idle-timeout shut it off), bounded only by the device's own hardware limits. This is inherent to a network-attached accessory. Where a lost link must *guarantee* heater shutdown, a directly-wired MCU is the safer choice — Klipper's MCU watchdog and `verify_heater` shut the heater down on a dead link, which a WiFi accessory cannot.

## Configuration File

After enabling, a config file is placed at:

```
/home/lava/printer_data/config/extended/klipper/panda_breath.cfg
```

To change the IP address or port, edit that file directly in Fluidd/Mainsail or via SSH:

```ini
[panda_breath]
host: 192.168.1.100
port: 80
```

Restart Klipper after saving.

## Usage

Once enabled, the Panda Breath appears as a chamber heater in Fluidd/Mainsail. Use standard G-code commands to control it:

| Command | Effect |
|---------|--------|
| `M141 S45` | Set chamber target to 45 °C |
| `M191 S45` | Set chamber target to 45 °C and wait until reached |
| `M141 S0` | Turn off chamber heating |

In your slicer, set the chamber temperature for the filament profile as usual — `M141`/`M191` commands in start G-code are handled automatically.

### Native Commands

Additional commands are available for direct device control:

| Command | Parameters | Description |
|---------|-----------|-------------|
| `PANDA_BREATH_AUTO` | `ENABLE=1/0 TARGET=<°C>` | Enable/disable Panda native auto mode |
| `PANDA_BREATH_DRY_RUN` | `TARGET=<°C> DURATION=<min>` | Start native filament drying cycle |
| `PANDA_BREATH_DRY_STOP` | — | Stop active drying cycle |

## Disabling

Disable via **Firmware Config > Tweaks > Panda Breath Chamber Heater > Disabled**. The device is automatically unbound from the printer and the configuration file is removed.

## Troubleshooting

**Klipper shows heater error / verify_heater failure**

The Panda Breath is a slow external heater with coarse 1 °C temperature reporting. The default `verify_heater` configuration uses extended gain check and error windows to avoid false positives. If errors still occur, check WiFi connectivity between the printer and the Panda Breath device.

**Device not reachable at `PandaBreath.local`**

mDNS resolution can be unreliable. Set a static DHCP lease and use the IP address directly in `panda_breath.cfg`.

**Print fails or printer reboots during long high-temperature prints**

This is a symptom of motherboard overheating. See [Motherboard Overheating](#motherboard-overheating) for cooling solutions.

## Related Documentation

- [Firmware Configuration](firmware_config.md) - Enable Panda Breath via the web interface under Snapmaker Components
- [Klipper and Moonraker Custom Includes](klipper_includes.md) - Further customise the generated `panda_breath.cfg`
