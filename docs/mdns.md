---
title: mDNS (.local Hostname)
---

# mDNS (.local Hostname)

Optional mDNS responder that lets you reach the printer as `<printer-name>.local`
on the local network, instead of remembering its IP address.

The advertised name is the printer's display name — the same name shown in the
Snapmaker app and set via `Settings` > `Device Name` — not the Linux system
hostname. Renaming the printer while mDNS is enabled requires toggling the setting
off and back on (or a reboot) to pick up the new name.

This is implemented with a small, purpose built, mDNS responder
(`overlays/firmware-extended/39-feature-mdns/apps/cmdnsd/`). It only answers
hostname (`A`) queries for `<printer-name>.local`.

## Enabling

1. Open `http://<printer-ip>/firmware-config/` in a browser.
2. Scroll to the mDNS section and enable it.
