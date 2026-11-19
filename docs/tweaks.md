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

## Smart Flow Calibration

When **Flow Calibration** is checked at print start, the stock firmware calibrates every toolhead used in the print (`T0` through `T3`). Each lane takes **~3.5 minutes** and purges **up to ~640 mm (~2g) of filament** across 4 test passes (`~159 mm` per pass) into the waste chute. On a 4-color print where you only swapped a single spool on Lane 4, checking **Flow Calibration** wastes **~10.5 minutes and up to ~1.9 meters (~6g) of filament** re-calibrating Lanes 1, 2, and 3 to the exact same values they already had.

**What it does (step by step):**
- **Remembers which lanes are already calibrated**: Once a lane finishes Flow Calibration, the printer saves a record that this lane's currently loaded spool and nozzle have been calibrated.
- **Watches for any spool or filament change**: A lane is automatically marked as needing calibration again whenever any of the following happen:
  - You unload filament from the hotend or load a new filament into the hotend
  - The feeder entrance sensor or toolhead filament sensor goes clear (spool removed or ran out)
  - An RFID spool tag is scanned, or you edit the filament brand, type, subtype, color, or nozzle size in the UI
- **Preserves existing calibration if Flow Calibration is unchecked**: Marking a lane as needing calibration only updates the smart-tracking flag — it never wipes the previously calibrated value in `flow_calibrator.json` unless OEM firmware itself resets it on an RFID/UI filament type change.
- **Skips unchanged lanes at print start**: When you start a print with **Flow Calibration** enabled, any lane whose filament has stayed loaded and untouched since its last calibration is skipped in `0 seconds` (saving `~3.5 minutes` and up to `~640 mm / ~2g` of purged filament per unchanged lane, without even preheating that nozzle to `140°C` while waiting), reusing its saved calibration value while only calibrating the lane(s) you actually changed.
- **Manual override macros in Fluidd / Mainsail**:
  - `SMART_FLOW_RESET`: Marks all lanes (or a single lane with `SMART_FLOW_RESET EXTRUDER=0..3`) as needing calibration on the next print without having to unload the filament.
  - `SMART_FLOW_MARK_VALID`: Marks all currently loaded lanes (or `EXTRUDER=0..3`) as already calibrated if you enable this setting when your spools are already calibrated.

**Configuration:**
This feature can be toggled via the Firmware Configuration web interface under **Settings → Print Preferences → Smart Flow Calibration (Changed Lanes Only)**.

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
- `klipper/smart_flow_calibration.cfg` - Smart Flow Calibration (Changed Lanes Only) macros and state tracking
- `moonraker/object_processing.cfg` - Moonraker object processing settings

These files are automatically included by the main printer configuration if present. Manual editing of these files is not recommended as they will be overwritten by the Firmware Configuration interface.
