---
title: Firmware Configuration
---

# Firmware Configuration


> **Note for users upgrading to v1.1.0:** The configuration file has been renamed from `extended.cfg` to `extended2.cfg`. During the first boot after upgrading a new `extended2.cfg` will be created with default settings. You will need to migrate your custom settings from `extended.cfg` to `extended2.cfg` manually or use `http://IP/firmware-config`

The extended firmware provides two ways to configure firmware behavior:

1. **Firmware Config Web Interface** - A web-based tool for managing settings, firmware upgrades, and troubleshooting
2. **extended2.cfg** - A configuration file for customizing firmware behavior

## Firmware Config Web Interface

Access the Firmware Config tool at `http://<printer-ip>/firmware-config/`

**Note:** The Firmware Config interface is only available when Advanced Mode is enabled. On the printer touchscreen, go to **Settings > Maintenance > Advanced Mode** and enable it, then restart the printer.

### Status

Displays system information including:
- Base firmware version
- Build version and profile
- Active firmware slot (A/B)
- Device name

### Quick Links

Dynamic links based on current settings:
- **Web Interface** - Opens Fluidd/Mainsail
- **Internal Camera** - Camera stream (when paxx12 service is enabled)
- **USB Camera** - USB camera stream (when enabled)
- **Remote Screen** - Remote screen access (when enabled)

### Settings

Toggle settings directly from the web interface:

| Setting | Options | Description |
|---------|---------|-------------|
| Frontend | Fluidd, Mainsail | Switch between web interfaces |
| Require Login (Fluidd only) | Enabled, Disabled | Require login for Moonraker API access |
| Internal Camera | Paxx12, Snapmaker, Disabled | Select camera service |
| Camera RTSP Stream | Enabled, Disabled | Enable RTSP streaming |
| USB Camera | Enabled, Disabled | Enable USB camera support |
| Remote Screen | Enabled, Disabled | Enable remote screen access |
| Klipper Metrics Exporter | Enabled, Disabled | Enable Prometheus metrics |
| VPN Provider | None, Tailscale | Enable VPN remote access (Experimental) |
| Cloud | None, OctoEverywhere | Enable Cloud-based remote access (Experimental) |
| Tweaks | TMC AutoTune, TMC Reduced Current, Object Processing, AFC Stub | Experimental Klipper tweaks ([tweaks](tweaks.md)) |
| Troubleshooting | Faulty Toolhead Bypass | Temporary toolhead thermistor bypass so the remaining toolheads can still be used ([faulty_toolhead](faulty_toolhead.md)) |
| RFID Detection System | External, Snapmaker, OpenRFID, OpenRFID (force generic vendor) | Set how filament is detected ([rfid_support](rfid_support.md)) |

Changes are applied immediately and relevant services are restarted.

### Troubleshooting

Available actions:
- **Show MCUs Version** - Display microcontroller firmware versions
- **Collect System Logs** - Generate and download system logs for debugging
- **Restart Klipper** - Restart the Klipper service
- **Restart Moonraker** - Restart the Moonraker service
- **Reboot System** - Reboot the printer
- **Revert Changes** - Remove configuration changes and disable data persistence
- **Recover to Backup Firmware** - Restore previous firmware version

### Firmware Upgrade

Upgrade firmware using one of two methods:

**Download from URL:**
1. Enter a firmware URL
2. Click "Download & Upgrade"
3. The system downloads and installs the firmware

**Upload File:**
1. Select or drag-and-drop a firmware file
2. Click "Upload & Upgrade"
3. The file is uploaded and installed

The system reboots automatically after a successful upgrade.

## Configuration File (extended2.cfg)

For advanced configuration, edit the configuration file directly.

### File Location

```
/home/lava/printer_data/config/extended/extended2.cfg
```

### Editing the Configuration File

The `extended2.cfg` file is automatically created by the firmware.

Some Firmware Config options, such as tweaks and troubleshooting helpers,
install files into `extended/klipper/` or `extended/moonraker/` instead of
writing to `extended2.cfg`.

#### Via Fluidd/Mainsail

1. On the printer, go to **Settings > Maintenance > Advanced Mode** and enable it
2. Open Fluidd or Mainsail in your web browser (`http://<printer-ip>`)
3. Go to the **Configuration** tab
4. Navigate to the `extended` directory and open `extended2.cfg`
5. Add or modify your configuration options (see below)
6. Save the file
7. Reboot the printer

#### Via SSH

```bash
ssh lava@<printer-ip>
vi /home/lava/printer_data/config/extended/extended2.cfg
```

After saving, reboot the printer.

### Configuration Options

#### [web]

**frontend** - Web interface selection (only one can be active)
- `fluidd` (default) - Fluidd web interface
- `mainsail` - Mainsail web interface

**firmware_config** - Enable or disable the Firmware Config web interface
- `true` (default) - Firmware Config available at `/firmware-config/` when Advanced Mode is enabled
- `false` - Firmware Config disabled even when Advanced Mode is enabled

**remote_screen** - Enable remote screen access at `http://<printer-ip>/screen/`
- `true` - Enable remote screen viewing and touch control in web browser
- `false` (default) - Remote screen access disabled

Note: Remote screen requires additional Moonraker configuration. See [Remote Screen Access](remote_screen.md) for complete setup.

#### [camera]

**internal** - Internal camera service selection (only one can be active)
- `paxx12` (default) - Hardware-accelerated v4l2-mpp camera service with WebRTC and timelapse
- `snapmaker` - Native Snapmaker camera service
- `none` - Disable internal camera

