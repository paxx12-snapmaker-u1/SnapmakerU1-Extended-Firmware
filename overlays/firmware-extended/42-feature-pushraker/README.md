# 42-feature-pushraker: Push Notifications (pushraker harness)

Print, progress, fault and filament notifications with images, sent through
[Apprise](https://github.com/caronc/apprise) to ntfy, Gotify, Pushover, Telegram,
Discord, email or a webhook. The notifications themselves are
[pushraker](https://github.com/noon92/pushraker), a Moonraker component for
any Klipper printer, published on PyPI. This overlay builds one pinned
release into the firmware image and wires it in; a new firmware brings a
new pushraker. Notifications are off until the user sets them up on its page
(Firmware Config > Links > Push Notifications).
User documentation: the [Push Notifications guide](https://github.com/noon92/pushraker/blob/main/docs/guide.md) in pushraker's repository.

## Layout

| Path | What |
| --- | --- |
| `scripts/01-install-pushraker.sh` | `pip install pushraker==<pinned> --hash=sha256:<pinned>` into the image (pip adds Apprise, requests and Pillow where missing); links the package's settings page and Klipper macros under `/usr/local/share/pushraker/`, and the default includes into the extended config |
| `root/home/lava/moonraker/moonraker/components/pushraker.py` | One-line shim: `[pushraker]` loads the package's component |
| `root/usr/local/share/pushraker/moonraker.cfg` | `[pushraker]` with the U1 specifics (`name: U1`, the Snapmaker error-code index URL) |
| `root/etc/nginx/fluidd.d/pushraker.conf` | `/notifications/` serves the package's settings page |
| `root/usr/local/share/firmware-config/functions/62_links_notifications.yaml` | The Push Notifications link |

## On the printer

- `extended/moonraker/05_pushraker.cfg` and `extended/klipper/05_pushraker.cfg`:
  links to `/usr/local/share/pushraker/`, so they follow the firmware. The
  Klipper file holds the [print hooks](../../../docs/klipper_hooks.md)
  (`_PRINT_END_NTFY`, `_CANCEL_PRINT_NTFY`), driven by `SET_GCODE_VARIABLE`
  from the component.
- `printer_data/config/pushraker.conf` (settings) and
  `printer_data/pushraker.secrets` (tokens, 0600), written by the page.

## Tests

pushraker's own: `python tests/test_pushraker.py` in its repository.
