---
title: Building from Source
---

# Building from Source

## Understanding Overlays

The custom firmware uses an overlay system to modify the base Snapmaker firmware. Overlays are modular modifications that:

- Add patches to modify existing firmware files
- Copy additional files to the firmware root filesystem
- Run build-time scripts to install components
- Enable features without changing the base firmware source

Each overlay is self-contained and numbered to control application order. This modular approach makes it easy to:
- Enable/disable features by including/excluding overlays
- Add custom modifications without conflicts

For external third-party components, see [Third-Party Integrations](design/third_party.md).

For external RFID API and mapping details, see [External RFID Support](design/filament_detect.md).

## Prerequisites

- Docker installed on your system

The `./dev.sh` script automatically sets up a Debian Trixie ARM64 environment with all required dependencies.

## Source Repository

The project is hosted on GitHub with a mirror on Codeberg:

- **GitHub**: [https://github.com/paxx12](https://github.com/paxx12)
- **Codeberg**: [https://codeberg.org/paxx12-snapmaker-u1](https://codeberg.org/paxx12-snapmaker-u1)

### Using Codeberg Mirror

To use Codeberg as the source instead of GitHub, configure git to automatically remap GitHub URLs:

```bash
git config --global url."https://codeberg.org/paxx12-snapmaker-u1/".insteadOf "https://github.com/paxx12/"
```

This remaps all GitHub repository URLs to Codeberg automatically, including submodules and dependencies.

## Quick Start

Build tools and download firmware:

```bash
./dev.sh make tools
./dev.sh make firmware
```

Build extended firmware:

```bash
./dev.sh make build PROFILE=extended OUTPUT_FILE=firmware/U1_extended.bin
```

Open a shell in the development environment:

```bash
./dev.sh bash
```

## Mods

The `PROFILE` argument is parsed as `<firmware>[-<mod>]*`: a single required
firmware name, followed by zero or more optional mod names, each joined
with a `-`. The firmware name selects overlays from `overlays/firmware-<firmware>/`,
and each mod name adds overlays from `overlays/mods/<mod>/`. Mods
can be freely combined, e.g. `extended-devel`, `extended-qemu`, or
`extended-devel-qemu`.

Run `make mods` to list the firmwares and mods currently available.

See [Mods](mods.md) for naming conventions and the rules around
contributing your own mod.

## Overlays

Overlays are organized into categories based on their scope and build mods. Each overlay is numbered to indicate its application order within its category.

### Overlay Categories

- **common/** - Core modifications applied to all firmware builds
- **firmware-\<name\>/** - Modifications specific to a firmware, e.g. `firmware-extended/`
- **mods/\<name\>/** - Optional, composable overlays enabled by adding `-<name>` to `PROFILE`, e.g. `mods/devel/`

## Build Options

- `extended-devel` - Add the `devel` mod overlays from `overlays/mods/devel/`
  - e.g. `./dev.sh make build PROFILE=extended-devel`
- `extended-qemu` - Add the `qemu` mod overlays from `overlays/mods/qemu/` (eth0 hotplug and virtio-touch hwdb mapping for the QEMU dev environment)
  - e.g. `./dev.sh make build PROFILE=extended-qemu`

### Devel Mod Features

When running firmware built with the `devel` mod, additional development tools are available:

**Entware Package Manager**

> The Entware is considered highly untrusted component,
> and might be removed at any point in the future without notice.

The devel mod includes Entware support for installing additional packages. After booting the devel firmware, initialize Entware:

```bash
entware-ctrl init
```

This sets up the Entware environment in `/userdata/extended/entware` and installs the bootstrap packages.

Other entware-ctrl commands:

- `entware-ctrl start` - Activate Entware (mount /opt)
- `entware-ctrl stop` - Deactivate Entware (unmount /opt)
- `entware-ctrl nuke` - Remove Entware installation completely

Once initialized, use `opkg` to install packages from the Entware repository.

### Directory Structure

```text
├── common/                          Core overlays applied to all builds
├── firmware-${firmware}/            Firmware-specific overlays
└── mods/${mod}/                     Optional overlays enabled by `-${mod}` in PROFILE
```

### Overlay Structure

Each overlay directory can contain:

- `patches/` - Patch files applied to extracted firmware
- `root/` - Files copied to firmware root filesystem
- `scripts/` - Build-time scripts executed during firmware build
- `pre-scripts/` - Scripts executed before main build process

### Application Order

Overlays are applied in the following order:

1. All overlays from `common/` (in numeric order)
1. Firmware-specific overlays from `firmware-${firmware}/` (in numeric order)
1. Mod-specific overlays from `mods/${mod}/` (in numeric order), for each `-${mod}` in `PROFILE`, in the order given

### Integrating Upstream Klipper Patches

The `20-klipper-patches` overlay in `firmware-extended/` backports upstream Klipper commits. To add new patches:

1. **Download the commit as a patch from GitHub:**
   ```bash
   wget https://github.com/Klipper3d/klipper/commit/16fc46fe5.patch -O 01_16fc46fe5.patch
   ```
   GitHub serves any commit as a patch by appending `.patch` to the commit URL.

2. **Name with order prefix and commit hash:**
   ```text
   01_16fc46fe5.patch
   02_6d1256ddc.patch
   03_16b4b6b30.patch
   ```

3. **Place in the target path within the overlay:**
   ```text
   overlays/firmware-extended/20-klipper-patches/patches/home/lava/klipper/
   ```
   The `patches/` directory maps to the firmware root, so `patches/home/lava/klipper/` applies patches to `/home/lava/klipper/` where Klipper is installed.

4. **Edit the patch to remove irrelevant hunks:**
   Upstream commits often include `docs/` and config changes that don't apply. Remove those hunks, keeping only the Python code changes in `klippy/`.

5. **Document in the overlay README:**
   Update `20-klipper-patches/README.md` with links to the upstream commits.

## Project Structure

```text
.
├── .github/                     Automated release builds
├── overlays/                    Overlay directories
│   ├── common/                  Core overlays for all builds
│   ├── firmware-${firmware}/    Firmware-specific overlays
│   └── mods/${mod}/             Optional overlays enabled by `-${mod}` in PROFILE
├── firmware/                    Downloaded and generated firmware files
├── scripts/                     Build and modification scripts
├── tmp/                         Temporary build artifacts
├── tools/                       Firmware manipulation tools
│   ├── rk2918_tools/            Rockchip image tools
│   └── upfile/                  Firmware unpacking tool
├── Makefile                     Build configuration
└── vars.mk                      Firmware version and kernel configuration
```

## Configuration

Edit `vars.mk` to configure base firmware and kernel.

## Extract Firmware

To extract and examine the base firmware:

```bash
./dev.sh make extract
```

Output: `tmp/extracted-<version>/` (the `FIRMWARE_VERSION` from `vars.mk`)

## Upgrade Firmware

To build and deploy firmware directly to a connected printer:

```bash
./dev.sh ./scripts/dev/upgrade-firmware.sh root@<printer-ip> <profile>
```

Example:

```bash
./dev.sh ./scripts/dev/upgrade-firmware.sh root@192.168.1.100 extended
```

By default, the script uses `snapmaker` as the SSH password. To use a different password:

```bash
PASSWORD=mypassword ./dev.sh ./scripts/dev/upgrade-firmware.sh root@192.168.1.100 extended
```

## Release Process

The project uses GitHub Actions for automated releases:

1. Changes pushed to `main` trigger a pre-release build
2. Extended firmware is built
3. Version is auto-incremented using `scripts/next_version.sh`
4. Release artifacts are published to GitHub Releases

## Tools

### rk2918_tools

- `afptool` - Android firmware package tool
- `img_maker` - Create Rockchip images
- `img_unpack` - Unpack Rockchip images
- `mkkrnlimg` - Create kernel images

### upfile

Firmware unpacking utility for Snapmaker update files.
