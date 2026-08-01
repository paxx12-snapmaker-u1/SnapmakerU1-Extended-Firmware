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
update is the worst outcome).

**No device-firmware detection is needed.** Mainline paxx never shipped DragonBreath —
only testers run it (via the PR 604 build), and they already have `dragonbreath.cfg`. So
the population is unambiguous and the existing host-side `get_cmd` already classifies it:
`panda_breath.cfg` present ⇒ **stock user ⇒ migrate**; `dragonbreath.cfg` present ⇒
already on DragonBreath ⇒ **skip**; neither ⇒ disabled. The trigger is the config state,
not a probe of the device.

1. **Read the device host** from `panda_breath.cfg` (`host`, e.g. `PandaBreath.local` or
   the saved IP) — that's where we POST. One small **idempotency/resume guard**: a quick
   `GET http://<host>/api/v2/info` — if it *already* answers `project=dragonbreath` (a
   prior run flashed but didn't finish the cfg-swap), **skip the flash and jump to the cfg
   swap.** This is "already done?", not "is it stock?". Unreachable ⇒ surface "needs
   attention" and let the user retry.
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

## The stock firmware-update call — RESOLVED + validated ✅
RE'd from the stock web UI (`ota_post_file`), and **flashed end-to-end on a bench device
from BOTH stock 1.0.3 and 1.0.4 → DragonBreath, no USB** (same app binary → identical
`/ota` handler on both). It's a plain HTTP POST:

```
POST /ota HTTP/1.1
Host: <panda-ip>
Content-Type: application/octet-stream;charset=UTF-8
OTA-Type: ota_fw

<raw dragonbreath-<ver>.bin>          # size ceiling 0x480000 (4.5 MB); our image ~1.08 MB
```
e.g. `curl -X POST -H 'OTA-Type: ota_fw' -H 'Content-Type: application/octet-stream' \
      --data-binary @dragonbreath.bin http://<ip>/ota` → **HTTP 200**, stock writes it
to the **inactive OTA slot** and reboots into it. Verified on the bench: device came up
`project=dragonbreath`, `inactive_slot=panda_breath` (stock intact for boot-inactive
rollback), and the v1.0.2 shim carried **WiFi + Moonraker** over automatically. The whole
device-flip is therefore proven — the orchestrator just wraps this POST plus a
poll-for-`/api/v2/info` and the cfg swap. Stdlib-only (`urllib`/`http.client`), no deps.

## Consent + the "not yet migrated" state
Prompt on **both** surfaces; simple **Accept / Deny** (no typed acknowledgement).
- **/firmware-config (web UI) — decided, easy:** the settings-YAML `confirm` block IS the
  Accept (proceed) / Deny (cancel), shown before the migrate `cmd` runs; explains the
  over-WiFi flash + auto-revert-on-failure; streaming `cmd` narrates progress.
- **U1 main touchscreen — wanted, mechanism UNRESOLVED.** The main screen is **Snapmaker's
  native UI** drawing to `/dev/fb0`; paxx only *mirrors* it (`fb-http` in `screen-apps`,
  served at `127.0.0.1:8092`) and does **not** render it. There is **no modal/notification
  hook in the overlays** (no KlipperScreen/Guppy/action-prompt support). So an interactive
  Accept/Deny on the native screen is not a simple injection — it depends on whether that
  screen surfaces Klipper `M117`/messages/action-prompts or exposes a notification API.
  **Determine this on a LIVE U1** (read-only: what process draws `/dev/fb0`, does the
  native screen show Klipper messages/prompts, any notify path) before designing it.
  Likely outcomes, best→worst: a Klipper **action-prompt** with Accept/Deny buttons (if
  the screen renders it) → a text **notice** via `M117`/Klipper message that directs the
  user to Settings (non-interactive) → real screen-stack integration (bigger lift).
- **Decline → Disabled:** if the user doesn't confirm (or picks Disabled), the chamber
  heater is set to **Disabled** (clean, no broken heater object) and the manual route
  stays open (flash DragonBreath themselves → pick the DragonBreath option). No forced
  flash without consent.
- **First-boot safety (must-have):** 1.6.0 removes the Panda Klipper module at build time,
  so a user still carrying `panda_breath.cfg` who hasn't migrated would make **klippy fail
  to load `[panda_breath]`**. A first-boot runtime hook must **auto-disable a lingering
  `panda_breath.cfg`** (comment/rename so klippy starts clean) and set a "migration
  pending" flag — which is what makes the migrate action appear (`if_cmd`-gated). So the
  un-consented state is a *disabled* heater, never a *broken printer*.

## Safety / rollback (forced flash)
Stock always bootable via **boot-inactive** (stock stays in the inactive slot; bootloader
+ partition table untouched); dual-OTA auto-rollback on a bad image; **staged config**
(keep `panda_breath.cfg` until DragonBreath verifies live); fail loud with a retry, never
a silent dead heater.

## Open questions
1. ~~Exact stock update request~~ — **RESOLVED + validated on 1.0.3 and 1.0.4** (POST `/ota`, above).
2. Consent — firmware-config **Accept/Deny** DECIDED (`if_cmd`-gated Convert action +
   `confirm`; decline → Disabled; first-boot auto-disables a lingering `panda_breath.cfg`).
   **U1 touchscreen prompt still open** — mechanism must be found on a live U1 (native
   Snapmaker screen; no overlay hook). See "Consent + the 'not yet migrated' state".
3. Settings carry-over: which stock settings map to DragonBreath (setpoint, AUTO
   threshold, filter) — enumerate + default the rest.
4. `host` value: `dragonbreath.local` (mDNS) vs the device's DHCP IP (same MAC usually
   keeps the lease; fall back to a subnet probe).
5. Version pinning of the bundled `.bin` across paxx releases.
