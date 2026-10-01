# Firmware Config Shortcut

Adds a small floating shortcut to Fluidd and Mainsail so users can discover the
Extended Firmware Config UI at `/firmware-config/` without remembering the URL.

The shortcut is injected as a separate script instead of patching frontend app
bundles. If the shortcut script fails to load, both web frontends continue to
work normally.
