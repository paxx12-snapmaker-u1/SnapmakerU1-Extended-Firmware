---
title: Push Notifications
---

# Push Notifications

Print, progress and error notifications with camera snapshots, sent to your
phone through [ntfy](https://ntfy.sh) (the public `ntfy.sh` server or your own),
[Pushover](https://pushover.net), [Telegram](https://telegram.org),
[Discord](https://discord.com), and/or by email (Gmail, Brevo or any SMTP
server), and/or to a webhook for home automation.

![Notifications settings](screenshots/notifications.png)

## What It Provides

| Notification | Image | Notes |
|---|---|---|
| ▶️ Print started | Model preview | The slicer thumbnail, and the slicer's estimated print time and ETA |
| 🔍 1st / 2nd / 3rd layer complete | Camera | Choose after which layer (default: 2nd) |
| ⏳ 25% / 50% / 75% complete | Camera | Same percentage as Fluidd and the Snapmaker app, with time left and ETA |
| ⏸️ Paused / ▶️ Resumed | Camera | Resumed includes time left and ETA |
| ✅ Print complete | Camera | Includes print time |
| 🚫 Cancelled | Camera | |
| ⚠️ Error | Camera | Faults that pause or stop a print, e.g. filament runout or a dirty bed |
| ⚠️ Warning | Camera | Faults that don't stop a print, e.g. a toolhead contact warning |
| 🛑 Klipper shutdown | Camera | Heater, thermistor and MCU faults, emergency stop |
| 🔌 Power loss recovery | Camera | Sent after boot when an interrupted print can be resumed, with a photo of the part (the camera gets up to 45 seconds to start) |
| ▶️ Resumed after power loss | Camera | Sent instead of *Print started* when a recovered print continues (uses the *Resumed* settings), with time left and ETA |
| 🧵 Not enough filament | Spools | Spoolman: when a toolhead the print uses has less filament than the rest of the print needs (red), or only just enough, within 20 g or 5% (orange: "may be insufficient"). Checked at print start, as one notification for all toolheads at risk, and again during the print when a toolhead gets worse. If refill is on, says which spool the printer can refill from, how much it has, whether it's an exact match or only a compatible spool, and whether together they're enough, or that no refill spool was found. A loaded spool that Spoolman shows at 0 g or less is reported once, at print start, as "amount of filament left is unknown" (it isn't used for any other check). Nothing is sent when all spools are fine |
| 🧵 Spool running low | Spool | Spoolman: when a spool is down to a set % of a full spool (default 10%) or a set weight (default 50 g), whichever comes first. Sent once per spool, until it is refilled or replaced; time to buy a new one. Spools that run low at the same time (e.g. at print job start) come in one notification. With refill on, it shows the spool the printer would refill from (or that there is none) |
| 🔄 Switched to backup spool | Spools | Spoolman: when the printer refills a filament from another toolhead mid-print, with the new spool's remaining weight |

Each notification can be sent by push, by email, or both, with its own
priority (push) and image setting. For example: progress by push only, and
errors by push and email. By default, faults and power loss are also emailed.

- **Time left and ETA**: Calculated like Fluidd's default estimate (print
  time so far, divided by file progress). This also holds after a
  power-loss recovery, which keeps the print time and progress of the
  interrupted print. ETAs are shown in the time zone of the browser you last
  saved the settings from, since the printer's clock runs on UTC.
- **Error codes**: Error, warning and shutdown notifications include the
  Snapmaker error code in the same format the printer uses
  (`level-module-index-code`, e.g. `0002-0532-0000-0001: detected dirty bed`)
  with the same title and advice the printer's screen shows for that code
  (e.g. `Possible Spaghetti Defect Detected`), and an **Error Index** link to the
  [U1 error code reference](https://wiki.snapmaker.com/en/snapmaker_u1/troubleshooting/u1_error_codes).
- **Snapshots before parking**: For *Print complete* and *Cancelled*, the
  snapshot is taken once the nozzle has lifted 15 mm off the part, while the
  printer's own end/cancel moves carry on, so the image shows the part and the
  nozzle doesn't stop over it and ooze onto it. There is no lift if Z is
  not homed, and the lift is reduced near the top of the build volume so the
  stock end/cancel moves stay in range. For *Paused*, the snapshot is taken
  3 seconds after the pause starts, once the nozzle has moved off the part.
- **Images never block a notification**: Images are uploaded as files, so your
  phone doesn't need to reach the printer. If the camera is unavailable, the
  notification is sent without the image.
- **Spoolman**: The Spoolman notifications appear when the
  [Spoolman integration](spoolman.md) is enabled. They use each toolhead's
  assigned spool and the slicer's per-filament weights, mapped to toolheads
  the way the print was started (including touchscreen remapping). With
  refill on, backup spools count as "any compatible spool" (same material)
  or "same exact spool" (same vendor, material, type and colour), like the
  printer's own setting. Spools are checked once a minute while printing.
  What a toolhead still needs is measured, not guessed: the slicer's
  weight for its filament minus what Spoolman says its spools (including
  a refill spool it moved to) used since the print job started. This
  needs Spoolman to be tracking usage (SpoolLink does); if nothing was
  used 5% into the print job, the checks during the print job are skipped
  and only the check at the start runs. After a power-loss recovery, what
  was used before the power loss isn't known, so only the running-low
  alert is sent.
  Toolheads are numbered 1-4, as on the printer. The pictures show each
  spool in its Spoolman colour, filled to how much is left (in 10% steps of
  a full spool), in red (not enough), orange (may be insufficient or
  running low) or green (enough).
- **Long messages**: Pushover allows 1024 characters per message, ntfy
  about 4 KB, and Telegram and Discord about 4000 characters. A not-enough-filament notification that doesn't fit is split
  into several (e.g. "(1/2)", "(2/2)"), never in the middle of a toolhead,
  each with the pictures of its own toolheads. Only if one toolhead's text
  doesn't fit on its own are the file name and spool names shortened with
  "…". Email has no limit.
- **File names** are shown without the `.gcode` extension.
- **Retries**: When the service answers with an error, or can't be reached,
  the notification is retried after 6, 10 and 30 seconds (4 attempts), then
  given up. Push and email are sent and retried separately. If the printer
  has no network at all (e.g. Wi-Fi not connected
  yet after a power loss), each attempt first waits up to 10 minutes for it;
  that wait doesn't count as an attempt. Each attempt waits up to 60 seconds
  for the server's answer before it counts as failed, so a slow image upload
  isn't sent twice. The
  last attempt is sent without the image, in case the image is what the
  server rejects. Notifications are sent one at a time per service, so while
  one is retrying the next ones wait and arrive in order.
- **Links**: ntfy notifications get up to three buttons: **Web UI** and
  **Live Camera** (with a printer URL set) and, for faults, **Error Index**.
  Pushover allows one link: **Web UI** when a printer URL is set, otherwise
  **Error Index** on fault notifications. Emails, Telegram and Discord have
  the same links as ntfy, in the text.

## Choosing a Service

| | Setup | Privacy |
|---|---|---|
| **ntfy.sh** (public server) | None: pick a topic and subscribe to it in the [ntfy app](https://docs.ntfy.sh/subscribe/phone/) | **Public**: anyone who knows or guesses the topic can read the notifications, including images and file names |
| **ntfy** (your own server) | [Self-host ntfy](https://docs.ntfy.sh/install/) | Private, with access control |
| **Pushover** | Pushover account and app (30-day free trial, then a one-time purchase per platform), plus an application token | Private to your account |
| **Telegram** | A bot of your own, made in Telegram in a minute (see [Telegram](#telegram)) | Private chat with your bot, or a group you choose |
| **Discord (direct message)** | A bot of your own in the Discord Developer Portal, added to a server you are in (see [Discord](#discord)) | Direct messages to you |
| **Discord (server channel)** | A webhook for a channel on a server you manage (see [Discord](#discord)) | Everyone who can see the channel |

To use the public server, choose **ntfy** and click **Use public server** next
to the Server URL. Firmware Config asks you to confirm the privacy warning and
keeps showing it under the server field. The public server is fine for
testing; for everyday use, setting up your own ntfy server is a good idea. On
the public server, use a long, unguessable topic: the **Random** button
creates one, and topics shorter than 16 characters are rejected.

On Pushover, **High** and **Max** both use Pushover's high priority. Pushover's
emergency priority, which repeats until acknowledged, is not used.

Telegram and Discord have no priorities: **Min** and **Low** notifications
arrive without a sound, the others normally.

### Telegram

1. In Telegram, open [@BotFather](https://t.me/BotFather) and send `/newbot`.
   Choose a display name and a username ending in `bot` (e.g.
   `martha_u1_bot`).
2. BotFather replies with a **token** (`123456789:ABC...`). Paste it into
   **Bot token**.
3. Open your new bot (BotFather links to it) and press **Start**.
4. Leave **Chat ID** empty and click **Send Test** or **Save & Apply**: the
   chat is found from your Start message. Telegram keeps it for 24 hours; if
   it's older, send the bot any message and try again.

For a group instead (e.g. to share notifications), add the bot to the group,
send a message in the group, then save with **Chat ID** empty. A chat ID can
also be entered by hand: a number, `-100...` for a group or channel, or
`@channelname` for a public channel the bot posts in.

The image comes with the text under it. Messages longer than Telegram's
1024-character caption limit arrive as the image followed by the text.

### Webhook

A webhook can be added alongside the push service and email, for Home
Assistant, Node-RED, n8n or your own scripts. Check **Send every
notification to a webhook** and paste the URL. It gets every notification,
whatever the Push and Email columns say (the receiver picks by `event`),
each as an HTTP POST with a JSON body:

```json
{
  "version": "1.0",
  "title": "✅ U1 complete",
  "message": "Benchy\nPrint time: 1h 12m",
  "type": "success",
  "event": "complete",
  "printer": "U1",
  "web_ui": "http://u1/",
  "live_camera": "http://u1/webcam/webrtc",
  "attachments": [{"filename": "snapshot.jpg", "mimetype": "image/jpeg", "base64": "..."}]
}
```

`type` is `info`, `success`, `warning` or `failure`; `event` is the
notification (`started`, `first_layer`, `progress_25`, `paused`, `complete`,
`error`, `filament_low`, ...; `test` for Send Test). The links appear when a
printer URL is set, and `error_index` on faults. The URL's own query
parameters and a `user:password@` login are kept. Priority isn't sent.

### Discord

Discord has two options. Discord's bots can only message people they share a
server with, so both need a server: any server works, including an empty
one you create just for this (**+** in the server list, **Create My Own**).

**Direct message** (notifications come to you privately, from your own bot):

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and click **New Application**. Name it (e.g. `U1-martha`) and create it.
2. Go to **Bot** and click **Reset Token**, then **Copy**. Paste it into
   **Bot token**. The token is shown only once; if you lose it, reset it
   again.
3. Click **Save & Apply**. If the bot isn't in a server yet, the error
   message includes a link: open it, pick your server, and click
   **Authorize**. Then save again.
4. **Send to (user ID)** can stay empty: messages go to the account that made
   the bot. To send to someone else, enter their user ID (with **Developer
   Mode** on under **Settings > Advanced**, right-click them and choose
   **Copy User ID**). They must share a server with the bot.

If the test says the bot can't message you, check that the server you added
the bot to allows direct messages from its members (the server's name >
**Privacy Settings** > **Direct Messages**).

**Server channel** (notifications are posted in a channel):

1. In Discord, open the channel's **Edit Channel** (the gear icon) >
   **Integrations** > **Webhooks** > **New Webhook**.
2. Optionally rename it (notifications show the printer name either way),
   then click **Copy Webhook URL** and paste it into **Webhook URL**.
3. Optionally, a **Mention** for high and max priority notifications, so
   they notify you even if the channel is muted: your user ID, `role:` and a
   role ID, `everyone` or `here`.

Each Discord notification is one message with a coloured bar (green when
complete, orange for pauses, cancellations and spool warnings, red for
faults and not enough filament, grey otherwise) and the image.

### Email

Email is sent through an SMTP server, so it works with any email provider.
The form has presets:

| Provider | Server | Username | Password | From |
|---|---|---|---|---|
| **Gmail** | `smtp.gmail.com:587`, STARTTLS | Your Gmail address | An [app password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification), not your Google password | Your Gmail address |
| **Brevo** | `smtp-relay.brevo.com:587`, STARTTLS | Your SMTP login (`…@smtp-brevo.com`) | An SMTP key, under [SMTP & API](https://app.brevo.com/settings/keys/smtp) | A sender verified in Brevo |
| **Other** | Your provider's SMTP server; usually port 587 with STARTTLS or 465 with SSL/TLS | | | |

On **Brevo**, also check **Security > Authorised IPs**
(https://app.brevo.com/security/authorised_ips): if IP blocking is on, Brevo
rejects the printer with `525 Unauthorized IP address`. Allow your
network's public IP, or turn the blocking off (most home connections get a
new public IP from time to time, which would block email again).

Emails can go to up to 5 addresses. Each email is a card with a coloured bar for the kind of notification (green when complete, orange for pauses, cancellations and spool warnings, red for faults), the image inside it, and the links as buttons. Mail apps that don't show HTML get a plain-text version.

Emails about one print are one conversation in your email app: they share
the subject (printer and file name, e.g. `U1: Benchy`) and the notification
itself is the heading of the email. Faults join the print's conversation
while it prints and up to 2 minutes after it ends, and a power loss joins
the print it interrupted; other emails (e.g. an idle warning) have the
notification as their subject.

## Requirements

- For images on a **self-hosted ntfy** server, attachments must be enabled
  (`attachment-cache-dir` in the server's `server.yml`). Otherwise
  notifications arrive without images.
- For camera images, the **paxx12** camera service for the selected camera
  (the default for the internal camera, see [Camera Support](camera_support.md)).
- For *Layer complete*, the slicer must emit `SET_PRINT_STATS_INFO`
  layer information (the Snapmaker U1 Orca profile includes it).

## Setup

1. Open [Firmware Config](firmware_config.md) and go to
   **Settings > Monitoring & Debugging > Push Notifications**, then click
   **Configure**.
2. Check **Enable notifications** and choose the **Push service** (or
   **None** for email only):
   - **ntfy**: enter your **Server URL** (e.g. `http://192.168.1.10:8080`), or
     click **Use public server** for ntfy.sh. Keep the default **Topic** (`u1`),
     or enter or generate another, and
     subscribe to it in the ntfy app. On your own server, choose
     **Authentication**: none, an access token (`tk_...`), or username &
     password.
   - **Pushover**: enter your **User key** and the **Application API token**,
     and optionally a device name.
   - **Telegram**: see [Telegram](#telegram).
   - **Discord**: see [Discord](#discord).
3. For email, check **Send email notifications**, choose the **Email
   provider**, and enter the username, password, From and To (see
   [Email](#email)).
4. Optionally set a **Printer URL** for the links. On first setup it is filled in
   automatically: the Tailscale name if Tailscale is running, otherwise the
   local IP. Clear it for no links, or change it with:
   - **Use Tailscale** fills in the printer's Tailscale (MagicDNS) name, e.g.
     `http://u1`, which works from anywhere your phone is on the tailnet. It is
     available when [Tailscale](vpn.md) is enabled and logged in. The name is
     used rather than the Tailscale IP because the IP changes if Tailscale is
     turned off and on again in Firmware Config (the printer rejoins as a new
     device). The name needs MagicDNS, which is on by default.
   - **Use local IP** fills in the printer's LAN address. The links then only
     work while your phone is on the same network as the printer.
5. Choose which notifications you want (**Push** and/or **Email**), with
   priority and image.
6. Click **Send Test** to check the connection. It sends a test to each
   service you set up. On failure, the log shows the service's response
   (e.g. `forbidden, error=40301` for an ntfy token without write access to
   the topic, or `535` for a wrong email password).
7. Click **Save & Apply**.

Saving restarts Moonraker and/or Klipper only when the generated
configuration changed, and only when the printer is idle. While a print is
running (or if the printer state can't be checked), settings are saved but
services are not restarted. They apply after you restart Moonraker and
Klipper (**Actions**) or reboot.

## Security

- On `ntfy.sh`, notifications are public to anyone with the topic name (see
  above).
- On your own ntfy server, give the printer a dedicated user with
  **write-only** access to its topic, e.g.
  `ntfy access u1-printer u1-printer write-only`.
- For email, use an app password or SMTP key, which you can revoke on its
  own, rather than your account password.
- Telegram and Discord bot tokens and the Discord webhook URL let anyone
  who has them send messages as the bot. If one leaks, reset it (BotFather
  `/revoke`, Developer Portal **Reset Token**, or delete the webhook).
- Tokens and passwords are stored in `extended2.cfg` and in the generated
  Moonraker configuration. Anyone with access to Fluidd/Mainsail or the
  Moonraker API can read them (e.g. via the config file browser or
  `/server/config`). Enable **Web Authentication** under
  **Settings > Remote Access** if your network isn't trusted.

## Configuration File

Settings are stored in `extended2.cfg` under `[notifications]`:

```ini
[notifications]
enabled: true
provider: ntfy
server: https://ntfy.example.com
topic: u1-printer
auth: token
username:
pushover_user:
pushover_device:
telegram_chat:
discord_user:
discord_mention:
secret: tk_...
printer_url: http://100.64.0.10
camera: internal
name: U1
layers_complete: 2
email_enabled: true
email_host: smtp.gmail.com
email_port: 587
email_security: starttls
email_username: you@gmail.com
email_password: abcd efgh ijkl mnop
email_from: you@gmail.com
email_to: you@gmail.com
webhook_enabled: false
webhook_url:
# <notification>: on|off (push), <priority>, image|no_image, email|no_email
started: on, default, image, no_email
paused: on, default, image, no_email
error: on, max, image, email
...
```

From this, the firmware generates (on save, and on every Moonraker and
Klipper start):

- `extended/moonraker/notifications.cfg`: the `[push_notifications]`
  Moonraker component, with one [Apprise](https://github.com/caronc/apprise)
  URL per enabled notification and service (Discord uses the firmware's own
  `discordhook://` and `discorddm://` senders).
- `extended/klipper/notifications.cfg`: the `_PRINT_END_NTFY` and
  `_CANCEL_PRINT_NTFY` [print hooks](klipper_hooks.md).

Don't edit the generated files; they are overwritten. For other services
(Slack, Matrix, etc.), add your own Moonraker
[`[notifier]`](https://moonraker.readthedocs.io/en/latest/configuration/#notifier)
section in a separate file in `extended/moonraker/` (see
[Klipper and Moonraker Custom Includes](klipper_includes.md)).

## Troubleshooting

- **Nothing arrives**: use **Send Test** first. Then check
  `/userdata/logs/moonraker.log` for lines starting with `[push_notifications]`.
  Each notification is logged per service, e.g. `error (email): sent`, or as
  `attempt N failed` for each retry and `send failed after 4 attempts` (with
  the service's error just before each). A notification that was
  deliberately skipped is logged as `not sent, ...` with the reason, e.g. an
  error already reported by the pause it caused.
- **No emails**: check the spam folder, and use **Send Test**: `535` means
  the username or password was rejected (Gmail needs an app password, Brevo
  an SMTP key). On Brevo, `525 Unauthorized IP address` means Brevo's IP
  blocking is on: allow your IP, or turn the blocking off, under
  **Security > Authorised IPs**.
- **Telegram: "Open your bot in Telegram and press Start"**: the bot has no
  message from you in the last 24 hours. Send it any message and try again.
- **Discord: "the bot can't message you"**: the bot isn't in a server you
  are in, or that server doesn't allow DMs from its members (see
  [Discord](#discord)).
- **Notifications arrive without images**: check the camera service, and on a
  self-hosted ntfy server that attachments are enabled.
- **No layer-complete notification**: the G-code file has no
  `SET_PRINT_STATS_INFO` lines; enable layer information in the slicer.
