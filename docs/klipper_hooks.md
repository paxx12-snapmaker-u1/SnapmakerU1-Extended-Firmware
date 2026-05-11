---
title: Klipper Print Hooks
---

# Klipper Print Hooks

The extended firmware patches `fluidd.cfg` to add a hook system for `PRINT_START`, `PRINT_END`, and `CANCEL_PRINT`. Features and custom macros can react to print lifecycle events without modifying any stock macro.

## How It Works

At the end of `PRINT_START`, and at the start of `PRINT_END` and `CANCEL_PRINT`, the macro iterates `printer.configfile.config` and calls every `gcode_macro` whose name matches the relevant prefix:

| Event | Hook prefix | Called |
|---|---|---|
| `PRINT_START` | `_PRINT_START_` | **after** the original |
| `PRINT_END` | `_PRINT_END_` | **before** the original |
| `CANCEL_PRINT` | `_CANCEL_PRINT_` | **before** the original |

`PRINT_END` and `CANCEL_PRINT` hooks run first so features can do cleanup (e.g. update spool state) before the printer parks and turns off heaters.

## Registering a Hook

Create a `gcode_macro` with the matching prefix inside any `.cfg` file loaded by Klipper. Placing it in `extended/klipper/` is the recommended location.

```cfg
[gcode_macro _PRINT_START_MY_FEATURE]
gcode:
    RESPOND TYPE=echo MSG="my feature: print started"

[gcode_macro _PRINT_END_MY_FEATURE]
gcode:
    RESPOND TYPE=echo MSG="my feature: print ended"

[gcode_macro _CANCEL_PRINT_MY_FEATURE]
gcode:
    RESPOND TYPE=echo MSG="my feature: print cancelled"
```

No existing macro needs to be modified. Hooks are discovered at runtime from the live config.

## Hook Ordering

Hooks are called in the order Klipper processed the config sections, which follows the alphabetical file order within each included directory. If ordering between hooks matters, use a numeric prefix in the macro name (e.g. `_PRINT_END_10_SPOOLMAN`, `_PRINT_END_20_NOTIFY`).

## Passing Parameters

Hooks receive no parameters. If a hook needs slicer-supplied values (e.g. bed temperature), read them from `printer.configfile.settings` or via a shared `gcode_macro` variable.

## Example: Cavity LED

Turn the cavity LED on when a print starts and off when it ends or is cancelled.

```cfg
[gcode_macro _PRINT_START_CAVITY_LED]
gcode:
    SET_LED LED=cavity_led WHITE=1.0

[gcode_macro _PRINT_END_CAVITY_LED]
gcode:
    SET_LED LED=cavity_led WHITE=0

[gcode_macro _CANCEL_PRINT_CAVITY_LED]
gcode:
    SET_LED LED=cavity_led WHITE=0
```
