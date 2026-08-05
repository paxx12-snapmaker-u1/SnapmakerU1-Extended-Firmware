# Experimental Anycubic ACE mod

This personal mod is an intentionally small hardware-test path for one Anycubic
ACE Pro or ACE 2 Pro connected to a Snapmaker U1.

It is disabled by default. Build it with:

```bash
./dev.sh make build PROFILE=extended-tareku99
```

After installing that firmware, connect the ACE and enable **Anycubic ACE
(experimental)** in Firmware Config. The setting creates the live Klipper
include, then restarts Klipper. The ACE model and serial settings are detected
automatically.

The load path remains the U1's normal filament-loading flow:

1. The U1 requests a slot load.
2. The ACE feeds the selected slot.
3. The U1 physical filament/runout sensor ends the ACE feed.
4. The existing U1 load, heat, extrude, and flush steps continue.

The four load_length_slotN values in ace.cfg are deliberately exposed for
tube-length tuning. Start with status and temperature commands, then test one
slot at a time. This first pass delegates the active load feed; unload behavior
is not hardware-validated yet. If the ACE path is not usable, disable the
setting and the stock feeder path is restored after the Klipper restart.

This mod is not hardware-validated yet. Keep it as a draft experiment until
the first complete load test is confirmed on a real U1 and ACE.
