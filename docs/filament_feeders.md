---
title: Filament Feeders
---

# Filament Feeders

Selects the filament feeder setup the U1 is using. The main thing that differs
between setups is the **preload length**: how far the feeders push filament
toward the toolhead when it is inserted. The stock value is 950 mm, which suits
the U1's built-in feeders. Setups that move the feeders further from the
toolhead need a longer preload, or filament stops short of the toolhead.

To support longer paths, the `preload_length` upper limit in
`klippy/extras/filament_feed.py` is raised from 1500 mm to 2500 mm. This is
always applied. The stock default of 950 mm is unchanged, so it has no effect
until a longer value is configured.

## Configuration

Select the feeder setup via the Firmware Config web interface under
**Snapmaker Components → Filament Feeders**:

| Option | Preload length | Use for |
|---|---|---|
| Default | 950 mm (stock) | U1's built-in feeder placement |
| BIQU PopStation mini | 2000 mm | Feeders installed in a [BIQU PopStation mini](#biqu-popstation-mini) |

Selecting an option other than **Default** links a config file into
`extended/klipper/` that overrides `preload_length` for both
`[filament_feed left]` and `[filament_feed right]`, then restarts Klipper.
`printer.cfg` is not modified. If you downgrade to a firmware without this
feature, the link is removed automatically on the next boot.

> **Only select a non-default option with the matching hardware installed.**
> With the stock feeder placement, a longer preload may push filament hard
> into the toolhead.

## BIQU PopStation mini

The [BIQU PopStation mini](https://neo.bttwiki.com/en/docs/more-series/pop-module/popstation-mini/)
is a filament storage and feeding station that sits under the U1, with the
U1's feeders moved into the station's drawer.

BIQU's setup guide has you SSH in, `touch /oem/.debug`, raise the limit in
`filament_feed.py` to 2500, and set `preload_length: 2000` in `printer.cfg`.
Stock firmware updates overwrite these edits. With the extended firmware, skip
all of those steps and select **Snapmaker Components → Filament Feeders →
BIQU PopStation mini** instead.

## Custom preload length

If none of the options fit your filament path, leave **Filament Feeders** on
**Default** and create your own file in `extended/klipper/` through
Fluidd/Mainsail (see [Klipper Custom Includes](klipper_includes.md)):

```cfg
[filament_feed left]
preload_length: 2000

[filament_feed right]
preload_length: 2000
```

Values between 600 and 2500 are accepted. Restart Klipper after saving.

## Troubleshooting

**Filament stops before reaching the toolhead:** check that **Filament
Feeders** matches your hardware. If you use a custom file, check that it is in
`extended/klipper/` and has a `.cfg` extension.

**Klipper fails to start with a `preload_length` error:** the value is
outside 600–2500 mm.
