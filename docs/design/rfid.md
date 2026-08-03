---
title: RFID Format & Reader Design
---

# RFID Format & Reader Design

The Snapmaker U1 automatically detects filament properties by reading RFID tags
on spools. Tags are read when filament is loaded into the feeder, and the data
clears when filament is removed.

**Firmware Support:**
- **Original:** Mifare Classic 1K with Snapmaker proprietary format
- **Extended:** Supports Custom Tags via OpenSpool, OpenRFID, or External Readers

For the OpenSpool payload format and field reference, see
[OpenSpool Format Design](openspool.md).

## Readers

What each detection system or hardware reader can identify.

| Reader | Author | Hardware | Snapmaker | OpenSpool | Bambu | Creality | Anycubic | Elegoo | Qidi | TigerTag | SpoolEase | SpoolLink | Support |
|--------|--------|----------|-----------|-----------|-------|----------|----------|--------|------|----------|-----------|-----------|---------|
| Snapmaker (built-in, default) | [Snapmaker](https://github.com/Snapmaker) | Internal | ✅ | ✅ | – | – | – | – | – | – | – | ✅ | — |
| [OpenRFID](https://github.com/suchmememanyskill/OpenRFID) | [suchmememanyskill](https://github.com/suchmememanyskill) | Internal | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| External - wasikuss: [snapmaker-u1-remote-rfid-reader](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader) | [wasikuss](https://github.com/wasikuss) | External — ESP32-C3 + PN532 | – | ✅ | – | – | – | – | – | – | – | – | — |
| External - baze: [snapmaker-u1-drybox-nfc-reader](https://gitlab.com/baze/snapmaker-u1-drybox-nfc-reader) | [baze](https://gitlab.com/baze) | External — ESP32-C3 + PN532, browser-flashable, [printable case](https://www.printables.com/model/1637071-remote-nfc-rfid-reader-for-snapmaker-u1) | – | ✅ | – | – | – | – | – | – | – | ✅ | — |

**Notes:**

- **SpoolLink** means the reader reports a tag's UID to the printer even when
  it can't parse the tag's contents (unknown format, encrypted, or blank), so
  the spool can still be resolved by [SpoolLink](../spoolman.md#apps) UID
  linking.
- Bambu and Creality require providing encryption keys (see
  [Bambu / Creality Spool Configuration](#enabling-openrfid) below).
- External readers sit outside the printer — for example on a drybox — and
  push tags to the U1 over the network. To use one, set the RFID Detection
  System to **External**. Licences: wasikuss MIT, baze CC BY-NC 4.0.

## Apps

Community apps that create and write filament tags. The quickest way to write
a tag is PrintTag-Web: open it in Chrome on Android, enter the filament
details, and tap an NTAG215/216 tag to the phone. Any app that writes NDEF
with JSON (MIME type `application/json`) also works.

| App | Author | Platform | Reads | Writes | Vendor Tags | Source | Support |
|-----|--------|----------|-------|--------|--------------|--------|---------|
| [U1-RFID](https://github.com/DnG-Crafts/U1-RFID) | [DnG-Crafts](https://github.com/DnG-Crafts) | Android ([Google Play](https://play.google.com/store/apps/details?id=dngsoftware.u1rfid)) | OpenSpool | OpenSpool, including all U1 extended fields | – | [GitHub](https://github.com/DnG-Crafts/U1-RFID) (source) | — |
| [SpoolPainter](https://github.com/ni4223/SpoolPainter) | [ni4223](https://github.com/ni4223) | Android ([Google Play](https://play.google.com/store/apps/details?id=com.spoolpainter.app)) | OpenSpool | OpenSpool | Bambu, Snapmaker, QIDI, Anycubic, Elegoo, Creality | [GitHub](https://github.com/ni4223/SpoolPainter) (GPL-3.0) | [![US](https://img.shields.io/badge/Snapmaker%20US-00B2E3?logo=shopify&logoColor=white)](https://snapmaker-us.myshopify.com?ref=ni42) [![EU](https://img.shields.io/badge/Snapmaker%20EU-00B2E3?logo=shopify&logoColor=white)](https://snapmaker-eu.myshopify.com?ref=ni42) [![Global](https://img.shields.io/badge/Snapmaker%20Global-00B2E3?logo=shopify&logoColor=white)](https://test-snapmaker.myshopify.com?ref=ni42)<br>Referral Code: `ni42` |
| [SpoolKid](https://github.com/marko-p/SpoolKid) | [Marco](https://github.com/marko-p) | iOS ([TestFlight beta](https://testflight.apple.com/join/Y4BmejQk); build from source) | OpenSpool, OpenTag3D, ELEGOO, Anycubic ACE; UID-only for Mifare Classic and unrecognized tags | OpenSpool, OpenTag3D, ELEGOO, Anycubic ACE | ELEGOO, Anycubic ACE | [GitHub](https://github.com/marko-p/SpoolKid) (MIT) | [![Ko-fi](https://img.shields.io/badge/Ko--fi-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/spoolkid) [![Sponsor](https://img.shields.io/badge/Sponsor-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/marko-p) |
| [OpenSpool app](https://github.com/spuder/OpenSpoolMobile) | [spuder](https://github.com/spuder) | Android, iOS | OpenSpool (basic fields) | OpenSpool (basic fields) | – | [GitHub](https://github.com/spuder/OpenSpoolMobile) (source) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://www.buymeacoffee.com/openspool) |
| [PrintTag-Web](https://printtag-web.pages.dev) | [paxx12](https://github.com/paxx12) | Android (Chrome, Web NFC) | OpenSpool, OpenPrintTag | OpenSpool, OpenPrintTag | – | [GitHub](https://github.com/paxx12/PrintTag-Web) (source) | [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/paxx12) |
| [NFC Tools](https://www.wakdev.com/en/apps/nfc-tools.html) | [wakdev](https://github.com/wakdev) | Android ([Google Play](https://play.google.com/store/apps/details?id=com.wakdev.wdnfc)), iOS ([App Store](https://apps.apple.com/us/app/nfc-tools/id1252962749)) | Any NDEF | Any NDEF | – | Closed source | — |
| [TigerTag Studio Manager](https://github.com/TigerTag-Project/TigerTag-Studio-Manager) | TigerSystem | Windows, macOS, Linux (desktop, ACR122U USB NFC reader recommended) | TigerTag | TigerTag | – | [GitHub](https://github.com/TigerTag-Project/TigerTag-Studio-Manager) (MIT) | — |
| [TigerTag Connect](https://tigertag.io/) | TigerSystem | Android ([Google Play](https://play.google.com/store/apps/details?id=com.tigertag.connect)), iOS ([App Store](https://apps.apple.com/us/app/tigertag-rfid-connect/id6745437963)) | TigerTag | TigerTag | – | Closed source | — |

**Notes:**

- Custom tags are only readable by the U1 on extended firmware.
- PrintTag-Web requires Chrome on Android (Web NFC API); it cannot read or
  write tags on iOS, desktop Chrome, or other browsers.
- Use **NFC Tools** to inspect an existing tag's type and NDEF records.
- Apps that also link tags to Spoolman spools are listed in
  [Spoolman Integration](../spoolman.md#apps).

## Enabling OpenRFID

The OpenRFID detection system is an alternative to Snapmaker's built-in
filament tag detection, based on the [OpenRFID](https://github.com/suchmememanyskill/OpenRFID)
project. It adds support for tagged spools from multiple manufacturers.

To enable it, navigate to the [firmware-config](../firmware_config.md) web
interface, go to **Snapmaker Components > RFID Detection System**, and select
**OpenRFID** or **OpenRFID (force generic vendor)**.

- **OpenRFID** - Filament is identified by brand and type. Spools unrecognized by Snapmaker Orca are hidden in Snapmaker Orca.
- **OpenRFID (force generic vendor)** - Same as OpenRFID, but spools are labeled as Generic so they always appear in Snapmaker Orca.
- **External** - Disables the built-in readers entirely, useful for [external readers](#readers).

| System | Enabled by default | Remarks |
|--------|-------------------|---------|
| Bambu | No | Requires additional configuration (see below) |
| Creality | No | Requires additional configuration (see below) |
| Anycubic | Yes | - |
| Snapmaker | Yes | - |
| Elegoo | No | Elegoo spools tagged with RFID work unreliably |
| [OpenSpool](https://openspool.io/) | Yes | - |
| TigerTag | Yes | Fully offline implementation |
| Qidi | Yes | - |
| [SpoolEase](https://spoolease.io/) | No | NTAG NDEF tags; enable the processor to use |

**Bambu / Creality Spool Configuration**

Bambu and Creality tagged spools require authentication keys. Add them to the
OpenRFID user configuration file:

```
/oem/printer_data/config/extended/openrfid_user.cfg
```

For **Bambu** spools:
```ini
[bambu_lab_tag_processor]
key = <your 32 hex character key>
```

For **Creality** spools:
```ini
[creality_tag_processor]
key = <your 32 hex character key>
encryption_key = <your 32 hex character key>
```

After editing, restart the printer.

**Enabling Disabled By Default Tag Systems**

Some tag formats are disabled by default, for example as they do not read
reliably. Enable them in `/oem/printer_data/config/extended/openrfid_user.cfg`
by removing the `#` prefix from the tag processor:

```
# [elegoo_tag_processor]
```

[SpoolEase](https://spoolease.io/) tags (NTAG NDEF) are also supported but not
enabled by default. Add the processor to the same file to use them:

```ini
[spoolease_tag_processor]
```

## Manual Commands

- Read tag: `FILAMENT_DT_UPDATE CHANNEL=<n>`
- Clear tag data: `FILAMENT_DT_CLEAR CHANNEL=<n>`
- Check current tag: `FILAMENT_DT_QUERY CHANNEL=<n>`

## Troubleshooting

**Tag not detected:**
- Ensure tag is NTAG213/215/216 or Mifare Classic 1K — ISO15693 tags
  (OpenPrintTag) are not supported by the U1 hardware
- Position tag within 1-3cm of reader antenna
- Ensure you place on tag on the side next to the U1 housing, which will depend on which side of the printer you load the spool
- If a vendor tag is present, for example Bambu Lab filament tags, this will usually interfere with reading a user-provided tag (you can cover up the vendor tag with foil tape)
- Manually read tag: `FILAMENT_DT_UPDATE CHANNEL=<n>` then `FILAMENT_DT_QUERY CHANNEL=<n>`
- For OpenRFID issues, open Fluidd **Logs** and fetch `openrfid.log`

**NTAG tags not read:**
- NTAG215/216 support requires extended firmware; original firmware only
  supports Mifare Classic 1K with the Snapmaker proprietary format
