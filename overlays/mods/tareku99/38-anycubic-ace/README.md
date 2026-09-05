# Experimental MultiACE-derived Anycubic ACE integration

This personal mod adds an experimental Anycubic ACE Pro / ACE 2 Pro path to a
Snapmaker U1. It uses the protocol, device-management, retry, RFID, and
load/unload integration patterns from [multiACE](https://github.com/decay71/multiACE)
and keeps the implementation disabled until it is selected in Firmware Config.

Build it with:

```bash
./dev.sh make build PROFILE=extended-tareku99
```

After flashing the image:

1. Connect one to four ACE units to the U1.
2. Enable Advanced Mode.
3. Open `http://<printer-ip>/firmware-config/`.
4. Under **Settings > Snapmaker Components**, enable **Anycubic ACE
   (experimental)**.

Firmware Config switches the MultiACE-derived Klipper modules into place,
installs the ACE include, and restarts Klipper. Disabling the setting restores
the stock U1 modules before restarting. The stock path therefore remains the
fallback while this integration is being validated.

The runtime supports ACE Pro (JSON/V1) and ACE 2 Pro (protobuf/V2), one to four
devices, stable device ordering, per-device slot state, RFID metadata, feed
assist, load/unload retries, and head-to-ACE/slot mapping. The optional
MultiACE web service and online updater are deliberately not bundled in this
firmware overlay; activation is managed by Firmware Config instead.

Set `ace_device_count` in the installed `ace.cfg` before enabling the feature
when using more than one ACE. Start with the default of `1` until device
ordering and the physical tube splitters have been verified.

For connector pinouts, wiring, commands, attribution, and the first hardware
test checklist, see the [Anycubic ACE wiring and test guide](../../../../docs/anycubic_ace.md).

This mod is not hardware-validated yet. Keep the pull request as a draft until
the complete status, load, unload, RFID, and recovery paths have been tested
on a real U1 with the intended ACE hardware.
