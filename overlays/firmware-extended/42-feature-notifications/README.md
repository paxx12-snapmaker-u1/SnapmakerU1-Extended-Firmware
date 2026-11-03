# 42-feature-notifications: Push Notifications (pushraker harness)

Print, progress, fault and filament notifications with images, sent through
[Apprise](https://github.com/caronc/apprise) to ntfy, Gotify, Pushover, Telegram,
Discord, email or a webhook. The notifications themselves are
[pushraker](https://github.com/noon92/pushraker), a Moonraker component for
any Klipper printer, published on PyPI. This overlay builds one pinned
release into the firmware image and wires it in; a new firmware brings a
new pushraker. It is on by default and set up from its own page
(Firmware Config > Links > Push Notifications).
User documentation: [docs/notifications.md](../../../docs/notifications.md).

## Layout

| Path | What |
| --- | --- |
| `scripts/01-install-pushraker.sh` | `pip install pushraker==<pinned>` into the image (`--no-deps`: Apprise, requests and Pillow are already there); links the package's settings page and Klipper macros under `/usr/local/share/pushraker/`, and the default includes into the extended config |
| `root/home/lava/moonraker/moonraker/components/pushraker.py` | One-line shim: `[pushraker]` loads the package's component |
| `root/usr/local/share/pushraker/moonraker.cfg` | `[pushraker]` with the U1 specifics (`name: U1`, the Snapmaker error-code index URL) and the move from this firmware's earlier Push Notifications form |
| `root/etc/nginx/fluidd.d/pushraker.conf` | `/notifications/` serves the package's settings page |
| `root/usr/local/share/firmware-config/functions/62_settings_notifications.yaml` | The Push Notifications link |

## On the printer

- `extended/moonraker/05_pushraker.cfg` and `extended/klipper/05_pushraker.cfg`:
  links to `/usr/local/share/pushraker/`, so they follow the firmware. The
  Klipper file holds the [print hooks](../../../docs/klipper_hooks.md)
  (`_PRINT_END_NTFY`, `_CANCEL_PRINT_NTFY`), driven by `SET_GCODE_VARIABLE`
  from the component.
- `printer_data/config/pushraker.conf` (settings) and
  `printer_data/pushraker.secrets` (tokens, 0600), written by the page.

At the first start, settings from `extended2.cfg [notifications]` and
`moonraker.secrets [notifications]` are moved over, and the files the old
form generated (`extended/{moonraker,klipper}/notifications.cfg`) are
renamed to `.old`.

## Tests

pushraker's own: `python tests/test_pushraker.py` in its repository.
