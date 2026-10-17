---
title: Anycubic ACE via multiACE
---

# Anycubic ACE via multiACE

This experimental integration is provided by the rolling-only
`extended-multiace` firmware profile. It is not included in stable firmware
images. The PAXX-managed multiACE provider package is downloaded only when
selected in Firmware Config; it is not bundled into the firmware image.

This integration packages [multiACE](https://github.com/decay71/multiACE), the
upstream Anycubic ACE implementation created by [Decay (@decay71)](https://github.com/decay71)
and developed with its contributors. PAXX provides the U1-specific packaging
and firmware integration; the upstream ACE implementation is the work of the
multiACE authors.

This draft pins a published multiACE release for hardware validation on the
rolling `extended-multiace` image only. Initial printer testing confirmed that
an ACE 2 Pro can reach the ready state after fixing a serial-device permission
problem; filament operations and reconnect behavior are still being tested.
The firmware integration remains experimental even though the provider
version is a regular release.

The PAXX integration follows the same general model used by other optional
third-party applications in this firmware: the provider release is pinned in
the firmware source, downloaded on demand, and verified with a SHA256 checksum.

## Enable it

1. Install the `extended-multiace` image from the project's rolling release.
2. Open **Firmware Config** and then **Snapmaker Components**.
3. Set **multiACE Integration** to **Enabled**.
4. Confirm the action and allow the printer to reboot.

For firmware updates, select the `develop` upgrade channel. The rolling
release includes a matching `extended-multiace` image; stable and testing
releases do not.

Remove any separately installed or manually started multiACE copy before
enabling this option. Two copies must not be active at the same time: they can
both try to provide the same Klipper modules or web port.

For the adapter tested with the ACE 2 Pro (USB ID `1a86:7523`), add
`v2_extra_usb_ids: 1a86:7523` under the existing `[ace]` section in
`/home/lava/printer_data/config/extended/ace.cfg`. The default intentionally
does not accept every generic serial adapter as an ACE. PAXX installs a
persistent udev rule for this exact USB ID so the `lava` account can open the
serial device after boot and USB reconnection. Verify that behavior after
flashing this change; the test printer previously needed a one-time manual
permission correction before the ACE reached ready state.

When enabling multiACE, PAXX checks whether the installed package differs from
the firmware's reviewed version pin and upgrades it when needed. If multiACE is
already enabled, the **Upgrade pinned multiACE package** quick action performs
the same upgrade and reboots afterward.

The first installation also installs the provider's constrained web
dependencies into the versioned MultiACE package. This may take longer than
subsequent starts and requires the printer to be able to download the package
and dependencies. The dependencies therefore survive reboot without requiring
the firmware's debug-persistence mode.

## What PAXX manages

The firmware owns the integration boundary while the provider owns its ACE
behavior:

- The exact provider release URL and SHA256 checksum are pinned in the
  firmware package definition.
- The release is installed under `/oem/apps/multiace/` with a `latest` link.
- Provider Klipper modules are mounted read-only over their stock paths only
  when `components ace: multiace` is selected.
- The stock files are not overwritten. Disabling the component removes the
  mounts and restores the stock paths on the next Klipper start.
- Persistent configuration is kept under
  `/home/lava/printer_data/config/extended/multiace/`.
- The provider configuration is linked into the normal extended Klipper
  include directory only while the managed integration is enabled.
- The web UI is served through the authenticated Fluidd or Mainsail origin at
  `/multiace/`; its backend listens only on localhost.
- The managed provider package disables standalone install, uninstall,
  self-update, and file-copy mode switching. multiACE-to-head runtime mode
  changes remain available. PAXX owns package upgrades through the reviewed
  version and checksum pin.
- PAXX retains an idempotent cleanup for stale update-wrapper macros in
  persistent configs created by the older test archive; it does not pass the
  legacy update-disable environment flag. The managed provider package ships
  without those wrappers and enforces managed mode itself.

The integration uses the platform-neutral `.multiace-managed` marker in its
persistent state directory. Decay's managed-package PR defines this provider
contract:

- `MULTIACE_MANAGED=1`
- `MULTIACE_MANAGED_MARKER`
- `MULTIACE_APP_DIR`
- `MULTIACE_CONFIG_DIR` (the directory containing `printer.cfg`,
  `/home/lava/printer_data/config` on the U1)
- `MULTIACE_PRINTER_DATA`

The marker and paths let multiACE distinguish a platform-managed install from
its standalone installation without coupling provider code to PAXX-specific
paths or names. PAXX passes the five-variable contract to the managed web
service and preserves the provider's default U1 paths for Klipper.

## Disable or remove it

In Firmware Config, set **multiACE Integration** to **Disabled** and confirm
the reboot. The PAXX service stops the web UI, removes the provider mounts, and
removes the downloaded package. Persistent ACE configuration is intentionally
left in place so it can be inspected or reused if the integration is enabled
again.

If a package is present but cannot be activated, the activation hook fails
closed: it unmounts any partial provider mounts and leaves the stock Klipper
paths available. A conflicting file or an already occupied provider web port
is treated as an error rather than silently replacing another installation.

## Current pin and scope

PAXX pins multiACE version `1.27b` from the regular
[`v1.27b` release](https://github.com/decay71/multiACE/releases/tag/v1.27b),
built from source commit
[`a1a7b91`](https://github.com/decay71/multiACE/commit/a1a7b91b1fd297c1486a13ea22c61e01499a196d).
The exact managed archive URL and SHA256 are pinned in the package definition;
the installer does not follow a moving `latest` release or an unverified
branch. This release includes the Gen 1 tunnel and web preflight helper as
required managed-package files.

There is one remaining provider-package gap: the `v1.27b` managed archive
excludes `tools/merge_ace_cfg.py`, even though that helper exists in the
multiACE source tree. PAXX preserves the existing `ace.cfg` and will invoke the
provider helper during package refresh when it is present, but this release
cannot merge new template options into an existing user config. Including that
helper in a managed multiACE release (or an equivalent provider-supported
managed migration interface) is still needed to complete safe config upgrades.

This is a draft integration for hardware testing. It has been checked for
package structure, checksum verification, shell syntax, and provider-side
managed-boundary tests; ACE loading, unloading, tool changes, recovery, and
long-print behavior still require testing on a connected printer.

Report ACE protocol, device detection, and multiACE UI behavior in the
multiACE issue tracker at https://github.com/decay71/multiACE/issues. Report
firmware packaging, component selection, device permissions, and activation
problems in the PAXX firmware issue tracker at
https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware/issues.

The PR adds `overlays/mods/multiace/` and a separate `extended-multiace`
rolling image; it does not add multiACE to the stable release workflow or
combine it with the AFC profile.
