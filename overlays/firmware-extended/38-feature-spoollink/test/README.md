# SpoolLink Tests

Offline checks for the SpoolLink Moonraker component. No printer, Spoolman instance, or
Moonraker install is needed — the component is loaded directly with stubbed Moonraker
services.

```bash
python3 active_spool_test.py
```

Covers what SpoolLink does when Moonraker's active spool is set by a client. Moonraker
tracks one active spool while the U1 has four channels, so a frontend that sets it can
say which spool but not where; see
[Spoolman Integration Design](../../../../docs/design/spoolman.md#active-spool).

## Live Checks

Runs against a printer that has Spoolman enabled, over Moonraker's HTTP API:

```bash
python3 spoolman_live_test.py <host>          # read-only
python3 spoolman_live_test.py <host> --apply  # also exercises the write paths
```

Read-only by default: it reports Klipper and Spoolman state, whether the `spoollink`
component and the `SET_SPOOL_ID` macro are present, whether AFC-Lite is loaded, and the
current spool mapping.

`--apply` sets a spool the way Fluidd's Spoolman panel sets it and checks that it lands
on the channel of the toolhead in use rather than being reverted, then checks that
`SET_SPOOL_ID` resolves a channel from `LANE=E<n>`, from `CHANNEL=<n>`, and rejects an
unrecognised lane. The original spool ids and active spool are restored afterwards, and
nothing runs while a print is active.

Options: `--spool <id>` to choose the spool to test with, `--channel <n>` to choose the
channel for the `SET_SPOOL_ID` tests, `--api-key <key>` if Moonraker requires one.

No RFID tag is needed - both paths under test are the ones that work without one.
Binding a card to a spool, per-toolhead behaviour across a tool change, and usage
tracking during a print still need a physical check.
