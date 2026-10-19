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
belongs to exactly one spool or filament, and its UID is normalized to uppercase hex
without separators, the same form `card_uids` uses.

| Operation | Request |
|-----------|---------|
| Look up | `GET api/v1/spool?tag={uid}` |
| Link | `POST api/v1/spool/{id}/tag` with `{"uid": "{uid}"}`; `409` with `spool_id` if another spool holds it |
| Unlink | `DELETE api/v1/spool/{id}/tag/{uid}` |

Spool responses carry the linked tags as `tags: [{"uid": ...}]`.

SpoolLink uses exactly one store, picked from `GET api/v1/info`: native tags when the
version is `0.27.0` or newer, `card_uids` otherwise. The version is read at startup and
before each card resolve; a failed check keeps the previous choice.

## Migration from `card_uids`

The first time native tags are selected in a Moonraker run, SpoolLink lists every spool
(`limit=1000&allow_archived=true`) and, for each `card_uids` entry:

- links it as a native tag, then removes it from `card_uids`;
- drops it from `card_uids` without linking if another spool already holds the tag
  (`409`), as the tag wins;
- leaves it in `card_uids` when the same UID is on several spools, or linking fails,
  and logs a warning. The migration runs again on the next start.

Card resolves wait for the migration to finish. If the spool list cannot be fetched, the
migration is retried before the next card resolve.

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
   creating them via `POST api/v1/field/{entity}/{key}` if missing (`card_uids` only when
   native tags are not in use). Before that it reads `GET api/v1/info` to pick the
   [card UID store](#spoolman-native-tags), migrating `card_uids` to tags if needed.
3. Klipper (or AFC) calls `spoollink_resolve_spool` with `channel`, `spool_id`, and/or `card_uid`.
4. `spoollink` resolves the spool from Spoolman:
   - By ID: `GET api/v1/spool/{id}`
   - By card UID: `GET api/v1/spool?tag={uid}` with native tags, otherwise
     `GET api/v1/spool?limit=1000` filtered client-side on `extra.card_uids`
     (comma-separated, JSON-encoded uppercase hex UIDs).
5. If both `spool_id` and `card_uid` are given, any other spool that carries the same
   UID has it removed first — a UID can only belong to one spool at a time. Then, if the
   card is not yet on the spool, `spoollink` links it: `POST api/v1/spool/{id}/tag` with
   native tags, otherwise `PATCH api/v1/spool/{id}` with
   `{"extra": {"card_uids": "\"UID1,UID2\""}}` (JSON-encoded string, as required by
   Spoolman's custom field API).
6. On success, `spoollink` pushes the resolved filament info into Klipper by calling the
   `spoollink/set` endpoint (registered by the Klipper `[spoollink]` router), which merges
   it onto `filament_protocol.FILAMENT_INFO_STRUCT` and applies it to `print_task_config`.
7. When SpoolLink `force_generic_vendor` is enabled, non-Snapmaker filament metadata
   sent to Klipper uses `Generic` as the vendor and clears the variant. The resolved
   Spoolman object, spool ID, card UID, cache data, active spool tracking, and usage
   reporting remain unchanged.
8. Klipper stores the metadata in `print_task_config` and notifies subscribers.
   `AFC_lane.get_status()` surfaces `spool_id` to Fluidd/Mainsail.

