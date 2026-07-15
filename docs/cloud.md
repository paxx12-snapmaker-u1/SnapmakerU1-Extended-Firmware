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