**usb** - USB camera service selection
- `paxx12` - Enable USB camera with paxx12 service at `http://<printer-ip>/webcam2/`
- `none` (default) - USB camera disabled

**rtsp** - Enable RTSP streaming support (paxx12 service only)
- `true` - Enable RTSP streaming at `rtsp://<printer-ip>:8554/stream` (internal) and `rtsp://<printer-ip>:8555/stream` (USB)
- `false` (default) - RTSP streaming disabled

**logs** - Camera service logging destination
- `syslog` - Enable logging to `/var/log/messages`

#### [components]

**rfid** - Filament tag detection system
- `snapmaker` (default) - Snapmaker's built-in RFID reader
- `external` - Disable built-in readers (useful for external readers)
- `openrfid` - Alternative detection with extended spool/tag support
- `openrfid-generic` - Same as openrfid, labels all spool vendors as generic

See [Alternative Filament Detection](rfid_support.md#alternative-detection-systems) for setup instructions.

#### [remote_access]

**ssh** - Enable SSH remote access via dropbear
- `true` - Enable SSH access
- `false` (default) - SSH disabled

**vpn** - VPN provider for remote access (only one can be active)
- `none` (default) - VPN disabled
- `tailscale` - Connect to your Tailnet via [Tailscale](https://tailscale.com)

See [VPN Remote Access](vpn.md) for setup instructions.

**cloud**
- `none` (default) - No cloud providers enabled.
- `octoeverywhere` - [OctoEverywhere.com](https://octoeverywhere.com) remote access

See the [3D Printing Clouds](cloud.md) for setup instructions.

#### [monitoring]

**klipper_exporter** - Enable Prometheus metrics exporter for Klipper
- `:9101` - Enable metrics at `http://<printer-ip>:9101/metrics`
- Custom format: `[host]:port` (e.g., `127.0.0.1:9101`, `:8080`)
- Not set (default) - Klipper exporter disabled

See [Monitoring](monitoring.md) for integration with Grafana, Home Assistant, or DataDog.

### Example Configuration

```ini
[web]
# Web interface frontend: fluidd, mainsail
frontend: fluidd
# Enable access at http://<printer-ip>/firmware-config/: true, false
firmware_config: true
# Enable access at http://<printer-ip>/screen/: true, false
remote_screen: false

[camera]
# Internal (Case) camera options: paxx12, snapmaker, none
internal: paxx12
# External (USB) camera options: paxx12, none
usb: none
# Enable RTSP streaming server: true, false
rtsp: false

[components]
# Filament tag detection system: external, snapmaker, openrfid, openrfid-generic
rfid: snapmaker

[remote_access]
# Enable SSH access: true, false
ssh: false
# VPN provider for remote access: none, tailscale
# Must SSH and run "tailscale up" to complete login flow
vpn: none
# Cloud: none, octoeverywhere
# - none - No cloud services enabled.
# - octoeverywhere - OctoEverywhere.com remote access
cloud: none

[monitoring]
# Enable Klipper Prometheus exporter on specified address
# klipper_exporter: :9101
```

### Identifying Customized Settings

When you modify a configuration file, the system automatically creates a `.default` file alongside it containing the original default values. For example, if you customize `extended2.cfg`, you'll find `extended2.cfg.default` in the same directory.

This makes it easy to:
- See which files you have customized
- Compare your changes against the defaults
- Restore default values if needed

The `.default` files are updated on each boot to reflect the current firmware defaults.

## Important Notes

- After making changes to `extended2.cfg`, reboot the printer for changes to take effect
- The file uses INI-style format with sections like `[camera]` and `[web]`
- Lines starting with `#` are comments and ignored
- Only one camera service can be active at a time for internal camera
- Only one web interface can be active at a time
- Many Firmware Config settings update `extended2.cfg`, but some options also
  manage files in `extended/klipper/` or `extended/moonraker/`

## Recovery & Reset

### Reset to Default Configuration

To restore default extended configuration, remove or rename the `extended` folder in Fluidd/Mainsail Configuration tab, then reboot.

### Password Recovery

If you forget your Moonraker admin password when Require Login/Password (Fluidd only) is enabled:

1. Create an empty file named `extended-recover.txt` on a USB drive
2. Insert the USB drive into the printer
3. Restart the printer
4. The extended configuration (including authentication settings) will be backed up and reset
5. Remove the USB drive
6. Re-enable Require Login/Password (Fluidd only) in Firmware Config to generate a new admin password

**Important:** The `extended-recover.txt` method resets ALL extended configuration, not just authentication. Your other settings (camera, VPN, etc.) will also be reset to defaults.

### Recovery from Configuration Issues

If an invalid configuration breaks Moonraker (printer won't connect to WiFi):

1. Create an empty file named `extended-recover.txt` on a USB drive
2. Insert the USB drive into the printer
3. Restart the printer
4. The extended configuration will be backed up to `extended.backup.N` and reset to defaults
5. Remove the USB drive (the recovery file will be automatically deleted)

## Related Documentation

- [Camera Support](camera_support.md) - Camera features and WebRTC streaming
- [Klipper and Moonraker Custom Includes](klipper_includes.md) - Add custom configuration files
- [Klipper Print Hooks](klipper_hooks.md) - React to print lifecycle events via hook macros
- [Data Persistence](data_persistence.md) - Understanding persistent storage
- [VPN Remote Access](vpn.md) - Secure remote access via Tailscale
