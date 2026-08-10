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

## Creating a Spool from a U1 Channel

The U1 Filament Manager can create and assign a Spoolman spool directly from
a metadata-bearing RFID tag:

1. Open **Filament Manager** and select **Read spool tags**.
2. On a channel whose detected UID is not already stored in Spoolman, select
   **Create Spool**.
3. Review the filament and mass values, then select **Create and Assign**.

The button is shown only when the tag provides a valid UID, vendor, material,
and six-digit colour. UID-only, empty, and malformed tags cannot provide enough
metadata to create a spool. They can still be assigned to an existing spool by
using **Spool**.

Before writing anything, the dialog reloads the Spoolman spool, vendor, and
filament records. It reuses a vendor case-insensitively. It also reuses an exact
filament match based on vendor, material, variant, and colour; blank and
`Basic` variants are treated as equivalent. If multiple exact filament matches
exist, the user must select one. Otherwise the dialog creates the required
vendor and filament before creating the spool.

The initial and remaining filament values use the tag's reported net mass when
it is valid, or a reviewed default of 1000 g. When an existing filament is
reused, its empty-spool mass is inherited. For a new filament, empty-spool mass
is optional. Diameter, density, temperatures, colour, name, and both mass
values remain reviewable in the dialog.

Creation stores only the SpoolLink fields documented below: `card_uids` on the
spool and, when present, `variant` on a newly created filament. If a request
fails after one or more records were created, those records are retained and
their IDs are reported. Retrying is safe because the dialog rechecks UID
ownership and exact filament matches before creating additional records.

## Two RFID Tags on One Spool

Use **Spool** on the channel reading the second physical tag, then select the
same Spoolman spool. SpoolLink appends the UID to that spool's comma-separated
`card_uids` value. The picker displays the number of stored RFID tags and uses
the following safeguards:

- Adding the first UID needs no additional confirmation beyond selecting the
  spool.
- Adding a second UID requires an explicit **Add Second RFID** confirmation.
- Adding a third or later UID requires an explicit warning confirmation.
- Moving a UID from another spool requires an explicit move confirmation.
- A UID found on more than one spool is rejected until the duplicate ownership
  is corrected in Spoolman.

After `SET_SPOOL_ID`, the Filament Manager reloads Spoolman and verifies that
the UID has exactly one owner and that all UIDs previously stored on the target
spool remain present. Filament metadata alone is never used to pair two tags;
identical spools can legitimately have the same metadata.

## Apps

Community apps that support SpoolLink, so scanning a tag resolves its spool
automatically when the filament is loaded:

