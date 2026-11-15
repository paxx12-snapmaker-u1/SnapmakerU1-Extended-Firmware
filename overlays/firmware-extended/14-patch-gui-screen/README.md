# gui-screen-fix

Prevents the stock Snapmaker touchscreen UI (`/usr/bin/gui`) from wedging the Rockchip VOP2 display pipeline (`vp0` / `rk_iommu ff407e00.iommu`) when the screen sleep timer blanks and unblanks the display.

## Root Cause

`/usr/bin/gui` contains two screen power functions:
- `system_module_set_screen_backlight_on_off(int is_on)` (at `0x188eb0`), which toggles `/sys/class/backlight/backlight/bl_power` between `"1"` (backlight off) and `"0"` (backlight on), but is never called.
- `system_module_force_screen_on_off(int is_on)` (at `0x188d40`), which opens `/sys/class/drm/card0-DPI-1/status` and writes `"off"` on sleep and `"on"` on wake.

Writing `"off"` to `/sys/class/drm/card0-DPI-1/status` forces the Linux DRM subsystem to simulate a connector hotplug disconnect (`rockchip_drm_output_poll_changed` -> `drm_fb_helper_hotplug_event` -> `vop2_crtc_atomic_disable`). On the Snapmaker U1's Rockchip SoC, `vop2_crtc_atomic_disable` repeatedly logs:

```text
rockchip-vop2 ff400000.vop: [drm:vop2_disable_all_planes_for_crtc] *ERROR* wait win close timeout
rockchip-vop2 ff400000.vop: [drm:vop2_crtc_atomic_disable] wait for vp0 dsp_hold timeout
```

When `rk_iommu ff407e00.iommu` times out waiting for the DMA stall (`Enable stall request timed out, status: 0x00008b`) and fails to detach (`iommu driver failed to attach the default/blocking domain`), every subsequent wake (`"on"`) fails in `vop2_crtc_atomic_enable` with:

```text
rockchip-drm display-subsystem: [drm:vop2_crtc_atomic_enable] *ERROR* vp0 failed to attach dma mapping, -16
```

This permanently leaves the physical LCD black (`-EBUSY`) until a full system reboot, while `/dev/fb0` in RAM continues updating for `fb-http` (Remote Screen).

## Solution

1. `libgui-screen-fix.so` (loaded via `/etc/hooks/lmd.d/40-gui-screen-fix.sh` only when stock `/usr/bin/gui` is active) intercepts `fopen("/sys/class/drm/card0-DPI-1/status", "w")` and redirects it to `/sys/class/backlight/backlight/bl_power`, translating `"off"` -> `"1"` (backlight off) and `"on"` -> `"0"` (backlight on) — matching the behavior of `/usr/bin/gui`'s own `system_module_set_screen_backlight_on_off`.
2. `/etc/hooks/lmd.d/40-gui-screen-fix.sh` also disables `drm_kms_helper` connector polling (`echo N > /sys/module/drm_kms_helper/parameters/poll`) on the fixed internal DPI panel.
3. Because the VOP2 CRTC (`vp0`) and `/dev/fb0` are never torn down during screen sleep, the physical LCD wakes instantaneously without risking an IOMMU `-EBUSY` lockup, and Remote Screen (`fb-http.py`) remains uninterrupted whether the physical backlight is on or off.
