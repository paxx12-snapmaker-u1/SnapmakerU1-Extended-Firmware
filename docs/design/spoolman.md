---
title: Spoolman Integration Design
---

# Spoolman Integration Design

# Spoolman Custom Fields

| Entity | Key | Purpose |
|--------|-----|---------|
| `spool` | `card_uids` | Comma-separated uppercase hex NFC UIDs associated with the spool |
| `filament` | `variant` | Filament subtype / variant (e.g. "Silk", "Matte") |

Both fields use Spoolman's double-serialised string convention: the inner value is
JSON-encoded before storage, so a single UID `"AABBCCDD"` is stored as `"\"AABBCCDD\""`.
`spoollink` decodes these on read and re-encodes on write.

## `card_uids` Custom Field

NFC tag UIDs are stored in a Spoolman custom field named `card_uids` on the spool entity.

### Field definition (created on first connection test)

```json
{
  "key": "card_uids",
  "name": "Card UIDs",
  "entity_type": "spool",
  "field_type": "text",
  "order": 1,
  "default_value": "\"\""
}
```

### Wire format

The `extra.card_uids` value is a **JSON-encoded string** — the string value itself is
wrapped in JSON quotes by the client before sending, so it arrives as a JSON string
containing another JSON string:

```
extra.card_uids = "\"AABBCCDD,11223344\""
```

When decoded, the inner string is a comma-separated list of uppercase hex UIDs:

```
AABBCCDD,11223344
```

### Encoding rule

Before writing, the comma-separated UID string must be JSON-encoded:

```
jsonEncode("AABBCCDD,11223344") → "\"AABBCCDD,11223344\""
```

### Decoding rule

On read, strip the outer JSON quotes if present; handle both encoded and raw forms:

```
raw = "\"AABBCCDD,11223344\""
decoded = AABBCCDD,11223344
uids = ["AABBCCDD", "11223344"]
```

### PATCH body for `card_uids`

```json
{
  "extra": {
    "card_uids": "\"AABBCCDD,11223344\""
  }
}
```

---

## UID Format

NFC tag UIDs are formatted as **uppercase hex with no separators**:

```
AABBCCDD        (4-byte UID)
04A1B2C3D4E5F6  (7-byte UID)
```

---

## Sync Logic ("Add to current, remove from others")

When an NFC tag is scanned or assigned to a spool:

1. **Fetch** the target spool via `GET api/v1/spool/{id}`.
2. **Append** the tag UID to `extra.card_uids` if not already present.
3. **Write** the updated UID list via `PATCH api/v1/spool/{id}`.
4. **Search** for other spools that contain the same UID by fetching all spools
   (`limit=1000&allow_archived=true`) and filtering client-side on `card_uids`.
5. **Remove** the UID from each other spool's `card_uids` and write back via `PATCH`.

---

## `variant` Custom Field (Filament)

The filament variant (e.g. "Silk", "Matte") is stored in a Spoolman custom field named
`variant` on the filament entity.

### Field definition (created on first connection test)

```json
{
  "key": "variant",
  "name": "Variant",
  "entity_type": "filament",
  "field_type": "text",
  "order": 1,
  "default_value": "\"\""
}
```

The value follows the same JSON-encoded string convention as `card_uids`:

```
extra.variant = "\"Silk\""
```

### Create Filament body with variant

```json
{
  "name": "Galaxy Black Silk",
  "material": "PLA",
  "extra": {
    "variant": "\"Silk\""
  }
}
```

# Spoolman Native Tags

