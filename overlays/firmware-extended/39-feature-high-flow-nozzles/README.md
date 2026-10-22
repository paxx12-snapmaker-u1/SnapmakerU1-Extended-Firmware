# High-flow nozzle sizes

Adds 0.2, 0.6 and 0.8 mm to the stock touchscreen's **High Flow** nozzle selector.
Standard Flow retains 0.2, 0.4, 0.6 and 0.8 mm. Select the flow type first,
then the diameter and confirm the configuration. Switching to High Flow
initially selects 0.4 mm, as in the stock UI.

The closed-source LVGL GUI already creates and handles all four diameter
buttons. This overlay changes one AArch64 conditional branch to an
unconditional branch in its visibility updater, always taking the path
that shows all four diameters in their normal columns. The patch verifies the complete
original GUI SHA-256 (allowing its own changes on a rerun) before writing.
It supports the GUI bundled with firmware **2.0.0.205**; an unknown GUI
stops the build and requires reviewing the offsets and instructions.

## Settings and downstream behavior

The existing GUI save callback sends `printer.control.nozzle_properties`
with the selected toolhead, `diameter` and `volume_type: "high_flow"`.
Klipper already accepts all three new combinations, persists them in each
extruder's nozzle configuration and reports them back to the display.
No backend remapping or new API is needed.

The existing diameter and volume-type fields feed print compatibility
checks, filament loading/unloading and pressure-advance calibration.
High-flow nozzles use the high-flow unloading path and default to the
high-flow calibration algorithm. The optional Pechex auto-PA overlay
already keys values by both fields (`0.2_high_flow`, `0.6_high_flow`,
`0.8_high_flow`).

Stock firmware supplies a dedicated high-flow filament parameter table
only for 0.4 mm. For 0.2/0.6/0.8 mm it uses the respective standard-diameter
table as a conservative default, including standalone calibration
parameters. This overlay preserves that fallback; it does not invent
flow-rate, temperature or pressure-advance values. Print-time calibration
can use slicer-provided parameters, and nozzle-specific PA values can be
calibrated. Select matching diameter/flow settings in the slicer as well.

## Validation

Run against an extracted firmware rootfs (stock or patched by this version):

```sh
python3 -m venv /tmp/high-flow-tests
/tmp/high-flow-tests/bin/pip install unicorn==2.1.4
/tmp/high-flow-tests/bin/python overlays/firmware-extended/39-feature-high-flow-nozzles/test/test_nozzles.py tmp/firmware/rootfs
```

The tests emulate the actual GUI instructions with LVGL calls stubbed,
exercise selections and the save callback, and check the extracted Klipper
API's persistence and diameter-specific defaults. They do not replace a
physical touchscreen check or a full firmware build.
