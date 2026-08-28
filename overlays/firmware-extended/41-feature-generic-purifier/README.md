# 41-feature-generic-purifier

Moves the purifier's exhaust and inner (circulation) fans off their hand-rolled
PWM/tachometer plumbing in `klippy/extras/purifier.py` and onto stock Klipper
`[fan_generic]` objects — `exhaust_fan` and `circulation_fan` — so they show up
like any other fan (`SET_FAN_SPEED FAN=exhaust_fan`, `printer.fan_generic
circulation_fan` in the object query API, Fluidd/Mainsail fan panels, etc.)
instead of being opaque to everything except the purifier's own
`SET_PURIFIER`/webhook API.

## How purifier presence is detected

Unrelated to the fan change, and left untouched here: `[purifier]` reads an ADC
pin (`power_det_pin`, `PA7` on U1) that senses a voltage divider on the
purifier's connector. When a purifier is plugged in the sensed value drops
below `power_det_threshold` (`0.88`, in the `0..1` ADC-fraction units Klipper
uses); when unplugged it floats/reads high. The raw ADC callback
(`_adc_callback`) requires `power_det_debounce_threshold` (default `3`)
consecutive samples to agree before flipping `self._power_detected`, to ignore
noise. Losing presence forces both fans off and resets the chamber mode to
`MODE_IDLE`; every speed-setting entry point (`SET_PURIFIER`, `SET_PURIFIER_MODE`,
the `control/purifier` webhook) rejects a nonzero speed while
`_power_detected` is `False`.

## What changed

- `patches/home/lava/origin_printer_data/config/01-purifier-fan-generic.patch` (`printer.cfg`):
  - Splits the pin/PWM config out of `[purifier]` into `[fan_generic exhaust_fan]`
    and `[fan_generic circulation_fan]`.
  - `[purifier]` now only references them by name: `exhaust_fan_name: exhaust_fan`,
    `inner_fan_name: circulation_fan`. The `power_enable_pin`/`power_det_pin`/
    `external_temp_sensor`/chamber-mode config is unchanged.
- `patches/home/lava/klipper/01-purifier-fan-generic.patch` (`purifier.py`):
  - Removes the `PurifierFan`/`PurifierFanTachometer` classes (their PWM pin
    setup, kick-start, and tachometer handling are exactly what `fan_generic`'s
    underlying `fan.Fan` already does).
  - `Purifier.__init__` now does
    `self.printer.load_object(config, 'fan_generic ' + name)` for
    `exhaust_fan_name`/`inner_fan_name` instead of building a `PurifierFan`
    from raw pins. Both keys are required (`config.get(...)` with no
    default) — a `[purifier]` section without both is a config error, since
    the U1's purifier always has both fans.
  - All internal speed/status access goes through the looked-up object's
    `.fan` attribute (`fan_generic`'s `fan.Fan` instance): `.fan.set_speed(...)`,
    `.fan.get_mcu()`, `.fan.last_fan_value`, `.fan.max_power`. `.get_status()`
    is unchanged since `PrinterFanGeneric.get_status()` already returns the
    same `{'speed', 'rpm'}` shape the old `PurifierFan.get_status()` did.
  - `PrinterFanGeneric.cmd_SET_FAN_SPEED()`, its `control/generic_fan`
    webhook, and the `M106`/`M107` fan-id mapping in `extras/fan.py` all
    drive a fan through `fan.set_speed_from_command()` — a second write path
    `set_exhaust_fan_speed()`/`set_inner_fan_speed()` (and everything they
    gate: `power_enable_pin`, presence detection, the delay-off timers)
    can't see. A new `PurifierFanRouter` wraps the `fan.Fan` instance each
    `[fan_generic]` object holds and overrides only `set_speed_from_command()`
    — delegating everything else through `__getattr__` — so every one of
    those entry points lands in `Purifier.set_exhaust_fan_speed()`/
    `set_inner_fan_speed()` alongside `SET_PURIFIER`. `Purifier` itself calls
    the real fan's `set_speed()` directly, which the router doesn't
    intercept, so there's no recursion. `Purifier.__init__` swaps each
    `[fan_generic]` object's `.fan` attribute for the router once, right
    after registering its own event handlers.
  - Since every write now goes through one place, the `_exhaust_fan_state`/
    `_inner_fan_state` bookkeeping `set_exhaust_fan_delay_turn_off()` and the
    filter work-time accrual used to depend on is gone — they read the fans'
    live `.fan.last_fan_value` instead, which can no longer disagree with
    reality.

Behavior (delay-off timers, dynamic exhaust-fan speed ramping, RPM fault
detection, `SET_PURIFIER`/`SET_PURIFIER_MODE`/`GET_PURIFIER`, the
`control/purifier` webhook) is unchanged — only how the fans are configured
and addressed changes.


## Provenance

This is the complete implementation from [justinh-rahb/u1-klipper PR #6](https://github.com/justinh-rahb/u1-klipper/pull/6), adapted to the Extended Firmware overlay layout and verified against the stock U1 1.6.0 root filesystem used by PAXX PR #663. It supersedes the incomplete/incorrectly laid-out implementation in PAXX PR #670.
