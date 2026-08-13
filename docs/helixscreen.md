---
title: HelixScreen Touchscreen GUI (Experimental)
---

# HelixScreen Touchscreen GUI (Experimental)

Replace the stock Snapmaker touchscreen interface with
[HelixScreen](https://github.com/prestonbrown/helixscreen), a third-party
Klipper touchscreen UI.

> **Note**: HelixScreen is downloaded on-demand when selected by the user. See
> [third-party integration design](design/third_party.md) for details on how
> external components are managed.

> **Warning**: This feature is experimental and replaces the stock UI entirely.
> Snapmaker screen features that are not part of HelixScreen are unavailable
> while it is selected. Switching back to the stock UI is a single setting away.

## Options

- **snapmaker** - Stock Snapmaker touchscreen UI (default)
- **helixscreen** - HelixScreen

## Using firmware-config Web UI (preferred)

Navigate to the [firmware-config](firmware_config.md) web interface, go to the
Snapmaker Components section, and select HelixScreen under Touchscreen GUI. This
downloads and installs HelixScreen (~46 MB into `/oem/apps/helixscreen`), then
reboots the printer to apply — the GUI is only swapped while `lmd` starts.

Selecting Snapmaker again removes `/oem/apps/helixscreen` and reboots, so
switching back to HelixScreen downloads it again.

## Manual Setup (advanced)

Requires [SSH access](ssh_access.md) to the printer.

**Step 1:** Download HelixScreen (requires internet connection):

```bash
ssh root@<printer-ip>
extended-pkg helixscreen download
```

**Step 2:** Edit `extended/extended2.cfg`, set the `gui`:

```ini
[components]
gui: helixscreen
```

**Step 3:** Reboot.

## How it works

The stock UI is `/usr/bin/gui`, forked and supervised by `/usr/bin/lmd` from a
path compiled into the binary — there is no init script or launcher to redirect.
The `/etc/hooks/lmd.d/30-helixscreen.sh` hook therefore bind-mounts the
`helix-screen` binary over `/usr/bin/gui` before `lmd` starts, so `lmd` execs and
supervises HelixScreen exactly as it would the stock UI. The stock binary is
never started, so it never takes the DRM master and the display hands over
cleanly.

The bind mount is applied on every `lmd` start and lives only in memory, so
selecting `snapmaker` (or a reboot without the setting) restores stock behaviour
with nothing to undo on disk.

[Remote Screen](remote_screen.md) keeps working: HelixScreen renders through DRM
and never writes the framebuffer that `fb-http` snapshots, so it is asked to
mirror each rendered frame into `/dev/fb0`.

## WiFi

HelixScreen manages WiFi itself, over the same `wpa_supplicant` control socket
(`/var/run/wpa_supplicant`) the stock UI uses — scan and join from its own
network settings.

Networks are saved to `printer_data/gui/wpa_supplicant.conf`, the same file the
stock UI keeps its copy in, so they survive a reboot and both interfaces see the
same network. A network already configured on the stock screen is picked up
automatically.

## Settings

HelixScreen writes its settings, themes and spool assignments to
`printer_data/config/extended/helixscreen/`, so they survive both a reboot and a
firmware upgrade. By default it would keep them inside its own install
directory, which is replaced whenever a new version is downloaded.

The directory is moved aside by `extended-recover`, and removed by
`full-recover`, along with the rest of the extended configuration — see
[Data Persistence](data_persistence.md).

## Known limitations

- **The camera must not be set to Disabled.** `lmd` is not started at all in that
  case, and it is what launches the GUI — this applies to the stock UI too.
