---
title: Klipper and Moonraker Custom Includes
---

# Klipper and Moonraker Custom Includes


Add custom Klipper and Moonraker configuration files through the Fluidd/Mainsail web interface.

Configuration files are automatically included from:
- Klipper: `extended/klipper/*.cfg`
- Moonraker: `extended/moonraker/*.cfg`

## Usage

1. Open Fluidd or Mainsail (`http://<printer-ip>`)
2. Go to **Configuration** tab
3. Navigate to **extended/klipper/** or **extended/moonraker/** folder
4. Create `.cfg` files with your custom configuration
5. Restart the respective service after making changes

### Example: Custom Klipper Macro

Create `extended/klipper/custom-macros.cfg`:

```cfg
[gcode_macro CUSTOM_MACRO]
gcode:
    G28
    G1 Z10 F600
```

For camera configuration examples, see [Camera Support](camera_support.md#moonraker-camera-configuration).

## Important Notes

- All `.cfg` files in `extended/klipper/` and `extended/moonraker/` are automatically included
- Configuration files persist across reboots
- Do not modify or remove the `00_keep.cfg` placeholder files
- Invalid configuration will prevent Klipper/Moonraker from starting
- Test changes carefully before printing

## Configuration Recovery

If an invalid configuration breaks Moonraker (printer won't connect to WiFi), see [Firmware Configuration - Recovery & Reset](firmware_config.md#recovery--reset) for recovery instructions.

## See Also

- [Klipper Print Hooks](klipper_hooks.md) - React to `PRINT_START`, `PRINT_END`, and `CANCEL_PRINT` events from your custom includes
