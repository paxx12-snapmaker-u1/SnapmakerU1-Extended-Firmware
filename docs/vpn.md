---
title: VPN Remote Access (Experimental)
---

# VPN Remote Access (Experimental)

Control your printer remotely using a VPN provider.

> **Note**: VPN providers are downloaded on-demand when enabled by the user. See [third-party integration design](design/third_party.md) for details on how external components are managed.

> **Warning**: This feature is experimental. VPN services consume additional CPU and memory resources which may affect print quality or reliability during active prints. It is recommended to disable VPN while printing or monitor system performance closely.

## Supported Providers

- **none** - VPN disabled (default)
- **tailscale** - Access your printer on your [Tailnet](https://tailscale.com)

## Tailscale

- Gain full access to your printer from anywhere
- Requires no port forwarding

### Using firmware-config Web UI (preferred)

Navigate to the [firmware-config](firmware_config.md) web interface, go to the Remote Access section, and select Tailscale under VPN Provider. This will automatically download and install Tailscale.

### Manual Setup (advanced)

Tailscale setup requires [SSH access](ssh_access.md) to the printer.

**Step 1:** Download Tailscale (requires internet connection):
```bash
ssh root@<printer-ip>
extended-pkg tailscale download
```

**Step 2:** Edit `extended/extended2.cfg`, set the `vpn`:
```ini
[remote_access]
vpn: tailscale
```

**Step 3:** Start the VPN service:
```bash
/etc/init.d/S99vpn restart
```

**Step 4:** Login to your tailnet using the [tailscale up command](https://tailscale.com/kb/1241/tailscale-up):
```bash
tailscale up

# show your tailscale IP
tailscale status | grep lava
100.95.6.132     lava               username@  linux    -
```

Note: You can use `tailscale up --ssh` to enable [tailscale SSH](https://tailscale.com/kb/1193/tailscale-ssh) and bypass passwords and keys.

### Optional: Enable SSL Certificates

Tailscale can generate Let's Encrypt SSL certificates for your printer using [Tailscale Serve](https://tailscale.com/kb/1312/serve):

```bash
tailscale serve --bg 80
# to disable: tailscale serve reset
```

Browse securely to: `https://lava.${YOUR_TAILNET}.ts.net/`

Note: The first time you load this it will take a minute to generate the certificate.

Note: Advanced users can enable [Funnel](https://tailscale.com/kb/1223/funnel) to expose Fluidd/Mainsail to the public internet. Only do this if you absolutely know what you are doing to stay secure.
