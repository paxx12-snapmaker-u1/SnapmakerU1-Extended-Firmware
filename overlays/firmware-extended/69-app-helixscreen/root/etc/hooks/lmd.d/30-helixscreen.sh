# Swap the touchscreen GUI for HelixScreen when `[components] gui` selects it.
#
# `lmd` forks `/usr/bin/gui` from a compiled-in path, so HelixScreen is bind-
# mounted over that path instead of patching a launcher. `lmd` validates its
# child against `/proc/<pid>/exe`, which a `#!` script would fail — bind the
# ELF directly instead of upstream's launcher script.
#
# Ordered after `20-camera-selection.sh`, which aborts when there's no GUI to
# set up. Upstream's `platform/hooks.sh` is not sourced: it re-launches `lmd`
# (racing our own init script) and starts `fb-http` (we leave that alone).
if [ "$1" = start ]; then
	HELIX_ROOT=/oem/apps/helixscreen/latest
	HELIX_CFG=/oem/printer_data/config/extended/extended2.cfg
	HELIX_CFG_DIR=/oem/printer_data/config/extended/helixscreen
	HELIX_GUI=/usr/bin/gui

	HELIX_SELECTED=$(/usr/local/bin/extended-config.py get "$HELIX_CFG" components gui snapmaker 2>/dev/null)
	grep -q " $HELIX_GUI " /proc/mounts && HELIX_BOUND=yes || HELIX_BOUND=

	if [ "$HELIX_SELECTED" = helixscreen ] && [ -x "$HELIX_ROOT/bin/helix-screen" ]; then
		# Restart wpa_supplicant with GUI controlled `wpa_supplicant.conf`
		HELIX_WPA_CONF=/oem/printer_data/gui/wpa_supplicant.conf
		mkdir -p "$(dirname "$HELIX_WPA_CONF")"
		cp -n /etc/wpa_supplicant.conf "$HELIX_WPA_CONF"
		chown -R lava:lava "$HELIX_WPA_CONF"
		killall wpa_supplicant 2>/dev/null
		sleep 1
		wpa_supplicant -B -i wlan0 -c "$HELIX_WPA_CONF"
		unset HELIX_WPA_CONF

		# Assets resolve from `/proc/self/exe`, which the bind mount makes `/usr/bin/gui`.
		export HELIX_DATA_DIR="$HELIX_ROOT"
		# An in-app restart exits for `lmd` to respawn, instead of forking a second instance.
		export HELIX_SUPERVISED=1
		# Skip connector auto-detection, which can race the DRM device at boot.
		export HELIX_DRM_DEVICE=/dev/dri/card0
		export HELIX_CACHE_DIR=/userdata/helixscreen/cache
		# Keep settings under the extended config dir, which survives a firmware upgrade;
		# `$HELIX_ROOT` doesn't.
		export HELIX_CONFIG_DIR="$HELIX_CFG_DIR"
		if [ ! -d "$HELIX_CFG_DIR" ]; then
			mkdir -p "$HELIX_CFG_DIR"
			chown -R lava:lava "$HELIX_CFG_DIR"
		fi

		# HelixScreen never writes to `/dev/fb0`, so mirror its frames there for `fb-http`.
		export HELIX_REMOTE_SCREEN_FB0=/dev/fb0

		if [ -z "$HELIX_BOUND" ]; then
			if mount -o ro --bind "$HELIX_ROOT/bin/helix-screen" "$HELIX_GUI"; then
				logger -p user.info -t "lmd[$$]" -- "HelixScreen bound over $HELIX_GUI"
			fi
		fi
	elif [ -n "$HELIX_BOUND" ]; then
		umount "$HELIX_GUI" || umount -l "$HELIX_GUI"
		logger -p user.info -t "lmd[$$]" -- "Restored stock $HELIX_GUI"
	fi

	unset HELIX_ROOT HELIX_CFG HELIX_CFG_DIR HELIX_GUI HELIX_SELECTED HELIX_BOUND
fi