Spoolman v0.27.0 added NFC/RFID tags keyed on the hardware UID
([Donkie/Spoolman#1096](https://github.com/Donkie/Spoolman/pull/1096)). A tag
belongs to at most one spool or filament, and UIDs are normalised to uppercase hex
without separators, the same form `card_uids` uses.

| Operation | Request |
|-----------|---------|
| Look up | `GET api/v1/spool?tag={uid}` |
| Link | `POST api/v1/spool/{id}/tag` with `{"uid": "{uid}"}`; `409` with `spool_id` if another spool holds it |
| Unlink | `DELETE api/v1/spool/{id}/tag/{uid}` |

Spool responses carry the linked tags as `tags: [{"uid": ...}]`.

## Choosing the store

Two `[spoollink]` Moonraker options (rendered from `extended2.cfg` `[spoolman]`,
both default `true`) select where card UIDs are kept:

- `use_spoolman_uid` - native tags. Effective only when `GET api/v1/info` reports
  version `0.27.0` or newer. The version is checked at startup and again before each
  card resolve, so upgrading Spoolman needs no printer restart.
- `use_spoollink_uid` - the `card_uids` custom field.

With both enabled, lookups query both stores. A native tag match is authoritative:
any other spool still listing the UID in `card_uids` is treated as stale and the UID
is removed from it. Without a native match, `card_uids` matches are used as before,
and more than one is reported as an error. Binding writes every enabled store, so a
card resolved only through `card_uids` is linked as a native tag on its next scan.
When linking returns `409` for another spool, the tag is unlinked there and linked
again.

# `spoollink` Component Flow

`spoollink` is a Moonraker component (`moonraker/components/spoollink.py`), loaded when a
`[spoollink]` section is present in the Moonraker config. It runs in Moonraker's event loop
and uses the built-in `http_client`, `klippy_apis`, and `spoolman` components directly, so
it does not maintain its own WebSocket connection.

1. On construction, `spoollink` calls `server.register_remote_method("spoollink_resolve_spool", ...)`;
   Moonraker re-registers it with Klipper on every Klippy `ready`, so the binding survives
   Klipper restarts.
2. On startup (`component_init`), `spoollink` calls `GET api/v1/field/spool` and
   `GET api/v1/field/filament` to verify the `card_uids` and `variant` custom fields exist,
   creating them via `POST api/v1/field/{entity}/{key}` if missing (`card_uids` only
   when `use_spoollink_uid` is enabled). It also reads `GET api/v1/info` to detect
   [native tag](#spoolman-native-tags) support.
3. Klipper (or AFC) calls `spoollink_resolve_spool` with `channel`, `spool_id`, and/or `card_uid`.
4. `spoollink` resolves the spool from Spoolman:
   - By ID: `GET api/v1/spool/{id}`
   - By card UID: `GET api/v1/spool?tag={uid}` when native tags are in use, and/or
     `GET api/v1/spool?limit=1000` filtered client-side on `extra.card_uids`
     (comma-separated, JSON-encoded uppercase hex UIDs).
5. When a `card_uid` resolves to a spool (via `spool_id`, or the card itself), the card
   is bound to it in every enabled store that lacks it: a native tag via
   `POST api/v1/spool/{id}/tag`, and/or `card_uids` via `PATCH api/v1/spool/{id}` with
   `{"extra": {"card_uids": "\"UID1,UID2\""}}` (JSON-encoded string, as required by
   Spoolman's custom field API). Any other spool that already carries the same UID has it
   removed first — a UID can only belong to one spool at a time.
6. On success, `spoollink` pushes the resolved filament info into Klipper by calling the
   `spoollink/set` endpoint (registered by the Klipper `[spoollink]` router), which merges
   it onto `filament_protocol.FILAMENT_INFO_STRUCT` and applies it to `print_task_config`.
7. When SpoolLink `force_generic_vendor` is enabled, non-Snapmaker filament metadata
   sent to Klipper uses `Generic` as the vendor and clears the variant. The resolved
   Spoolman object, spool ID, card UID, cache data, active spool tracking, and usage
   reporting remain unchanged.
8. Klipper stores the metadata in `print_task_config` and notifies subscribers.
   `AFC_lane.get_status()` surfaces `spool_id` to Fluidd/Mainsail.

