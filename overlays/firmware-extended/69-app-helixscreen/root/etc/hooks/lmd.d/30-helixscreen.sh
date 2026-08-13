# Swap the touchscreen GUI for HelixScreen when `[components] gui` selects it.
#
# `lmd` forks `/usr/bin/gui` from a compiled-in path, so there is no init script
# to redirect and no launcher to patch. Bind-mounting HelixScreen over
# `/usr/bin/gui` before `lmd` forks it is launcher-independent: `lmd` execs the
# same path it always did and supervises HelixScreen exactly as it would the
# stock UI. The stock binary never runs at all, so it never takes the DRM master
# and no keepalive dance is needed to hand the display over.
#
# The `helix-screen` ELF is bound directly rather than upstream's launcher
# script: `lmd` validates its child against `/proc/<pid>/exe` and the
# `/proc/<pid>/stat` name ("PID %d reused" / "PID %d name mismatch"). A `#!`
# script execs its interpreter, so `exe` would read `/bin/sh` and fail that
# check; binding the ELF makes `exe` resolve to `/usr/bin/gui`.
#
# Ordered after `20-camera-selection.sh`, which aborts the init script outright
# when the camera selection leaves `lmd` unused — no GUI to set up in that case.
#
# Upstream's `platform/hooks.sh` is deliberately not sourced. It exists to serve
# their launcher, and everything in it is either already ours or actively wrong
# here: it re-launches `lmd` when it finds it stopped (which is exactly the case
# while `lmd`'s own init script is running, so it would race a second `lmd` in),
# and it starts `fb-http` because their installer hijacks `S99fb-http` — we
# leave that init script alone, so it still serves the remote screen itself.
# The two environment exports it would set are set directly below.
#
# Both directions are applied here rather than at selection time: `lmd` has not
# forked the GUI yet, so the mount is never busy, and every start re-reads the
# config and converges on it.
#
# WiFi needs nothing from this hook: `14-patch-wifi-persist` points
# `wpa_supplicant` at a persistent config for both UIs, so a network joined in
# HelixScreen survives a reboot on its own.
if [ "$1" = start ]; then
	HELIX_ROOT=/oem/apps/helixscreen/latest
	HELIX_CFG=/oem/printer_data/config/extended/extended2.cfg
	HELIX_CFG_DIR=/oem/printer_data/config/extended/helixscreen
	HELIX_GUI=/usr/bin/gui

	HELIX_SELECTED=$(/usr/local/bin/extended-config.py get "$HELIX_CFG" components gui snapmaker 2>/dev/null)
	grep -q " $HELIX_GUI " /proc/mounts && HELIX_BOUND=yes || HELIX_BOUND=

	if [ "$HELIX_SELECTED" = helixscreen ] && [ -x "$HELIX_ROOT/bin/helix-screen" ]; then
		# Mandatory: HelixScreen otherwise resolves `ui_xml/` and `assets/`
		# relative to `/proc/self/exe`, which the bind mount makes `/usr/bin/gui`.
		export HELIX_DATA_DIR="$HELIX_ROOT"
		# Tells HelixScreen a supervisor owns its lifecycle, so an in-app restart
		# exits for `lmd` to respawn instead of forking a second instance.
		export HELIX_SUPERVISED=1
		# Skip connector auto-detection, which can race the DRM device at boot.
		export HELIX_DRM_DEVICE=/dev/dri/card0
		export HELIX_CACHE_DIR=/userdata/helixscreen/cache
		# Settings, themes and spool assignments otherwise resolve to `config`
		# relative to the install root, which is `$HELIX_ROOT` — the versioned
		# directory an upgrade deletes. Keep them in the extended config dir,
		# which persists across firmware upgrades and is already backed up and
		# reset by the recovery flags.
		export HELIX_CONFIG_DIR="$HELIX_CFG_DIR"
		if [ ! -d "$HELIX_CFG_DIR" ]; then
			mkdir -p "$HELIX_CFG_DIR"
			chown lava:lava "$HELIX_CFG_DIR"
		fi

		# `S99fb-http` snapshots `/dev/fb0` for the remote screen, but
		# HelixScreen renders into its own DRM buffer and never writes the
		# framebuffer, so `/screen/` would sit frozen on whatever was there
		# last. Have it mirror every rendered frame into `/dev/fb0` instead.
		#
		# Not gated on `web remote_screen`: that toggle restarts `S99fb-http`,
		# not `lmd`, so a config check here would still reflect boot time and
		# turning the remote screen on would serve a frozen screen until an
		# unrelated reboot. The mirror only copies frames the UI actually
		# redraws, on a 480x320 panel, so paying for it always beats a setting
		# that silently does nothing.
		export HELIX_REMOTE_SCREEN_FB0=/dev/fb0

		if [ -z "$HELIX_BOUND" ]; then
			if mount -o ro --bind "$HELIX_ROOT/bin/helix-screen" "$HELIX_GUI"; then
				logger -p user.info -t "lmd[$$]" -- "HelixScreen bound over $HELIX_GUI"
			fi
		fi
	elif [ -n "$HELIX_BOUND" ]; then
		# Stock UI selected again, or HelixScreen removed while still selected —
		# drop the bind so `lmd` forks the real Snapmaker binary.
		umount "$HELIX_GUI" || umount -l "$HELIX_GUI"
		logger -p user.info -t "lmd[$$]" -- "Restored stock $HELIX_GUI"
	fi

	unset HELIX_ROOT HELIX_CFG HELIX_CFG_DIR HELIX_GUI HELIX_SELECTED HELIX_BOUND
fi
