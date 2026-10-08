# 14-patch-pbo-safe-pause

Stops a paused print-by-object job from parking the toolhead through taller objects that were finished earlier in the print.

Stock `INNER_PAUSE` lowers the bed a fixed 5 mm before parking. In a print-by-object job, an object finished earlier can be much taller than the one being printed, and parking drives straight through it. This affects the Pause button and spaghetti detection, which pauses through the same macro.

## What changed

`patches/home/lava/origin_printer_data/config/01-pbo-safe-pause.patch` (`fluidd.cfg`):

- `SET_PRINT_STATS_INFO` records the tallest Z printed so far, resetting at the start of each print.
- `INNER_PAUSE` lowers the bed by the larger of the stock 5 mm and enough to clear that height by 2 mm, capped at the bed's travel, and logs when it goes further than stock.
- `INNER_RESUME` passes `XY_FIRST=1` to `RESUME_BASE`, so the toolhead crosses back at the lowered height before the bed rises.

Print-by-layer pauses still lower the bed exactly 5 mm.

## Upstream

This is the same change as [Snapmaker/u1-klipper#17](https://github.com/Snapmaker/u1-klipper/pull/17). Remove this overlay once a Snapmaker firmware release includes it.
