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

This is implemented with [minimdnsd](https://github.com/cnlohr/minimdnsd), a small
standalone mDNS responder. It only answers hostname (`A`/`AAAA`) queries for
`<printer-name>.local` — it does not advertise or browse services, so the printer
will not show up in Bonjour-style network/printer discovery tools. A full
Avahi/DNS-SD stack could be added later if that's ever needed; this feature covers
just the "can't remember the IP" case.

## Enabling

1. Open `http://<printer-ip>/firmware-config/` in a browser.
2. Scroll to the mDNS section and enable it.

Or via config file:

```bash
extended-config.py add /home/lava/printer_data/config/extended/extended2.cfg networking mdns_enabled true
/etc/init.d/S31mdns restart
```

## Usage

From another device on the same local network:

```bash
ping <printer-name>.local
```

Where `<printer-name>` is the printer's display name, with spaces and punctuation
replaced by hyphens (mDNS hostnames only allow letters, digits, and hyphens).

## Limitations

- Hostname resolution only — no service advertisement or discovery.
- mDNS only works within the same local network/broadcast domain; it will not
  resolve across VLANs, VPNs, or the internet.
- The advertised name only updates when the service is restarted (toggle off/on,
  or reboot) after renaming the printer.
