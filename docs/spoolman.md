---
title: Spoolman Integration
---

# Spoolman Integration

Automatic filament metadata sync and spool tracking via
[Spoolman](https://github.com/Donkie/Spoolman).

## What It Provides

- Resolves a Spoolman spool by ID or RFID card UID and applies its
  metadata (vendor, material, variant, colour) to the extruder channel.
- Associates RFID card UIDs with spools automatically, so scanning a
  known card loads its filament without any manual step.
- Tracks the active spool in Moonraker so Spoolman can update remaining
  filament weight as you print.

## Enabling

Enable via Fluidd/Mainsail settings under
**Snapmaker Components > Spoolman Integration**, set the Spoolman host,
and reboot. Set the same toggle to **Disabled** to turn it off.

## Apps

Community apps that support SpoolLink, so scanning a tag resolves its spool
automatically when the filament is loaded:

| App | Author | Platform | SpoolLink support | Legacy Spools | Source | Support the author |
|-----|--------|----------|--------------------|----------------|--------|---------------------|
| [SpoolLink (Android)](https://github.com/paxx12-snapmaker-u1/proof-of-concept-spool-link-apps) | [paxx12](https://github.com/paxx12) | Android (build from source) | Reference implementation — links tags to spools via NTAG or Mifare Classic UIDs, pre-fills from OpenSpool tags | ✅ (see notes) | [GitHub](https://github.com/paxx12-snapmaker-u1/proof-of-concept-spool-link-apps) (GPL-3.0) | — |
| [SpoolLink (iOS)](https://github.com/paxx12-snapmaker-u1/proof-of-concept-spool-link-apps) | [paxx12](https://github.com/paxx12) | iOS (build from source; requires a paid Apple Developer account for the NFC entitlement) | Reference implementation — links tags to spools via NTAG UIDs only, pre-fills from OpenSpool tags | ✅ (see notes) | [GitHub](https://github.com/paxx12-snapmaker-u1/proof-of-concept-spool-link-apps) (GPL-3.0) | — |
| [SpoolPainter](https://github.com/ni4223/SpoolPainter) | [ni4223](https://github.com/ni4223) | Android ([Google Play](https://play.google.com/store/apps/details?id=com.spoolpainter.app)) | Links tags to spools; create-and-pair spools, multi-tag binding, vendor tag pairing | ✅ (see notes) | [GitHub](https://github.com/ni4223/SpoolPainter) (GPL-3.0) | [![Snapmaker Store](https://img.shields.io/badge/Snapmaker%20Store-00B2E3?logo=shopify&logoColor=white)](https://snapmaker-us.myshopify.com?ref=ni42) |
| [SpoolKid](https://github.com/marko-p/SpoolKid) | [Marco](https://github.com/marko-p) | iOS ([TestFlight beta](https://testflight.apple.com/join/Y4BmejQk); build from source) | Links tag UIDs to spools, including UID-only linking for Mifare Classic and other tags it can't otherwise read | – | [GitHub](https://github.com/marko-p/SpoolKid) (MIT) | [![Ko-fi](https://img.shields.io/badge/Ko--fi-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/spoolkid) |
| [SpoolTagger](https://codeberg.org/NiftyBits/SpoolTagger) | [NiftyBits](https://codeberg.org/NiftyBits) | Windows, Linux (desktop, ACR122U USB NFC reader) | Links tags to spools (up to 2 tags per spool) | ✅ (see notes) | [Codeberg](https://codeberg.org/NiftyBits/SpoolTagger) (MIT) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/NiftyBits) |
| [3DMRP](https://github.com/MKloberg/3dmrp) | [MKloberg](https://github.com/MKloberg) | Self-hosted (Windows), phone via Chrome on Android | Print-farm manager — links tags to spools, NFC scan-to-select; also AFC lane control and U1 touchscreen mirror | – (see notes) | [GitHub](https://github.com/MKloberg/3dmrp) (source) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/mkloberg) |

**Notes:**

- **Legacy Spools** means the app converts an older tag into a SpoolLink
  link, so spools tagged before SpoolLink keep resolving:
  - **SpoolLink apps** (Android and iOS) — a scanned tag carrying an
    OpenSpool `spool_id` is linked to that spool automatically.
  - **SpoolPainter** — a scanned tag carrying an OpenSpool `spool_id`
    resolves its spool and offers to re-pair it.
  - **SpoolTagger** — migrates the whole Spoolman library in one pass on
    startup.
  - **3DMRP** — writes the current convention since v0.8.1; earlier links
    are updated on the next rescan.
- 3DMRP's Android support is its Chrome-based mobile NFC client (Web NFC
  API); the print-farm server itself runs on Windows only.
- Apps that write filament data (material, colour, temperatures) to tags
  are listed in [RFID Format & Reader Design](design/rfid.md#apps).

## External Readers

Community hardware readers push detected tags to the same
`filament_detect/set` webhook the printer's built-in readers use. For a tag
to resolve a Spoolman spool by UID, the reader must report the UID for every
detected tag — even one it can't otherwise read — the same way
[OpenRFID](design/rfid.md#enabling-openrfid) falls back to a UID-only report
when a tag can't be parsed:

| Project | Author | SpoolLink support | Source |
|---------|--------|--------------------|--------|
| External - wasikuss: [snapmaker-u1-remote-rfid-reader](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader) | [wasikuss](https://github.com/wasikuss) | Partial — only reports a tag when its payload validates as OpenSpool JSON; unreadable, blank, or vendor-format tags are never reported, so those spools can't be resolved by UID | [GitHub](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader) (MIT) |
| External - baze: [snapmaker-u1-drybox-nfc-reader](https://gitlab.com/baze/snapmaker-u1-drybox-nfc-reader) | [baze](https://gitlab.com/baze) | Full — reports the UID for every detected tag by default ("UID-only" mode); can optionally switch to a full-read mode that also sends OpenSpool metadata, but then skips tags that aren't valid OpenSpool | [GitLab](https://gitlab.com/baze/snapmaker-u1-drybox-nfc-reader) (CC BY-NC 4.0) |

Hardware and setup details for both readers are in
[RFID Format & Reader Design](design/rfid.md#readers).

## GCode Commands

### `SET_SPOOL_ID`

Assign a Spoolman spool to an AFC lane. Reads the lane's RFID card UID,
binds it to the spool (so a later scan resolves automatically), and
applies the filament metadata to the channel:

```
SET_SPOOL_ID LANE=E0 SPOOL_ID=5
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `LANE` | — | AFC lane name (e.g. `E0`) |
| `SPOOL_ID` | `0` | Spoolman spool ID; `0` clears the assignment |

## Limitations

- Spoolman must be reachable from the printer over HTTP.
- Variant defaults to `Basic` for Snapmaker-branded filaments when not
  set in Spoolman; empty for all other vendors.

For the wire format, custom fields, and component flow see the
[design notes](design/spoolman.md). For AFC lane status that surfaces
`spool_id` see [AFC-Lite](afc-lite.md).
