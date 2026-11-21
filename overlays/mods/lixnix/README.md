# LixNix

Personal mod by [@LixNix](https://github.com/LixNix). The analysis, the
Klipper and Moonraker patches, and the `gui` instruction changes are all
LixNix's work, taken from their U1 2.0.0.205 nozzle-map fix and only rebased
onto `firmware-extended` here. Build with:

```bash
./dev.sh make build PROFILE=extended-lixnix
```

## 01-multi-nozzle

Fixes printing with different nozzle diameters per toolhead (e.g.
0.2 / 0.4 / 0.6 / 0.8) on stock firmware `2.0.0.205`. See #773.

Stock fails in two ways, depending on the slicer:

| File from | What `gui` sends | What Klipper does | Result |
|---|---|---|---|
| Snapmaker Orca | every filament matched against `nozzle_diameter_list[0]` | accepts | prints from the wrong toolhead |
| Other slicers | correct map table | compares `nozzle_diameter[0]` against every used extruder | refuses: `f2_0.2 != e2_0.6` |

Both halves have to be fixed together:

- `scripts/01-patch-gui-nozzle-map.sh`: patches two instructions in `/usr/bin/gui`
  so the per-filament nozzle diameter is always used. It checks the SHA256 of
  the stock binary and of the result, and fails the build on any other firmware.
- `klipper/01-print-task-config-nozzle-map.patch`: validates each used filament
  against the extruder it is mapped to through `extruder_map_table`.
- `klipper/02-flow-calibrator-line-width-percent.patch`,
  `moonraker/01-metadata-anchor-and-line-width-percent.patch`,
  `moonraker/02-klippy-apis-line-width-percent.patch`: Orca's percentage line
  widths (`line_width = 105%`) were read as `105.0` mm and discarded by flow
  calibration. Also anchors the Moonraker footer-key regexes.

Not compatible with the `afc` mod, which patches the same `metadata.py`.

## Credits

All fixes in this mod were found, written and tested by
[@LixNix](https://github.com/LixNix), who also contributed the multi-nozzle
printing support to OrcaSlicer and Snapmaker Orca.
