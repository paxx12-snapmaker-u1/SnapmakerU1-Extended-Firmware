# This overlay integrates upstream klipper changes

## Upstream commits:

1. [resonance_tester: Fix chips selection, add accel_per_hz selection](https://github.com/Klipper3d/klipper/commit/6d1256ddc)
2. [toolhead: Use delta_v2 when calculating centripetal force](https://github.com/Klipper3d/klipper/commit/8291788f4)
3. [toolhead: Remove arbitrary constants controlling junction deviation](https://github.com/Klipper3d/klipper/commit/847331260)
4. [resonance_tester: Added a new sweeping_vibrations resonance test method](https://github.com/Klipper3d/klipper/commit/16b4b6b30)
5. [toolhead: Reduce LOOKAHEAD_FLUSH_TIME to 0.150 seconds](https://github.com/Klipper3d/klipper/commit/16fc46fe5)
6. snapmaker: Fix self.test references after upstream refactor

## U1 fixes (offered to Snapmaker/u1-klipper, not merged there yet)

10. [exclude_object: drop the skipped-object offset after a park outside the transform](https://github.com/Snapmaker/u1-klipper/pull/24) (fixes [u1-klipper#22](https://github.com/Snapmaker/u1-klipper/issues/22): skip an object, pause, and Resume fails with "Move out of range")

The part-fan fix for [u1-klipper#18](https://github.com/Snapmaker/u1-klipper/issues/18) (M106/M107 switch the fan
seconds early) is #787, by @alternativniy.

It comes with a printer-free regression test in
[u1-fixpack](https://github.com/DesignMaster-Solutions/u1-fixpack) (`tests/extended/`).

## How patches were generated

These patches were generated from the `snapmaker-lava/snapmaker-klipper` repository
on the `patches-v1.1.0` branch. The commit order and details follow the cherry-pick
sequence documented in `snapmaker-lava/snapmaker-klipper/SUMMARY.md`.

To regenerate patches, use `git format-patch` on each commit from snapmaker-klipper,
excluding docs/ directory changes to keep patches minimal and focused on code changes.

## Formatting of patches

Patches are ordered per SUMMARY.md, stripped of docs/ changes, and minimal amount of
amendments are done to make them as close to upstream as possible.