| App | Author | Platform | Reads | SpoolLink support | Legacy Spools | Source | Support the author |
|-----|--------|----------|-------|--------------------|----------------|--------|---------------------|
| [SpoolLink (Android)](https://github.com/paxx12-snapmaker-u1/spool-link-apps) | [paxx12](https://github.com/paxx12) | Android ([releases](https://github.com/paxx12-snapmaker-u1/spool-link-apps/releases/latest); or build from source) | OpenSpool; any tag by UID (NTAG, Mifare Classic) | Reference implementation — links tags to spools by UID, pre-fills from OpenSpool tags | ✅ (see notes) | [GitHub](https://github.com/paxx12-snapmaker-u1/spool-link-apps) (GPL-3.0) | — |
| [SpoolLink (iOS)](https://github.com/paxx12-snapmaker-u1/spool-link-apps) | [paxx12](https://github.com/paxx12) | iOS ([releases](https://github.com/paxx12-snapmaker-u1/spool-link-apps/releases/latest) IPA — sideload via AltStore or similar; building from source requires a paid Apple Developer account for the NFC entitlement) | OpenSpool; any tag by UID (NTAG only — no Mifare Classic on iOS) | Reference implementation — links tags to spools by UID, pre-fills from OpenSpool tags | ✅ (see notes) | [GitHub](https://github.com/paxx12-snapmaker-u1/spool-link-apps) (GPL-3.0) | — |
| [SpoolPainter](https://github.com/ni4223/SpoolPainter) | [ni4223](https://github.com/ni4223) | Android ([Google Play](https://play.google.com/store/apps/details?id=com.spoolpainter.app)) | OpenSpool; Bambu Lab, Snapmaker, QIDI, Anycubic, Elegoo, Creality (vendor decode + prefill; Bambu Lab and Creality need a per-brand key); any tag by UID for pairing | Links tags to spools; create-and-pair spools, multi-tag binding, vendor tag pairing | ✅ (see notes) | [GitHub](https://github.com/ni4223/SpoolPainter) (GPL-3.0) | [![US](https://img.shields.io/badge/Snapmaker%20US-00B2E3?logo=shopify&logoColor=white)](https://snapmaker-us.myshopify.com?ref=ni42) [![EU](https://img.shields.io/badge/Snapmaker%20EU-00B2E3?logo=shopify&logoColor=white)](https://snapmaker-eu.myshopify.com?ref=ni42) [![Global](https://img.shields.io/badge/Snapmaker%20Global-00B2E3?logo=shopify&logoColor=white)](https://test-snapmaker.myshopify.com?ref=ni42)<br>Referral Code: `ni42` |
| [SpoolKid](https://github.com/marko-p/SpoolKid) | [Marco](https://github.com/marko-p) | iOS ([TestFlight beta](https://testflight.apple.com/join/Y4BmejQk); build from source) | OpenSpool, OpenTag3D, ELEGOO, Anycubic ACE; any other tag by UID (including Mifare Classic) | Links tag UIDs to spools, including UID-only linking for Mifare Classic and other tags it can't otherwise read | – | [GitHub](https://github.com/marko-p/SpoolKid) (MIT) | [![Ko-fi](https://img.shields.io/badge/Ko--fi-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/spoolkid) |
| [SpoolTagger](https://codeberg.org/NiftyBits/SpoolTagger) | [NiftyBits](https://codeberg.org/NiftyBits) | Windows, Linux (desktop, ACR122U USB NFC reader; [releases](https://codeberg.org/NiftyBits/SpoolTagger/releases/latest)) | OpenSpool (NTAG); Mifare Classic by UID only | Links tags to spools (up to 2 tags per spool) | ✅ (see notes) | [Codeberg](https://codeberg.org/NiftyBits/SpoolTagger) (MIT) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/NiftyBits) |
| [3DMRP](https://github.com/MKloberg/3dmrp) | [MKloberg](https://github.com/MKloberg) | Self-hosted (Windows; [releases](https://github.com/MKloberg/3dmrp/releases/latest)), phone via Chrome on Android | Any tag by UID (Web NFC scan-to-select; no content parsing) | Print-farm manager — links tags to spools, NFC scan-to-select; also AFC lane control and U1 touchscreen mirror | – (see notes) | [GitHub](https://github.com/MKloberg/3dmrp) (source) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/mkloberg) |
| [Spool Studio](https://github.com/GeorgHo/SpoolStudio) | [GeorgHo](https://github.com/GeorgHo) | Android | OpenSpool; Bambu Lab (with user-provided key); legacy Spool Studio tags (for conversion); any tag by UID | Links tags to spools via NFC card UID; writes Paxx12-compatible OpenSpool tags and shows the printer-reported toolhead status | ✅ (see notes) | [GitHub](https://github.com/GeorgHo/SpoolStudio) (MIT) | — |

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
  - **Spool Studio** — v3 converts tags written by its own older
    (pre-SpoolLink) spool-id format after confirmation; v2 stable remains
    available for older firmware that predates SpoolLink.
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

| Project | Author | SpoolLink support | Source | Support the author |
|---------|--------|--------------------|--------|---------------------|
| External - wasikuss: [snapmaker-u1-remote-rfid-reader](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader) | [wasikuss](https://github.com/wasikuss) | Partial — only reports a tag when its payload validates as OpenSpool JSON; unreadable, blank, or vendor-format tags are never reported, so those spools can't be resolved by UID | [GitHub](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader) (MIT) | — |
| External - baze: [snapmaker-u1-drybox-nfc-reader](https://gitlab.com/baze/snapmaker-u1-drybox-nfc-reader) | [baze](https://gitlab.com/baze) | Full — reports the UID for every detected tag by default ("UID-only" mode); can optionally switch to a full-read mode that also sends OpenSpool metadata, but then skips tags that aren't valid OpenSpool | [GitLab](https://gitlab.com/baze/snapmaker-u1-drybox-nfc-reader) (CC BY-NC 4.0) | — |

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

## Known Conflicts

- **Leftover pre-SpoolLink Spoolman macros.** If Spoolman was previously
  wired up by hand (e.g. a community `spoolman_multi_tool`-style Klipper
  include with `CHANGE_SPOOL` and related macros) before switching to
  SpoolLink, that file conflicts with the integration and can leave
  Klipper failing to start with an error such as:

  ```
  [Errno 2] No such file or directory: '/printer_data/config/variables.cfg'
  ```

  Rename or remove the old include (e.g. add a `.bak` suffix) before
  enabling Spoolman here. See
  [#567](https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/pull/567#issuecomment-5118303479)
  for a reported case and workaround.

## Limitations

- Spoolman must be reachable from the printer over HTTP.
- Creation and RFID assignment require Spoolman's current v1 API through
  Moonraker's version-2 Spoolman proxy response.
- UID ownership checks use the same 1000-spool candidate limit as SpoolLink.
- Variant defaults to `Basic` for Snapmaker-branded filaments when not
  set in Spoolman; empty for all other vendors.

For the wire format, custom fields, and component flow see the
[design notes](design/spoolman.md). For AFC lane status that surfaces
`spool_id` see [AFC-Lite](afc-lite.md).
