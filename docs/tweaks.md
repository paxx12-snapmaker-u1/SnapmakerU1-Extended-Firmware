---
title: Klipper Tweaks
---

# Klipper Tweaks

Advanced experimental tweaks for Klipper stepper motor driver configuration. These settings can **only** be configured via the [Firmware Configuration](firmware_config.md) web interface under **Settings → Tweaks**.

> **Warning**: These are experimental features that modify low-level stepper driver parameters. Use with caution and monitor your printer carefully after enabling.

## TMC AutoTune

Applies optimized stepper motor driver settings for TMC2240 drivers.

**What it does:**
- Optimizes PWM settings for quieter operation
- Configures StallGuard and CoolStep parameters
- Adjusts timing parameters for better heat management
- Fine-tunes driver parameters for improved performance

**Risks:**
- May cause motors to overheat if cooling is insufficient
- Could result in reduced torque or skipped steps under heavy load
- Incorrect settings may affect print quality
- Changes low-level driver parameters that override defaults

**Recommendation:**
- Monitor motor temperatures during first use
- Test with simple prints before production work
- Revert to disabled if you experience issues

**Configuration:**
This feature can **only** be configured via Firmware Configuration web interface. Manual configuration is not supported.

## TMC Reduced Current

Lowers the stepper motor run current from 1.2A to 1.0A for X and Y axes.

**What it does:**
- Reduces X and Y axis motor current to 1.0A
- Lowers motor heat generation
- Results in quieter motor operation

**Risks:**
- May cause skipped steps under heavy load or fast movements
- Could result in layer shifts on demanding prints
- May reduce positioning accuracy under high acceleration

**Recommendation:**
- Monitor print quality after enabling
- Watch for layer shifts or positioning issues
- Disable if you experience motion problems

**Configuration:**
This feature can **only** be configured via Firmware Configuration web interface. Manual configuration is not supported.

## Max Speed

Raises the XY motion limits and speeds up tool changes. Motion settings only — no
stepper driver registers are changed, so sensorless homing is unaffected.

Both tiers run the printer above the motion limits Snapmaker ships it with — the
`Stock` column below is the rated configuration, and every value to the right of it
is beyond what the machine is specified for. These numbers are community-tested,
not vendor-validated, and the extra speed is paid for in wear.

