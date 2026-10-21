# Test fixture

`heater_fan_upstream.py` is a copy of `klippy/extras/heater_fan.py` from
[Snapmaker/u1-klipper](https://github.com/Snapmaker/u1-klipper), the Klipper
source the extended firmware builds against.

`test_fan_curves.py` applies the patch in
`overlays/firmware-extended/11-patch-klipper/patches/home/lava/klipper/09_heater_fan_fan_curves.patch`
to this copy and drives the patched callback, so the patch can be checked on a
build machine instead of on a printer.

Do not edit the file: the patch context lines have to match it byte for byte.
