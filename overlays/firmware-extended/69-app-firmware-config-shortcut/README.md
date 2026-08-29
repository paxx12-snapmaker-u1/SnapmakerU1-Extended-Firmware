# Firmware Config shortcut

Adds a small floating shortcut to Fluidd and Mainsail so users can discover the
Extended Firmware Config UI at `/firmware-config/` without remembering the URL.

The shortcut is intentionally injected as a separate script instead of patching
Fluidd or Mainsail application bundles. If the script fails to load, both web
frontends continue to work normally.
