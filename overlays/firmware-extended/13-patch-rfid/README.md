# 13-rfid-support (Internal API)

This overlay extends U1 RFID behavior in Klipper extras and is intended as an **internal integration API**.

## Scope

Patch set in `overlays/firmware-extended/13-rfid-support/patches`:

- `01-add-ntag215-support.patch`
  - Extends `fm175xx_reader.py` card handling for NTAG cards.
- `02-add-ndef-protocol.patch`
  - Extends `filament_detect.py` parsing to support NDEF payloads.
- `03-fm175xx-reader-enabled-guard.patch`
  - Adds `self.enabled = config.getboolean('enabled', True)` and early `return` in `FM175XXReader.__init__`.
  - Set `enabled: false` in `[fm175xx_reader]` to skip all hardware init and event registration.
- `04-filament-detect-reader-enabled-guard.patch`
  - Guards `filament_detect.py` against a disabled reader:
    - `_ready`: sets `_fm175xx_reader = None` when `enabled` is false.
    - `request_update_filament_info`: moves state update before the reader `None` check.
    - `request_clear_filament_info`: falls back to clearing via `_filament_info_update` when reader is `None`.
    - `cmd_FILAMENT_DT_SELF_TEST`: raises early error when reader is disabled.
- `05-add-filament-detect-set-endpoint.patch`
  - Adds webhook endpoint `filament_detect/set`.
- `08-pass-uid-on-m1-auth-failure.patch`
  - When M1 auth fails, sets `card_data` to the UID bytes from the anticollision/SELECT phase
    (`self.__picc_a.UID[0:4]`) so that `CARD_UID` and `CARD_TYPE` are still populated via the
    existing patch-07 handler in `filament_detect.py`.

## API Contract

See [docs/design/filament_detect.md](../../../docs/design/filament_detect.md) for the full field
reference, endpoint contract, and OpenSpool mapping.

`filament_detect.state[channel] == 1` signals that the printer is requesting an update for that
channel. Clients read state via `/printer/objects/query?filament_detect`.

## Compatibility Notes

- Target files in this tree are often CRLF. Patch application is expected after LF normalization.
- `pre-scripts/01_klippy_fix_lf.sh` is used to run `dos2unix` on relevant files before patching.

## Stability

- This is internal and may change without backward-compatibility guarantees.
- Clients are expected to understand and track the running software version.
- Command names, endpoint shape, and strict typing are tied to the overlay version in use.
