---
title: Custom Snapmaker U1 Firmware
---

# Custom Snapmaker U1 Firmware

[![Latest Release](https://img.shields.io/github/v/release/paxx12/SnapmakerU1)](https://github.com/paxx12/SnapmakerU1/releases/latest)
[![Pre-release](https://img.shields.io/github/v/release/paxx12/SnapmakerU1?include_prereleases&label=pre-release)](https://github.com/paxx12/SnapmakerU1/releases)

Custom firmware for the Snapmaker U1 3D printer, enabling debug features like SSH access and adding additional capabilities.

This is an independent project and is not affiliated with Snapmaker.

> **Warning**: While installing custom firmware does not automatically void the product warranty, any damage caused by or attributable to the installation or use of custom firmware is not covered under warranty. Use at your own risk. See [Snapmaker Terms of Use](https://www.snapmaker.com/terms-of-use) for details.
>
> Custom firmware is intended for users with appropriate technical knowledge. Ensure you understand the implications before proceeding.

## Getting Started

### Installation

Download the latest firmware from [Releases](https://github.com/paxx12/SnapmakerU1/releases).

**📖 [Installation Guide](install.md)** - Step-by-step installation instructions

[Release notes](https://github.com/paxx12/SnapmakerU1/releases/latest)

### Building from Source

**Source repositories:**
- GitHub: [https://github.com/paxx12](https://github.com/paxx12)
- Codeberg: [https://codeberg.org/paxx12-snapmaker-u1](https://codeberg.org/paxx12-snapmaker-u1)

For developers who want to build custom firmware, see [Building from Source](development.md).

## Community

Join the [Snapmaker Discord](https://discord.com/invite/snapmaker-official-1086575708903571536) and visit the **#u1-printer** channel to connect with other users using the custom firmware, share experiences, and get help.

## Features

Heavily expanded firmware with extensive features and customization:

**Web & Configuration:**

- [Firmware Configuration](firmware_config.md) - Customize firmware behavior via web interface or config file
- [Fluidd or Mainsail](firmware_config.md#web) (selectable) - Choose your preferred web interface

**Camera & Media:**

- [Camera Support](camera_support.md) - Hardware-accelerated camera stack with WebRTC streaming for internal and USB cameras
- [Remote Screen](remote_screen.md) - View and control printer screen remotely via web browser
- Timelapse Support - Record print timelapses with automatic cleanup
- [Timelapse Recovery Tool](https://github.com/horzadome/snapmaker-u1-timelapse-recovery) - Recover unplayable timelapse videos

**Klipper Customization:**

- [Klipper and Moonraker Custom Includes](klipper_includes.md) - Add custom configuration files via Fluidd/Mainsail
- [Klipper Print Hooks](klipper_hooks.md) - React to `PRINT_START`, `PRINT_END`, and `CANCEL_PRINT` without modifying stock macros
- [Klipper Tweaks](tweaks.md) - Experimental [TMC driver optimizations](tweaks.md#tmc-autotune), [reduced current](tweaks.md#tmc-reduced-current), and [object processing for adaptive mesh](tweaks.md#object-processing-for-adaptive-mesh)
- [AFC-Lite Stub](afc-lite.md) - Experimental AFC UI compatibility layer for Fluidd/Mainsail
- [Faulty Toolhead Bypass](faulty_toolhead.md) - Temporary bypass for one failed toolhead thermistor so the other toolheads can still be used
- [RFID Filament Tag Support](rfid_support.md) - NTAG213/215/216 support for OpenSpool format
- [Alternative Filament Detection](rfid_support.md#alternative-detection-systems) - Alternative detection implementations with extended spool/tag support from Bambu, Creality, Anycubic, and others

**Monitoring & Notifications:**

- [Monitoring](monitoring.md) - Integration with Prometheus, Home Assistant, DataDog, and other monitoring systems
- Moonraker Apprise Notifications - Send print notifications to Discord, Telegram, Slack, and 90+ services

**Remote Access:**

- [VPN Remote Access](vpn.md) - Secure remote access via Tailscale
- [Cloud Remote Access](cloud.md) - Cloud-based remote access service for 3D printers

## Support

See [Heroes](https://github.com/paxx12/SnapmakerU1/blob/main/HEROES.md) for contributors who made significant contributions to each release.

If you find this project useful and would like to support its development, you can:

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/paxx12)

🖨️ **Buy a Snapmaker Printer from Official Store** - use referral link and discount code `PAXX12` to get 5% discount on any purchase

  * EU store: [https://snapmaker-eu.myshopify.com?ref=paxx12](https://snapmaker-eu.myshopify.com?ref=paxx12)
  * US store: [https://snapmaker-us.myshopify.com?ref=paxx12](https://snapmaker-us.myshopify.com?ref=paxx12)
  * Global store: [https://test-snapmaker.myshopify.com?ref=paxx12](https://test-snapmaker.myshopify.com?ref=paxx12)

🖨️ **Buy a Snapmaker Printer from Official Store** - use discount code `PAXX12CUSTOM` to get $20 off Snapmaker U1 printer

  * EU store: [https://snapmaker-eu.myshopify.com?ref=paxx12](https://snapmaker-eu.myshopify.com?ref=paxx12)
  * US store: [https://snapmaker-us.myshopify.com?ref=paxx12](https://snapmaker-us.myshopify.com?ref=paxx12)
  * Global store: [https://test-snapmaker.myshopify.com?ref=paxx12](https://test-snapmaker.myshopify.com?ref=paxx12)
