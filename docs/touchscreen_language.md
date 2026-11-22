---
title: Touchscreen Language (Polish)
---

# Touchscreen Language (Polish)

Adds a Polish translation to the stock Snapmaker touchscreen UI: all menus
and error messages.

> **Note**: The stock UI has a fixed, compiled-in list of languages. Polish
> therefore takes the place of Turkish while it is selected. Turkish is
> back as soon as the option is set to **Stock**. The option is off by
> default.

## Options

- **stock** - Stock languages (default)
- **polish** - Polish, listed as "Polski" in place of "Türkçe"

## Using firmware-config Web UI (preferred)

1. Open the [firmware-config](firmware_config.md) web interface.
2. In the Snapmaker Components section, set Touchscreen Language to
   **Polski (replaces Türkçe)**. The printer reboots.
3. On the printer, select **Polski** under *Settings → Language*.

Only the stock Snapmaker UI is translated. Nothing changes while
[HelixScreen](helixscreen.md) is the selected touchscreen GUI.

## Manual Setup (advanced)

Requires [SSH access](ssh_access.md) to the printer.

Edit `extended/extended2.cfg`, set `gui_language`:

```ini
[components]
gui_language: polish
```

Then reboot.

## How it works

The stock UI (`/usr/bin/gui`) reads its texts from JSON files in
`/home/lava/resource`, but the language list and the menu labels are compiled
into the binary. At build time, the firmware gets the Polish texts and a copy
of `/usr/bin/gui` with the "Türkçe" label renamed to "Polski". The label sits
in a fixed slot, so nothing else in the binary moves. All of these are kept in
`/usr/local/share/gui-polish`.

When the option is selected, the `/etc/hooks/lmd.d/35-gui-polish.sh` hook
bind-mounts these files over the Turkish files and the stock binary before
`lmd` starts the UI. The bind mounts live only in memory, so selecting
**stock** (or a reboot without the setting) restores the stock UI with nothing
to undo on disk.

## Updating the translation

Translators: see
`overlays/firmware-extended/43-feature-gui-polish/i18n/README.md`. The
`i18n.py` tool lists only the texts that changed in a new stock firmware.
