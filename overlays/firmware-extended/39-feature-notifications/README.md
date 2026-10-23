# 39-feature-notifications: Push Notifications

Print, progress, fault and filament notifications with images, sent through
[Apprise](https://github.com/caronc/apprise) to ntfy, Gotify, Pushover, Telegram,
Discord, email or a webhook. Configured from Firmware Config
(**Settings > Monitoring & Debugging > Push Notifications > Configure**).
User documentation: [docs/notifications.md](../../../docs/notifications.md).

## Layout

| Path | What |
| --- | --- |
| `root/home/lava/moonraker/moonraker/components/push_notifications.py` | The Moonraker component: events, send order and retries, Spoolman checks, print-by-object |
| `…/push_notifications_email.py` | The HTML email card |
| `…/push_notifications_discord.py` | Discord senders as Apprise plugins (`discordhook://`, `discorddm://`) |
| `…/push_notifications_images.py` | The drawn spool pictures (Pillow) |
| `root/usr/local/bin/notifications-config.py` | Settings: `get`, `save`, `test`, `status`, `render-moonraker`, `render-klipper` |
| `root/usr/local/share/firmware-config/html/forms/notifications.html` | The settings form, shown in Firmware Config's modal |
| `root/usr/local/share/firmware-config/functions/62_settings_notifications.yaml` | The Firmware Config setting |
| `root/etc/hooks/{klipper,moonraker}.d/20-notifications.sh` | Regenerate the config files at service start |
| `scripts/01-install-pillow.sh` | Build step: Pillow |
| `test/notifications_test.py` | Offline regression tests |

## Where things live on the printer

- Settings: `extended2.cfg` `[notifications]` (no secrets).
- Secrets: `/home/lava/printer_data/moonraker.secrets` `[notifications]`,
  mode 0600, referenced from the generated config as
  `{secrets.notifications.<name>}`.
- Generated: `extended/moonraker/notifications.cfg` (the component and its
  Apprise URLs) and `extended/klipper/notifications.cfg` (the print-end
  hooks), rewritten by `save` and by the service hooks.

## Tests

```sh
python3 overlays/firmware-extended/39-feature-notifications/test/notifications_test.py
```

Needs Python 3.9+, `apprise` and `Pillow`; no printer or network. The
component runs against a stand-in Moonraker, Spoolman and fake services; the
settings script against a temporary `extended2.cfg`.
