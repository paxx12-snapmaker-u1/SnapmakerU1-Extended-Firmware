# 42-feature-notifications: Push Notifications (pushraker harness)

Print, progress, fault and filament notifications with images, sent through
[Apprise](https://github.com/caronc/apprise) to ntfy, Gotify, Pushover, Telegram,
Discord, email or a webhook. The notifications themselves are
[pushraker](https://github.com/noon92/pushraker), a Moonraker component for
any Klipper printer, published on PyPI. This overlay is the harness that
puts it on the Snapmaker U1: it is not in the firmware image, it is
installed when the user enables it (Firmware Config > Settings >
Monitoring & Debugging > Push Notifications).
User documentation: [docs/notifications.md](../../../docs/notifications.md).

## Layout

| Path | What |
| --- | --- |
| `root/usr/local/bin/pushraker-pkg` | `enable` (venv under `/oem/apps/pushraker`, `pip install pushraker`, wire into Moonraker and Klipper, move old settings over, restart), `disable`, `wire` (service start), `upgrade`, `status` |
| `root/usr/local/share/pushraker/version` | The oldest pushraker release the enable step accepts |
| `root/etc/hooks/moonraker.d/20-pushraker.sh` | Re-wires at Moonraker start (the shim in Moonraker's components folder is on the root filesystem, which firmware upgrades replace) |
| `root/etc/nginx/fluidd.d/pushraker.conf` | `/notifications/` serves the package's settings page |
| `root/usr/local/share/firmware-config/functions/62_settings_notifications.yaml` | The Enabled/Disabled setting and the Push Notifications link |

## What pushraker puts on the printer

- `/home/lava/moonraker/moonraker/components/push_notifications.py`: a shim
  that loads the package from the venv.
- `extended/moonraker/pushraker.cfg`: `[push_notifications]` with the U1
  specifics (`name: U1`, the Snapmaker error-code index URL).
- `extended/klipper/pushraker.cfg`: the print hooks (`_PRINT_END_NTFY`,
  `_CANCEL_PRINT_NTFY`), driven by `SET_GCODE_VARIABLE` from the component.
- `printer_data/config/pushraker.conf` (settings) and
  `printer_data/pushraker.secrets` (tokens, 0600).

Settings from an earlier firmware's `extended2.cfg [notifications]` and
`moonraker.secrets [notifications]` are moved over the first time.

## Tests

pushraker's own: `python tests/test_pushraker.py` in its repository.
