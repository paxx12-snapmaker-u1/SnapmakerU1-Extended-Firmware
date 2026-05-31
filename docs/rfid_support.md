---
title: RFID Filament Tag Support
---

# RFID Filament Tag Support


The Snapmaker U1 automatically detects filament properties by reading RFID tags on spools.

**Firmware Support:**
- **Original:** Mifare Classic 1K with Snapmaker proprietary format
- **Extended:** Adds NTAG215/216 support with OpenSpool format

## Supported Formats

| Feature | OpenSpool 🏆 | OpenPrintTag | OpenTag3D | Snapmaker |
|---------|--------------|--------------|-----------|-----------|
| **Tag Type** | NTAG215 (540 bytes) / NTAG216 (888 bytes) | ISO15693/SLIX2 | NTAG215/216 or ISO15693/SLIX2 | Mifare Classic 1K |
| **Encoding** | JSON (NDEF) | CBOR (NDEF) | Binary | Proprietary + RSA signature |
| **Data Format** | Human-readable JSON | Compact CBOR binary | Binary | Encrypted proprietary |
| **Specification** | [openspool.io](https://openspool.io/rfid.html) | [specs.openprinttag.org](https://specs.openprinttag.org/#/) | [OpenTag3D](https://github.com/prusa3d/OpenTag3D) | Proprietary (closed) |
| **GitHub Repository** | [spuder/OpenSpool](https://github.com/spuder/OpenSpool) | [prusa3d/OpenPrintTag](https://github.com/prusa3d/OpenPrintTag) | [queengooborg/OpenTag3D](https://github.com/queengooborg/OpenTag3D) | N/A |
| **Popularity** | ⭐⭐⭐ (623 stars) | ⭐⭐ (213 stars) | ⭐ (17 stars) | N/A |
| **Programming Tools** | Any NDEF-capable NFC app | Prusa app only | [opentag3d.info/make](https://opentag3d.info/make) | Snapmaker official only |
| **U1 Compatible** | ✅ Yes (extended firmware) | ❌ No (ISO15693 not supported) | ⚠️ Not implemented yet | ✅ Yes (all firmware) |
| **Ease of Programming** | Easy (any NFC app) | Medium (requires Prusa app) | Medium (web-based tool) | Hard (official tags only) |
| **Data Portability** | High (simple JSON) | High (open CBOR spec) | Medium (binary format) | None (proprietary) |

🏆 = Recommended for U1 (NTAG215 is the sweet spot for capacity and compatibility)

## How It Works

Tags are automatically read when filament is loaded into the feeder. Tag data clears when filament is removed.

**Manual Commands:**
- Read tag: `FILAMENT_DT_UPDATE CHANNEL=<n>`
- Clear tag data: `FILAMENT_DT_CLEAR CHANNEL=<n>`
- Check current tag: `FILAMENT_DT_QUERY CHANNEL=<n>`

## Programming Filament Tags

### OpenSpool (Recommended for Extended Firmware)

**Quick Setup:**
1. Get NTAG215 or NTAG216 tags
2. Open Chrome on Android phone
3. Visit [printtag-web.pages.dev](https://printtag-web.pages.dev)
4. Enter filament information
5. Tap tag to phone to write

**Alternative:** Use any NFC app that supports NDEF with JSON (MIME type: `application/json`)

Example payload:
```json
{
  "protocol": "openspool",
  "version": "1.0",
  "brand": "Generic",
  "type": "PLA",
  "color_hex": "#FF0000",
  "min_temp": 190,
  "max_temp": 220,
  "bed_min_temp": 50,
  "bed_max_temp": 60
}
```

Using the non-standard OpenSpool `subtype` field it is possible to specify a material subtype:

```json
{
  "protocol": "openspool",
  "version": "1.0",
  "type": "PETG",
  "subtype": "Rapid",
  "color_hex": "AFAFAF",
  "additional_color_hexes": ["EEFFEE","FF00FF"],
  "alpha": "FF",
  "brand": "Elegoo",
  "min_temp": "230",
  "max_temp": "260"
}
```

### OpenSpool Field Reference

**Required Fields:**
- `protocol` - Must be "openspool"
- `version` - Specification version (e.g., "1.0")
- `type` - Material type (PLA, PETG, ABS, TPU, etc.)
- `color_hex` - Color in hex format (#RRGGBB)

**Optional Standard Fields:**
- `brand` - Manufacturer name
- `min_temp` / `max_temp` - Nozzle temperature range in °C

**Optional Extended Fields (U1-specific):**
- `bed_min_temp` / `bed_max_temp` - Bed temperature range in °C
- `subtype` - Material variant (Basic, Rapid, HF, Silk, etc.)
- `alpha` - Color transparency (00-FF hex, default: FF)
- `additional_color_hexes` - Additional colors for multicolor spools (up to 4)
- `weight` - Spool weight in grams
- `diameter` - Filament diameter in mm (e.g., 1.75)

### Snapmaker Orca Naming Convention

Snapmaker Orca requires filaments to follow this naming pattern: `<brand> <type> <subtype>`

Examples: `Generic PLA Basic`, `Elegoo PETG Rapid`

## Reading Existing Tags

Use the **NFC Tools** app (iOS/Android) to inspect tags:

1. Download NFC Tools from App Store or Google Play
2. Tap "Read" and hold tag to phone
3. Check tag type and NDEF records

**Compatible tag types:** NTAG213/215/216, Mifare Classic 1K
**Note:** ISO15693 tags (OpenPrintTag) are not supported

## Alternative Detection Systems


The OpenRFID detection system is an alternative to Snapmaker's built-in filament tag detection, based on the [OpenRFID](https://github.com/suchmememanyskill/OpenRFID) project. It adds support for tagged spools from multiple manufacturers.

To enable it, navigate to the [firmware-config](firmware_config.md) web interface, go to **Snapmaker Components > RFID Detection System**, and select **OpenRFID** or **OpenRFID (force generic vendor)**.

- **OpenRFID** - Filament is identified by brand and type. Spools unrecognized by Snapmaker Orca are hidden in Snapmaker Orca.
- **OpenRFID (force generic vendor)** - Same as OpenRFID, but spools are labeled as Generic so they always appear in Snapmaker Orca.
- **External** - Disables the built-in readers entirely, useful for external readers such as [wasikuss/snapmaker-u1-remote-rfid-reader](https://github.com/wasikuss/snapmaker-u1-remote-rfid-reader).

### Supported Tags

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

### Bambu / Creality Spool Configuration

Bambu and Creality tagged spools require authentication keys. Edit the user configuration file to enable them:

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

### Enabling Disabled By Default Tag systems

Some tag formats are disabled by default, for example as they do not read reliably. You can enable them by editing the following file:

```
/oem/printer_data/config/extended/openrfid_user.cfg
```

Enable them by removing the `#` prefix from the tag processor.

```
# [elegoo_tag_processor]
```

[SpoolEase](https://spoolease.io/) tags (NTAG NDEF) are also supported but not
enabled by default. Add the processor to the same file to use them:

```ini
[spoolease_tag_processor]
```

## Troubleshooting

**Tag not detected:**
- Ensure tag is NTAG213/215/216 or Mifare Classic 1K
- Position tag within 1-3cm of reader antenna
- Ensure you place on tag on the side next to the U1 housing, which will depend on which side of the printer you load the spool
- If a vendor tag is present, for example Bambu Lab filament tags, this will usually interfere with reading a user-provided tag (you can cover up the vendor tag with foil tape)
- Manually read tag: `FILAMENT_DT_UPDATE CHANNEL=<n>` then `FILAMENT_DT_QUERY CHANNEL=<n>`
- For OpenRFID issues, open Fluidd **Logs** and fetch `openrfid.log`

**OpenPrintTag tags don't work:**
- Expected - OpenPrintTag uses ISO15693 which is not supported by U1 hardware
- Use NTAG tags with OpenSpool format instead

**NTAG tags only work on extended firmware:**
- Original firmware only supports Mifare Classic 1K with Snapmaker proprietary format
- Extended firmware adds NTAG215/216 support
