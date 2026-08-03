## Notice

> **Warning**: While installing custom firmware does not automatically void the product warranty, any damage caused by or attributable to the installation or use of custom firmware is not covered under warranty. Use at your own risk. See [Snapmaker Terms of Use](https://www.snapmaker.com/terms-of-use) for details.
>
> If you notice a problem, always reproduce it on stock firmware before contacting Snapmaker support. Despite our best efforts, bugs can occur. Only contact support if the issue also occurs on stock firmware.
>
> Custom firmware is intended for users with appropriate technical knowledge. Ensure you understand the implications before proceeding.

## New Features and Key Changes

- TBD

## Heroes of this release

- TBD

See [HEROES.md](HEROES.md) for all-time contributors.

## Install

For detailed installation instructions, see the [Installation Guide](docs/install.md).

Quick steps:

1. Download `.bin` and put on FAT32 formatted USB device
2. On the printer go to `Settings` > `About` > `Firmware Version` > `Local Update`
3. Select `.bin` and confirm.

## Troubleshooting

- **Custom extensions installed via SSH**: Installing third-party extensions or modifications over SSH (for example [helixscreen](https://github.com/prestonbrown/helixscreen)) can break the system. Because such changes are made outside the supported extended configuration, the built-in recovery may not undo them, and in some cases the system becomes very hard to recover. Do this only if you understand the risks and know how to restore the printer. If something breaks, try recovery in order: first `extended-recover.txt` (resets extended configuration), then `full-recover.txt` (also clears persisted changes, printer data and the debug flag), and only if neither works, reflash stock firmware via the [Snapmaker U1 Wiki](https://wiki.snapmaker.com/en/snapmaker_u1/firmware/release_notes).

## Revert

1. Download `.bin` from the [Snapmaker U1 Wiki](https://wiki.snapmaker.com/en/snapmaker_u1/firmware/release_notes).
2. Follow the same as for install.

## Community

Join the [Snapmaker Discord](https://discord.com/invite/snapmaker-official-1086575708903571536) and visit the **#u1-printer** channel to connect with other users using the custom firmware, share experiences, and get help.

## Support

If you find this project useful and would like to support its development, you can:

[![Buy Me A Coffee](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://buymeacoffee.com/paxx12)

🖨️ **Buy a Snapmaker U1** — ordering via the link below supports this project. Optionally use code `PAXX12CUSTOM` for $20 off, or any other discount you find online:

  * EU store: [https://snapmaker-eu.myshopify.com?ref=paxx12](https://snapmaker-eu.myshopify.com?ref=paxx12)
  * US store: [https://snapmaker-us.myshopify.com?ref=paxx12](https://snapmaker-us.myshopify.com?ref=paxx12)
  * Global store: [https://test-snapmaker.myshopify.com?ref=paxx12](https://test-snapmaker.myshopify.com?ref=paxx12)
