# HelixScreen

Adds Firmware Config functionality to install and manage [HelixScreen](https://helixscreen.org/),
an alternative touchscreen UI for the Snapmaker U1.

## What it does

- **Settings > Snapmaker Components > HelixScreen** — toggle Enabled to install,
  toggle Disabled to uninstall (restores stock UI with confirm)
- **Actions > System > Update HelixScreen** — re-runs the installer to get the
  latest version (only visible when installed)

No HelixScreen source code is bundled. The overlay only provides convenience
actions that trigger their remote installer, the same pattern we use for
Tailscale and OctoEverywhere.
