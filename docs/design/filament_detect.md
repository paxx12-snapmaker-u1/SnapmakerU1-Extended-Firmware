---
title: External RFID Support
---

# External RFID Support

This document defines the external RFID integration contract for Snapmaker U1 Extended Firmware.
For build and overlay workflow context, see [Building from Source](../development.md).

## `filament_detect` Fields

The `filament_detect` object holds per-channel filament state in `FILAMENT_INFO_STRUCT`. The same
field names are:

- returned by `GET /printer/objects/query?filament_detect` at `result.status.filament_detect.info[channel]`
- accepted by `POST /printer/filament_detect/set` in the `info` object (writable subset; unknown keys rejected)

Writable fields:

| Field | Type | Accepted values / format | Notes |
|---|---|---|---|
| `VENDOR` | `string` | Any string | |
| `MAIN_TYPE` | `string` | `PLA`, `PETG`, `ABS`, `TPU`, `PVA` | Other values accepted but not RFID-protocol-mapped |
| `SUB_TYPE` | `string` | `Basic`, `Matte`, `SnapSpeed`, `Silk`, `Support`, `HF`, `95A`, `95A HF` | Other values accepted but not RFID-protocol-mapped |
| `RGB_1` | `int` | Integer RGB value | |
| `RGB_2` | `int` | Integer RGB value | Requires `COLOR_NUMS` >= 2 on read |
| `RGB_3` | `int` | Integer RGB value | Requires `COLOR_NUMS` >= 3 on read |
| `RGB_4` | `int` | Integer RGB value | Requires `COLOR_NUMS` >= 4 on read |
| `RGB_5` | `int` | Integer RGB value | Requires `COLOR_NUMS` >= 5 on read |
| `ALPHA` | `int` | Integer 0..255 | |
| `HOTEND_MIN_TEMP` | `int` | Integer | |
| `HOTEND_MAX_TEMP` | `int` | Integer | |
| `BED_TEMP` | `int` | Integer | |
| `CARD_UID` | `list[int]` | Array of byte ints | |
| `SKU` | `int` | Integer | |

Read-only fields (returned by query, not accepted by `set`):

| Field | Notes |
|---|---|
| `ARGB_COLOR` | Derived: `(ALPHA << 24) \| RGB_1` |
| `OFFICIAL` | `true` when `info` is non-empty (set by firmware) |
| `MANUFACTURER` | |
| `VERSION` | |
| `TRAY` | |
| `COLOR_NUMS` | Derived: count of `RGB_x` fields provided (minimum 1) |
| `DIAMETER` | |
| `WEIGHT` | |
| `LENGTH` | |
| `DRYING_TEMP` | |
| `DRYING_TIME` | |
| `BED_TYPE` | |
| `FIRST_LAYER_TEMP` | |
| `OTHER_LAYER_TEMP` | |
| `MF_DATE` | |
| `RSA_KEY_VERSION` | |

## API Contract: `filament_detect/set`

Endpoint:

- `POST /printer/filament_detect/set`

Request shape:

```json
{
  "channel": 0,
  "info": {
    "VENDOR": "Generic",
    "MAIN_TYPE": "PLA",
    "SUB_TYPE": "Basic",
    "RGB_1": 16737792,
    "ALPHA": 255,
    "HOTEND_MIN_TEMP": 200,
    "HOTEND_MAX_TEMP": 220,
    "BED_TEMP": 55,
    "CARD_UID": [161, 178, 195, 212],
    "SKU": 12345
  }
}
```

| Field | Required | Type | Accepted values |
|---|---|---|---|
| `channel` | Yes | `int` | `0..3` |
| `info` | No | `object` | If missing/empty, treated as clear/reset |

Response:

- Success: `{"state": "success"}`
- Error: `{"state": "error", "message": "..."}`

## OpenSpool U1 Extended Format Input

This firmware uses an OpenSpool variant named **OpenSpool U1 Extended Format**.

Minimum payload:

```json
{
  "protocol": "openspool",
  "type": "PLA",
  "color_hex": "#FF6600"
}
```

Extended payload:

```json
{
  "protocol": "openspool",
  "version": "1.0",
  "brand": "Generic",
  "type": "PLA",
  "subtype": "Basic",
  "color_hex": "#FF6600",
  "additional_color_hexes": ["00FF00"],
  "alpha": 255,
  "min_temp": 200,
  "max_temp": 220,
  "bed_min_temp": 55,
  "bed_max_temp": 60,
  "diameter": 1.75,
  "weight": 1000
}
```

OpenSpool U1 Extended mapping profile:

