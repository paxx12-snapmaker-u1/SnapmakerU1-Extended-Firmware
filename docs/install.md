---
title: Installation Guide
---

# Installation Guide

This guide covers installing custom firmware on your Snapmaker U1 3D printer.

> **Warning**: While installing custom firmware does not automatically void the product warranty, any damage caused by or attributable to the installation or use of custom firmware is not covered under warranty. Use at your own risk. See [Snapmaker Terms of Use](https://www.snapmaker.com/terms-of-use) for details.
>
> Custom firmware is intended for users with appropriate technical knowledge. Ensure you understand the implications before proceeding.

## Prerequisites

- USB drive formatted as FAT32
- Downloaded firmware `.bin` file from [Releases](https://github.com/paxx12/SnapmakerU1/releases)

## Installation Steps

1. **Download Firmware**
   - Get the latest `.bin` file from the [Releases page](https://github.com/paxx12/SnapmakerU1/releases)

2. **Prepare USB Drive**
   - Format a USB drive as FAT32
   - Copy the downloaded `.bin` file to the root of the USB drive

3. **Install on Printer**
   - Insert the USB drive into the printer
   - On the printer touchscreen, navigate to: `Settings` > `About` > `Firmware Version` > `Local Update`
   - Select the `.bin` file from the USB drive
   - Confirm the installation
   - Wait for the update to complete (printer will reboot)

## Post-Installation

After installation, the printer will automatically reboot.

**Next Steps:**
- [SSH Access](ssh_access.md) - Access printer via SSH
- [Firmware Configuration](firmware_config.md) - Customize firmware behavior (Extended only)
- [Camera Support](camera_support.md) - Configure WebRTC camera streaming (Extended only)
- [Remote Screen](remote_screen.md) - Enable remote screen access (Extended only)
- [NFC Tags & Spoolman Setup Guide](nfc_spoolman_setup.md) - Tag your spools so the printer recognises them, and track them in Spoolman (Extended only)

## Reverting to Stock Firmware

If you need to revert to the original Snapmaker firmware:

1. Download the official `.bin` file from the [Snapmaker U1 Wiki](https://wiki.snapmaker.com/en/snapmaker_u1/firmware/release_notes)
2. Follow the same installation steps as above

## Troubleshooting

- **Update fails**: Ensure the USB drive is formatted as FAT32
- **File not found**: Make sure the `.bin` file is in the root directory of the USB drive
- **Printer won't boot**: Try reverting to stock firmware
- **Klipper failed to start** (Extended only): Open Firmware Config at `http://IP/firmware-config`, go to `Recovery` and select `Reset Extended to Defaults`. This will reset all extended settings and reboot the printer.
- For additional help, open an issue on the [GitHub repository](https://github.com/paxx12/SnapmakerU1/issues)