Values derived from [@JNP-1](https://github.com/JNP-1/Snapmaker-U1-Config).

**Two tiers:**

| | Stock | Balanced | Aggressive |
|---|---|---|---|
| Max XY velocity | 500 mm/s | 600 mm/s | 750 mm/s |
| Max acceleration | 20000 mm/s² | 22000 mm/s² | 25000 mm/s² |
| Tool change speed | 400 mm/s | 550 mm/s | 700 mm/s |
| Slow dock-entry speed | 60 mm/s | 40 mm/s | 60 mm/s |
| Dock grab speed | 10 mm/s | 30 mm/s | 50 mm/s |
| Tool change acceleration | 5000 mm/s² | 12000 mm/s² | 25000 mm/s² |

**Which one:** start with `Balanced` and move to `Aggressive` only once tool changes
are reliable. `Balanced`'s dock-entry speed is deliberately below stock — that move
is short, so a slightly slower dock approach is expected, not a regression.

**Requirements:**
- Dock positions are calibrated correctly
- Belt tension is correct
- Input shaper is calibrated for your printer (run `SHAPER_CALIBRATE`)
- Tool changes are already reliable with the stock configuration

**Risks:**
- Accelerated wear from running the printer above its rated limits
- Crashes into the tool docks if dock positions are off
- Layer shifts and skipped steps under heavy load
- Higher driver and motor temperatures
- Ringing and other quality artifacts if input shaper is not calibrated

**Recommendation:**
- Calibrate the printer fully before enabling
- Start with `Balanced` and a small test print, and watch the first tool changes
- Disable if you see dock alignment problems or layer shifts

**Note:** This tweak cannot be combined with
[TMC Reduced Current](#tmc-reduced-current) — running the X/Y motors at 1.0A with
these raised speed limits is untested. Firmware Config refuses that combination
and tells you which tweak to disable first. [TMC AutoTune](#tmc-autotune) can be
used alongside Max Speed; the two no longer change any setting in common.

**Configuration:**
This feature can **only** be configured via Firmware Configuration web interface. Manual configuration is not supported.

## Object Processing for Adaptive Mesh

Enables object processing in Moonraker's file manager to support adaptive mesh features.

**What it does:**
- Processes gcode files to extract object information
- Generates boundaries for adaptive mesh leveling
- Allows per-object print settings and controls

**Risks:**
- Can cause very long processing times for large gcode files (> 100MB)
- May result in extended delays when uploading files
- Snapmaker Orca may stay at 100% for a long time when sending prints
- High memory usage during file processing
- Can cause delays before prints can start

**Important:**
- Enabling this setting alone is not enough to use adaptive mesh
- You must also update your slicer start gcode to use: `BED_MESH_CALIBRATE ADAPTIVE=1`
- This tells Klipper to only mesh the area where objects will be printed

**Recommendation:**
- **Preferred approach:** Enable `Exclude Object` in your slicer settings instead of this option
- Slicer-generated object labels are more reliable and don't require server-side processing
- Only enable Moonraker object processing if your slicer doesn't support exclude object
- Disable if you frequently print large gcode files
- Monitor file upload times after enabling
- Consider splitting large models into smaller prints if processing is too slow

**Configuration:**
This feature can **only** be configured via Firmware Configuration web interface. Manual configuration is not supported.

## Fast Nozzle Cooldown

Speeds up pre-print nozzle cooling using the left side blower (`cavity_fan` at `X5 Y140`) and eliminates both the redundant `220°C -> 130°C -> 170°C -> 130°C` cool-reheat-cool cycle and the post-flow-calibration `170°C -> 220°C -> 170°C` pre-extrude reheat during `PRINT_START`.

**What it does (using PLA at `220°C` as an example):**
- **Step 0 — Post-Flow-Calibration Hold at `170°C` (when Flow Calibration runs on the initial toolhead)**: At the end of flow calibration, the active nozzle has just extruded `~640 mm` of fresh filament at `220°C`, cooled to `170°C` at the side blower, and wiped clean. Instead of letting the start sequence heat that same nozzle right back up from `170°C` to `220°C` to extrude another `20 mm`, cool it back down to `170°C`, and wipe it a second time, it holds the nozzle steady at `170°C` and proceeds directly into rough Z-homing (saving an extra `~57s` and `20 mm` of filament)
- **Step 1 — Quick Initial Drop (`220°C -> 188°C`, when Flow Calibration was skipped or disabled)**: Right after the `220°C` pre-extrude purge, sets the heater target to `170°C` and moves the toolhead to the left side blower (`X5 Y140`) with fans at `100%` until the nozzle reaches `188°C`. Instead of standing still waiting all the way down to `130°C`, it immediately starts the rough wipe, rough Z-home, and build-plate check while the nozzle continues cooling down to `170°C` (holding a `170°C` floor if the bed starts at the bottom of the printer)
- **Step 2 — Scrub on the Way Down (`170°C -> 145°C`)**: When the start sequence asks for `170°C` to scrub the nozzle, the nozzle is already at `170°C`, so the reheat wait is skipped (`0s`). It sets the heater target directly to `130°C` and runs both nozzle-scrubbing stages while the fans continue cooling the nozzle down toward `~145°C`
- **Step 3 — Final Settle (`~145°C -> 130°C`)**: Moves back to the left side blower at `100%` fan speed for the remaining `~15°C` drop toward `130°C`, then stops both fans `1.5°C` before the target (`131.5°C`) so the temperature glides smoothly into `130.0°C` during the return move without undershooting

**Configuration:**
This feature can be toggled via the Firmware Configuration web interface under **Settings → Print Preferences → Fast Nozzle Cooldown**.

## How to Configure

1. Open the printer's web interface (Fluidd or Mainsail)
2. Navigate to **Firmware Config** in the menu
3. Go to **Settings → Tweaks**
4. Select the desired option for each tweak
5. Confirm the warning dialog
6. Klipper will automatically restart to apply changes

Changes take effect immediately after Klipper restarts (no reboot required).

## Technical Details

These tweaks work by adding or removing configuration files from `/oem/printer_data/config/extended/`:
- `klipper/tmc_autotune.cfg` - TMC AutoTune parameters
- `klipper/tmc_current.cfg` - Reduced current settings
- `klipper/10_max_speed.cfg` - Max Speed motion limits and tool change speeds (either tier installs to this one file)
- `klipper/fast_nozzle_cooldown.cfg` - Fast Nozzle Cooldown macro (`M109` wrapper)
- `moonraker/object_processing.cfg` - Moonraker object processing settings

These files are automatically included by the main printer configuration if present. Manual editing of these files is not recommended as they will be overwritten by the Firmware Configuration interface.
