---
title: Cloud Remote Access (Experimental)
---

# Cloud Remote Access (Experimental)


Control your printer remotely using cloud-based remote access providers.

> **Note**: Cloud providers are downloaded on-demand when enabled by the user. See [third-party integration design](design/third_party.md) for details on how external components are managed.

> **Warning**: This feature is experimental. Cloud services consume additional CPU and memory resources which may affect print quality or reliability during active prints. It is recommended to monitor system performance closely.

## Supported Providers

- **none** - Cloud access disabled (default)
- **octoeverywhere** - Remote access via [OctoEverywhere.com](https://octoeverywhere.com)
- **konnect** - Register this printer with [Prusa Connect](https://connect.prusa3d.com) (or a compatible self-hosted server) via [Konnect](https://github.com/hanzov69/konnect)

## OctoEverywhere

- Access your printer remotely from anywhere
- AI print failure detection and notifications
- Webcam streaming and timelapse
- Requires no port forwarding or VPN configuration

### Using firmware-config Web UI (preferred)

Navigate to the [firmware-config](firmware_config.md) web interface, go to the Remote Access section, and select OctoEverywhere under Cloud Provider. This will automatically download and install the OctoEverywhere plugin and display the account linking instructions.

### Manual Setup (advanced)

**Step 1:** Download OctoEverywhere (requires internet connection):
```bash
ssh root@<printer-ip>
extended-pkg octoeverywhere download
```

**Step 2:** Edit `extended/extended2.cfg`, set the `cloud`:
```ini
[remote_access]
cloud: octoeverywhere
```

**Step 3:** Start the cloud service:
```bash
/etc/init.d/S99cloud restart
```

**Step 4:** Link your account by downloading `octoeverywhere.log` from Mainsail or Fluidd to find the account linking URL. Open the URL in your browser to link your printer to your OctoEverywhere.com account.

**Need help?** Visit [OctoEverywhere Support for Snapmaker U1](https://octoeverywhere.com/s/snapmaker-u1) for assistance.

## Konnect

- Shows the printer on Prusa Connect's dashboard: telemetry for all four
  toolheads, job state and remote start/pause/stop, file listing with
  thumbnails
- Alternatively registers with a custom, Connect-compatible server on
  your own network (chosen in Konnect's onboarding page)
- Built on Prusa's open printer SDK; the U1 advertises as a Prusa XL
  (toolchanger profile)

### Using firmware-config Web UI (preferred)

Navigate to the [firmware-config](firmware_config.md) web interface, go to the Remote Access section, and select Konnect under Cloud Provider. This will automatically download and install Konnect and start the service.

### Manual Setup (advanced)

**Step 1:** Download Konnect (requires internet connection):
```bash
ssh root@<printer-ip>
extended-pkg konnect download
```

**Step 2:** Edit `extended/extended2.cfg`, set the `cloud`:
```ini
[remote_access]
cloud: konnect
```

**Step 3:** Start the cloud service:
```bash
/etc/init.d/S99cloud restart
```

**Step 4:** Open `http://<printer-ip>/konnect/`, walk through the onboarding
(printer type, identity, server), click **Get code** and approve the code on
the server's add-printer page. Registration state persists in Moonraker's
database; disabling the provider stops the service without unregistering.
