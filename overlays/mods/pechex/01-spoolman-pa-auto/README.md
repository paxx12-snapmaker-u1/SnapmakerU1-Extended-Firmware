# Spoolman Auto Pressure Advance (PA) Flow Calibration MOD

This MOD extends the Spoolman integration (`spoollink`) in `SnapmakerU1-Extended-Firmware`:

1. **Auto-creates `pressure_advance` Extra Field** in Spoolman (Float type).
2. **Auto-applies `pressure_advance`**: When a spool is assigned/loaded to an extruder channel (via RFID card or UI), Klipper automatically applies the spool's saved Pressure Advance setting.
3. **Auto-saves `pressure_advance`**: When automatic flow calibration (`FLOW_CALIBRATE`) completes, the calibrated K-factor (Pressure Advance) is automatically saved to the active spool's `extra.pressure_advance` field in Spoolman.
