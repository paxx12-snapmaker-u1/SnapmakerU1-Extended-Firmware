# HelixScreen

Adds Firmware Config functionality to install and manage [HelixScreen](https://helixscreen.org/),
an alternative touchscreen UI for the Snapmaker U1.

## What it does

- **Settings > Snapmaker Components > HelixScreen** — shows installed status; toggle
  "Not Installed" to install, toggle "Installed" to uninstall (restores stock UI)
- **Actions > System > Update HelixScreen** — re-runs the official installer to
  update to the latest version (only visible when installed)

No HelixScreen source code is bundled. The overlay only provides convenience
actions that trigger their remote installer, the same pattern we use for
Tailscale and OctoEverywhere.
