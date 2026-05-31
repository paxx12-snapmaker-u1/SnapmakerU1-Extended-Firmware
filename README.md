# Custom Snapmaker U1 Firmware

[![Latest Release](https://img.shields.io/github/v/release/paxx12/SnapmakerU1)](https://github.com/paxx12/SnapmakerU1/releases/latest)
[![Pre-release](https://img.shields.io/github/v/release/paxx12/SnapmakerU1?include_prereleases&label=pre-release)](https://github.com/paxx12/SnapmakerU1/releases)

This project builds custom firmware for the Snapmaker U1 3D printer,
enabling debug features like SSH access and adding additional capabilities.

This is an independent project and is not affiliated with Snapmaker.

> **Warning**: While installing custom firmware does not automatically void the product warranty, any damage caused by or attributable to the installation or use of custom firmware is not covered under warranty. Use at your own risk. See [Snapmaker Terms of Use](https://www.snapmaker.com/terms-of-use) for details.
>
> Custom firmware is intended for users with appropriate technical knowledge. Ensure you understand the implications before proceeding.

## Download

Get the latest pre-built firmware from [Releases](https://github.com/paxx12/SnapmakerU1/releases).

## Documentation

See [User Documentation](https://snapmakeru1-extended-firmware.pages.dev/) for features, installation instructions, and usage guides.

## Building from Source

**Source repositories:**
- GitHub: [https://github.com/paxx12](https://github.com/paxx12)
- Codeberg: [https://codeberg.org/paxx12-snapmaker-u1](https://codeberg.org/paxx12-snapmaker-u1)

See [Building from Source](docs/development.md) for instructions on building custom firmware using Docker.

## Dependent projects

- [v4l2-mpp](https://github.com/paxx12/v4l2-mpp) - Hardware-accelerated camera stack with WebRTC streaming, V4L2 controls, and settings persistence
- [screen-apps](https://github.com/paxx12/screen-apps) - U1 touchscreen applications and UI components
- [prometheus-klipper-exporter](https://github.com/scross01/prometheus-klipper-exporter) - Prometheus metrics exporter for Klipper
- [snapmaker-u1-timelapse-recovery](https://github.com/horzadome/snapmaker-u1-timelapse-recovery) - Tool to recover corrupted timelapse videos
- [rockchip-linux/kernel](https://github.com/rockchip-linux/kernel) - Rockchip kernel source for building additional kernel modules

## Community

Join the [Snapmaker Discord](https://discord.com/invite/snapmaker-official-1086575708903571536) and visit the **#u1-printer** channel to connect with other users using the custom firmware, share experiences, and get help.

## Issues

For bug reports, please validate the issue against Stock Firmware first before creating a bug report. This helps determine if the issue is specific to the custom firmware or exists in the stock firmware as well.

This repository does not accept feature requests or support issues in general. Pull Requests are the desired way to propose new changes and may be accepted after review.

## Contact

For inquiries about the firmware, contact: paxx12dev@gmail.com

## Support

If you find this project useful and would like to support its development:

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/paxx12)

🖨️ **Buy a Snapmaker U1** — ordering via the link below supports this project. Optionally use code `PAXX12CUSTOM` for $20 off, or any other discount you find online:

  * EU store: [https://snapmaker-eu.myshopify.com?ref=paxx12](https://snapmaker-eu.myshopify.com?ref=paxx12)
  * US store: [https://snapmaker-us.myshopify.com?ref=paxx12](https://snapmaker-us.myshopify.com?ref=paxx12)
  * Global store: [https://test-snapmaker.myshopify.com?ref=paxx12](https://test-snapmaker.myshopify.com?ref=paxx12)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for information about contributing to this project.

See [HEROES.md](HEROES.md) for contributors who made significant contributions to each release.

## License

The main project is licensed under the GNU General Public License v3.0
(GPL-3.0). See [LICENSE](LICENSE) for details.

For licensing information about individual tools and dependencies, see their respective directories.
