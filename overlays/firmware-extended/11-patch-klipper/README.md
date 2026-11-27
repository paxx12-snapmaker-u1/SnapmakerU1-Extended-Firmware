# This overlay integrates upstream klipper changes

## Upstream commits:

1. [resonance_tester: Fix chips selection, add accel_per_hz selection](https://github.com/Klipper3d/klipper/commit/6d1256ddc)
2. [toolhead: Use delta_v2 when calculating centripetal force](https://github.com/Klipper3d/klipper/commit/8291788f4)
3. [toolhead: Remove arbitrary constants controlling junction deviation](https://github.com/Klipper3d/klipper/commit/847331260)
4. [resonance_tester: Added a new sweeping_vibrations resonance test method](https://github.com/Klipper3d/klipper/commit/16b4b6b30)
5. [toolhead: Reduce LOOKAHEAD_FLUSH_TIME to 0.150 seconds](https://github.com/Klipper3d/klipper/commit/16fc46fe5)
6. snapmaker: Fix self.test references after upstream refactor

## U1 fix (not in Snapmaker/u1-klipper yet)

9. filament_parameters: on a 0.2 mm nozzle, `startswith('PC')` also matches PCTG, so a copolyester is refused with the polycarbonates (u1-klipper#14, print start code 25). PCTG is allowed; PC, PC-ABS, PC-CF and the rest of the 0.2 list stay refused. PCTG-CF and PCTG-GF stay refused because of the -CF/-GF rule. Regression: `tests/test_issue14pc.py` in github.com/DesignMaster-Solutions/u1-fixpack. Not run on a physical U1.

## How patches were generated

These patches were generated from the `snapmaker-lava/snapmaker-klipper` repository
on the `patches-v1.1.0` branch. The commit order and details follow the cherry-pick
sequence documented in `snapmaker-lava/snapmaker-klipper/SUMMARY.md`.

To regenerate patches, use `git format-patch` on each commit from snapmaker-klipper,
excluding docs/ directory changes to keep patches minimal and focused on code changes.

## Formatting of patches

Patches are ordered per SUMMARY.md, stripped of docs/ changes, and minimal amount of
amendments are done to make them as close to upstream as possible.