| OpenSpool field | `filament_detect/set.info` | `print_task_config` target | Rule |
|---|---|---|---|
| `brand` | `VENDOR` | `filament_vendor[channel]` | string passthrough |
| `type` | `MAIN_TYPE` | `filament_type[channel]` | recommended uppercase |
| `subtype` | `SUB_TYPE` | `filament_sub_type[channel]` | string passthrough |
| `color_hex` | `RGB_1` | `filament_color_rgba[channel]` | normalize to `RRGGBB`, convert to int; alpha appended later |
| `alpha` | `ALPHA` | `filament_color_rgba[channel]` | int passthrough; combined with `RGB_1` as `RRGGBBAA` |
| `min_temp` | `HOTEND_MIN_TEMP` | `N/A` | stored in `filament_detect` only |
| `max_temp` | `HOTEND_MAX_TEMP` | `N/A` | stored in `filament_detect` only |
| `bed_min_temp`/`bed_max_temp` | `BED_TEMP` | `N/A` | collapsed to single bed temp |
| `protocol` | `N/A` | `N/A` | parser validation only |
| `version` | `N/A` | `N/A` | no endpoint field |
| `additional_color_hexes` | `RGB_2`..`RGB_N` | `N/A` | mapped to `RGB_2`..`RGB_5`; `COLOR_NUMS` derived from total color count |
| `diameter` | `N/A` | `N/A` | no endpoint field |
| `weight` | `N/A` | `N/A` | no endpoint field |

### Example

Source OpenSpool U1 Extended payload:

```json
{
  "protocol": "openspool",
  "version": "1.0",
  "brand": "Generic",
  "type": "PLA",
  "subtype": "Basic",
  "color_hex": "#3366CC",
  "alpha": 128,
  "min_temp": 205,
  "max_temp": 225,
  "bed_min_temp": 55,
  "bed_max_temp": 60
}
```

Call 1 (external bridge/client transformation and write):

```bash
curl -s http://<host>/printer/filament_detect/set \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": 0,
    "info": {
      "VENDOR": "Generic",
      "MAIN_TYPE": "PLA",
      "SUB_TYPE": "Basic",
      "RGB_1": 3368652,
      "ALPHA": 128,
      "HOTEND_MIN_TEMP": 205,
      "HOTEND_MAX_TEMP": 225,
      "BED_TEMP": 55
    }
  }'
```

Call 1 response:

```json
{
  "state": "success"
}
```

Call 2 (read `filament_detect`):

```bash
curl -s 'http://<host>/printer/objects/query?filament_detect'
```

Call 2 response (relevant subset):

```json
{
  "result": {
    "status": {
      "filament_detect": {
        "info": [
          {
            "VENDOR": "Generic",
            "MAIN_TYPE": "PLA",
            "SUB_TYPE": "Basic",
            "RGB_1": 3368652,
            "ALPHA": 128,
            "ARGB_COLOR": 2150852300,
            "HOTEND_MIN_TEMP": 205,
            "HOTEND_MAX_TEMP": 225,
            "BED_TEMP": 55,
            "OFFICIAL": true
          }
        ]
      }
    }
  }
}
```

Call 3 (read `print_task_config`):

```bash
curl -s 'http://<host>/printer/objects/query?print_task_config'
```

Call 3 response (relevant subset):

```json
{
  "result": {
    "status": {
      "print_task_config": {
        "filament_vendor": ["Generic"],
        "filament_type": ["PLA"],
        "filament_sub_type": ["Basic"],
        "filament_color_rgba": ["3366CC80"],
        "filament_official": [true]
      }
    }
  }
}
```

Transformation trace for this example:

| Source | Transformed value | Target field |
|---|---|---|
| `brand = "Generic"` | `VENDOR = "Generic"` | `filament_detect.info[0].VENDOR` |
| `type = "PLA"` | `MAIN_TYPE = "PLA"` | `filament_detect.info[0].MAIN_TYPE` |
| `subtype = "Basic"` | `SUB_TYPE = "Basic"` | `filament_detect.info[0].SUB_TYPE` |
| `color_hex = "#3366CC"` | `RGB_1 = 3368652` | `filament_detect.info[0].RGB_1 = 3368652` |
| `alpha = 128` | `ALPHA = 128` | `filament_detect.info[0].ALPHA = 128` |
| derived | `ARGB_COLOR = (0x80 << 24) \| 0x3366CC` | `filament_detect.info[0].ARGB_COLOR = 2150852300` |
| `min_temp = 205` | `HOTEND_MIN_TEMP = 205` | `filament_detect.info[0].HOTEND_MIN_TEMP` |
| `max_temp = 225` | `HOTEND_MAX_TEMP = 225` | `filament_detect.info[0].HOTEND_MAX_TEMP` |
| `bed_min_temp = 55`, `bed_max_temp = 60` | `BED_TEMP = 55` | `filament_detect.info[0].BED_TEMP` |
| `OFFICIAL` rule | non-empty `info` | `filament_detect.info[0].OFFICIAL = true` |
| `RGB_1 + ALPHA` | `3366CC80` | `print_task_config.filament_color_rgba[0]` |
| mirrored identity | `Generic PLA Basic` | `print_task_config.filament_vendor/type/sub_type[0]` |

## References

Repository overlays:

- `overlays/firmware-extended/13-rfid-support/root/home/lava/klipper/klippy/extras/filament_protocol_ndef.py`
- `overlays/firmware-extended/13-rfid-support/patches/02-add-ndef-protocol.patch`
- `overlays/firmware-extended/13-rfid-support/patches/05-add-filament-detect-set-endpoint.patch`

Klipper source code:

- `/home/lava/lava/klipper/klippy/extras/filament_detect.py`
- `/home/lava/lava/klipper/klippy/extras/filament_protocol.py`
- `/home/lava/lava/klipper/klippy/extras/print_task_config.py`
