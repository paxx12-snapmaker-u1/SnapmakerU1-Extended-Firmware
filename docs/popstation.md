---
title: BIQU PopStation mini
---

# BIQU PopStation mini

The [BIQU PopStation mini](https://neo.bttwiki.com/en/docs/more-series/pop-module/popstation-mini/)
is a filament storage and feeding station that sits under the Snapmaker U1, with
the U1's feeders moved into the station's drawer. The longer path from feeder to
toolhead means the stock filament preload length (950 mm) stops the filament
before it reaches the toolhead.

BIQU's setup guide fixes this by hand-editing two files over SSH: raising the
`preload_length` limit in `filament_feed.py` and setting `preload_length: 2000`
in `printer.cfg`. Stock firmware updates overwrite both edits. The extended
firmware ships the code change built in and makes the config change a single
toggle, so neither has to be redone after an update.

## What it does

- **Always on:** the `preload_length` upper limit in
  `klippy/extras/filament_feed.py` is raised from 1500 mm to 2500 mm. The
  stock default of 950 mm is unchanged, so this has no effect until a longer
  value is configured.
- **When enabled:** installs `extended/klipper/popstation.cfg`, which sets
  `preload_length: 2000` for both `[filament_feed left]` and
  `[filament_feed right]`, and restarts Klipper.

You do **not** need `touch /oem/.debug`, SSH, or any edits to `printer.cfg`
or `filament_feed.py`.

## Configuration

Enable via the Firmware Config web interface under **Snapmaker Components →
BIQU PopStation mini → Enabled**.

> **Only enable this with the feeders installed in a PopStation mini.** With
> the stock feeder placement, a 2000 mm preload may push filament hard into
> the toolhead.

To go back to the stock feeder placement, set it to **Disabled**.

### Custom preload length

If 2000 mm is too short or too long for your filament path, leave the toggle
**Disabled** and create your own file in `extended/klipper/` through
Fluidd/Mainsail (see [Klipper Custom Includes](klipper_includes.md)):

```cfg
[filament_feed left]
preload_length: 2000

[filament_feed right]
preload_length: 2000
```

Values between 600 and 2500 are accepted. Restart Klipper after saving.

## Troubleshooting

**Filament stops before reaching the toolhead:** check that **BIQU PopStation
mini** shows **Enabled**. If you use a custom file, check that it is in
`extended/klipper/` and has a `.cfg` extension.

**Klipper fails to start with a `preload_length` error:** the value is
outside 600–2500 mm.
