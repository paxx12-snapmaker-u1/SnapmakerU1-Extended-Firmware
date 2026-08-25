# Spoolman Auto Pressure Advance (PA) Flow Calibration MOD

This MOD extends the Spoolman integration (`spoollink`) in `SnapmakerU1-Extended-Firmware`.

Because the ideal Pressure Advance depends on the installed nozzle, PA values are
stored **per nozzle** in a JSON matrix instead of a single value. The matrix is
keyed by `"<nozzle_diameter>_<nozzle_volume_type>"`, using the raw values reported
by Klipper (nozzle diameter such as `0.2` / `0.4` / `0.6` / `0.8`, and volume type
`standard` or `high_flow`).

Example `extra.pressure_advance_matrix` value:

```json
{
  "0.4_standard": 0.222,
  "0.4_high_flow": 0.223,
  "0.2_standard": 0.33
}
```

## Behaviour

1. **Auto-creates the `pressure_advance_matrix` Extra Field** in Spoolman (text field
   holding the serialized JSON matrix, on the spool entity).
2. **Auto-applies PA on load**: when a spool is assigned/loaded to an extruder channel
   (via RFID card or UI), Klipper builds the key for the channel's current nozzle and,
   if the matrix has an entry for it, applies that Pressure Advance. If there is no
   entry for the current nozzle, the channel is queued for automatic calibration
   (when `auto_pa` is enabled).
3. **Auto-saves PA on calibration**: when automatic flow calibration (`FLOW_CALIBRATE`)
   completes, the calibrated K-factor is written back to the active spool under the key
   for the nozzle used during calibration. Existing entries for other nozzles are
   preserved (the matrix is merged, not overwritten).

## Notes

- `nozzle_volume_type` already exists in the base firmware and defaults to `standard`;
  `high_flow` is used automatically once selected. No firmware-side mapping is applied,
  the raw Klipper values are used verbatim as key components.
- The previous single-value `pressure_advance` field is no longer read or written. It
  is left untouched in Spoolman for spools that still have it, but it is ignored.
