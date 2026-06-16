# Anycubic ACE Pro / ACE 2 Pro integration

Overlay: `38-feature-anycubic-ace`

This overlay adds optional Anycubic ACE Pro / ACE 2 Pro support based on
upstream SnapAce and U1-focused ACE references:

https://github.com/BlackFrogKok/SnapAce
https://github.com/DnG-Crafts/U1-Ace
https://github.com/hakimio/U1-Ace/tree/ace2

Upstream reference: `BlackFrogKok/SnapAce`
`34a06e87bcd59ca3ebc845ed32a794627505437c` as inspected during
implementation. Additional U1/ACE references: `DnG-Crafts/U1-Ace`
`f845339800445269069a60a55c9e517911c3f2f4` and `hakimio/U1-Ace` branch `ace2`
`97e94b11f6f9b52e045dc89919f69405dda1d9cf`.

SnapAce is GPL-3.0 licensed, matching this project's GPL-3.0 license. The
integration is adapted into Extended Firmware overlays so users do not need to
install ACE mod repositories manually or replace Klipper files by hand.

The feature is disabled by default. Enabling it from Firmware Config installs
`ace.cfg` into the extended Klipper include directory and restarts Klipper.
