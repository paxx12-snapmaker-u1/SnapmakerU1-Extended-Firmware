# No-USB Panda → DragonBreath migration (paxx 1.6.0) — design

Status: **Draft / design (local — do not push until bench-tested).** paxx 1.6.0 removes
the stock-Panda chamber-heater path and converts everyone to DragonBreath. Any user
still on **stock Panda firmware** with the old `panda_breath.cfg` gets a dead heater on
update unless we convert them — **over the network, no USB.** This overlay is where the
conversion lives; it builds on PR 604's structure.

## Already done (the hard half)
- **DragonBreath firmware v1.0.2** ships a first-boot NVS carry-over shim: after an
  OTA-over-stock it reads stock's `app_nvs` `wifi_info` / `moonraker_info` / `ha_mqtt_info`
  blobs and rejoins the same **WiFi + Moonraker + HA** with no re-provisioning
  (hardware-validated). So the device side of the flip is seamless — the printer side is
  what's left.
- **Klipper module (`dragonbreath-klipper`): no changes required.** It verifies the
  device is DragonBreath-v2 on handshake (won't accept stock), rides out the flash/reboot
  window with automatic reconnect+backoff, and exposes `connected`/`firmware_version` for
  post-flip verification. Migration = write `[dragonbreath] host` cfg + restart klippy.
  (An optional standalone `GET /api/v2/info` probe CLI could be added for the orchestrator
  to call pre-restart, but it can just do the HTTP check itself. Deferred.)

## Two execution phases (don't conflate)
- **Build-time** (`scripts/*.sh`, run in `create_firmware.sh` on the build host): bake the
  Klipper module and — new — the **pinned DragonBreath `.bin`** into the rootfs.
- **Runtime** (settings-YAML `cmd`/`get_cmd`, run on the U1 by `firmware-config.py`): the
  conversion orchestrator, launched from the chamber-heater dropdown.

## The conversion flow (runtime, on the U1)
Idempotent + resumable; consent-gated; fails loud (a quietly-dead heater after a forced
update is the worst outcome). Reuses the existing stdlib WS client + `fw_version` readback
as the stock-vs-DragonBreath discriminator.

1. **Detect / classify** the device at the user's IP (or `PandaBreath.local` /
   `dragonbreath.local`):
   - `GET http://<ip>/api/v2/info` answers `project=dragonbreath` → **already migrated, skip.**
   - stock WS `version` returns `V1.0.3`/`V1.0.4` → **convert.**
   - unreachable → surface "needs attention", let the user retry.
2. **Flash** the bundled `dragonbreath-<ver>.bin` to the stock firmware-update endpoint
   (⚠ **the one thing to RE — see below**). Stock writes it to the inactive OTA slot and
   reboots into it; the **stock app stays in the other slot** (boot-inactive rollback,
   since DragonBreath ships on the stock partition layout). No internet needed (image is
   bundled).
3. **Wait + verify.** Re-resolve `dragonbreath.local` / same IP; confirm
   `GET /api/v2/info` `project=dragonbreath` + version. WiFi/Moonraker/HA carried by the
   v1.0.2 shim.
4. **Swap the Klipper config.** Same mechanics PR 604 already uses: `rm` `panda_breath.cfg`
   (unbind first via `panda_breath_cli.py … unbind` while it's still stock, if reachable),
   `cp -n dragonbreath.cfg`, `extended-config.py add … dragonbreath host <ip>`, chown lava.
   Carry the user's setpoint/mode where we can.
5. **Reload Klipper** — `S60klipper restart` (process restart; the module only loads then,
   NOT `FIRMWARE_RESTART`).
6. **Verify + finalize** — poll the `dragonbreath` object (`connected`/`firmware_version`)
   via Moonraker; then mark the dropdown DragonBreath. On failure, keep the old cfg and
   report — never leave it half-swapped.

## Components to build (files, modeled on PR 604)
- **`scripts/03-bundle-dragonbreath-firmware.sh`** (build-time) — `cache_file.sh` the
  pinned `dragonbreath-<ver>.bin` (from the GitHub release, checksum-verified) into
  `root/usr/local/share/chamber-heater/dragonbreath.bin`. Version pinned alongside the
  `GIT_SHA` pin in `01-install-dragonbreath.sh`.
- **`root/usr/local/bin/panda_breath_cli.py`** (extend) OR a new
  **`dragonbreath_migrate.py`** — add the network flash: POST the `.bin` to stock's update
  endpoint + progress readback. Keep it stdlib-only (no new deps), like the existing CLI.
  Also the detect/classify helper (info-v2 probe + `fw_version`).
- **`25_settings_..._chamber_heater.yaml`** (edit) — **remove the `panda-auto` option**;
  the `dragonbreath` option's `cmd` gains the full detect→flash→verify→cfg-swap→reload
  orchestration (streaming progress to the web UI); `confirm` text reframed from "flash
  it yourself first" to "this upgrades your chamber heater over WiFi, reverts to stock if
  anything fails"; optionally an `if_cmd`-gated "Convert to DragonBreath" that only shows
  when a stock Panda is detected. `get_cmd` detection unchanged (cfg-file presence).
- **Remove Panda:** delete `scripts/02-install-panda-breath.sh`, the `panda_breath.py`
  install, `panda_breath.cfg` + `panda_breath_heater_auto.cfg`, and the `panda-auto`
  branch; retire `docs/panda_breath.md` in favour of a DragonBreath doc.
- **`test/run.sh`** (extend) — after `scp root/` + klippy restart, invoke the migration
  entrypoint over SSH against a bench device (stock Panda) and assert it ends up on
  DragonBreath. Per Justin: **SSH-iterate the scripts on a live U1, CI-build the image only
  once for the final test.**

## ⚠ The one open unknown — the stock firmware-update call
The stock Panda's web UI *does* accept a firmware upload over the network (users OTA'd
DragonBreath 1.0.x that way), but this overlay has **no** flasher and the exact request
isn't captured here. Two candidates to confirm from the stock firmware (we have the
binary + a bench device):
- an **HTTP `POST /ota`** (raw octet-stream; per the `dragonbreath-webflash-installer`
  RE notes), or
- a **`/ws` command** that streams the image.

**This is the first implementation task**: RE the exact endpoint/headers/chunking and
validate it flashes `dragonbreath-<ver>.bin` on the bench, before wiring the orchestrator.

## Safety / rollback (forced flash)
Stock always bootable via **boot-inactive** (stock stays in the inactive slot; bootloader
+ partition table untouched); dual-OTA auto-rollback on a bad image; **staged config**
(keep `panda_breath.cfg` until DragonBreath verifies live); fail loud with a retry, never
a silent dead heater.

## Open questions
1. Exact stock update request (above) — RE + bench-validate first.
2. Consent/trigger: auto-detect-and-prompt on the dropdown vs a one-tap "Convert" option
   (recommended: one tap, since it's a forced firmware flash).
3. Settings carry-over: which stock settings map to DragonBreath (setpoint, AUTO
   threshold, filter) — enumerate + default the rest.
4. `host` value: `dragonbreath.local` (mDNS) vs the device's DHCP IP (same MAC usually
   keeps the lease; fall back to a subnet probe).
5. Version pinning of the bundled `.bin` across paxx releases.
