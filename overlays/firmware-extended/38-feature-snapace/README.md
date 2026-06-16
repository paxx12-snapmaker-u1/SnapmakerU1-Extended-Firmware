# SnapAce ACE Pro integration

This overlay adds optional Anycubic ACE Pro support based on the SnapAce
project:

https://github.com/BlackFrogKok/SnapAce

Upstream reference: `BlackFrogKok/SnapAce`
`34a06e87bcd59ca3ebc845ed32a794627505437c` as inspected during
implementation. SnapAce is GPL-3.0 licensed, matching this project's GPL-3.0
license. The integration is adapted into Extended Firmware overlays so users do
not need to install SnapAce manually or replace Klipper files by hand.

The feature is disabled by default. Enabling it from Firmware Config installs
`ace.cfg` into the extended Klipper include directory and restarts Klipper.
