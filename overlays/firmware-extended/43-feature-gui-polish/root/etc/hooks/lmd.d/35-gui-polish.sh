# Show Polish on the stock touchscreen GUI when `[components] gui_language`
# selects it.
#
# The stock GUI has a fixed, compiled-in list of languages, so Polish is shown
# in place of Turkish: the Polish texts are bind-mounted over the `tr-TR` files
# and a copy of `/usr/bin/gui` with the menu label "Türkçe" renamed to "Polski"
# is bind-mounted over the stock binary. Nothing on disk changes; selecting
# `stock` (or a reboot without the setting) restores the stock files.
#
# Ordered after `30-helixscreen.sh`: nothing is done while HelixScreen is the
# selected GUI, and `/usr/bin/gui` is never unmounted while it is.
if [ "$1" = start ]; then
	GUIPL_DIR=/usr/local/share/gui-polish
	GUIPL_CFG=/oem/printer_data/config/extended/extended2.cfg
	GUIPL_RES=/home/lava/resource
	GUIPL_BIN=/usr/bin/gui

	GUIPL_LANG=$(/usr/local/bin/extended-config.py get "$GUIPL_CFG" components gui_language stock 2>/dev/null)
	GUIPL_GUI=$(/usr/local/bin/extended-config.py get "$GUIPL_CFG" components gui snapmaker 2>/dev/null)

	guipl_bound() { grep -q " $1 " /proc/mounts; }
	guipl_bind() { guipl_bound "$2" || mount -o ro --bind "$1" "$2"; }
	guipl_unbind() { ! guipl_bound "$1" || umount "$1" || umount -l "$1"; }

	if [ "$GUIPL_LANG" = polish ] && [ "$GUIPL_GUI" != helixscreen ]; then
		guipl_bind "$GUIPL_DIR/pl.json" "$GUIPL_RES/locales/tr-TR.json"
		guipl_bind "$GUIPL_DIR/exception_pl.json" "$GUIPL_RES/text/exception_tr-TR.json"
		# The relabelled binary is only built when the label was found.
		if [ -x "$GUIPL_DIR/gui" ]; then
			guipl_bind "$GUIPL_DIR/gui" "$GUIPL_BIN"
		fi
		logger -p user.info -t "lmd[$$]" -- "Polish touchscreen language enabled (replaces Turkish)"
	else
		guipl_unbind "$GUIPL_RES/locales/tr-TR.json"
		guipl_unbind "$GUIPL_RES/text/exception_tr-TR.json"
		if [ "$GUIPL_GUI" != helixscreen ]; then
			guipl_unbind "$GUIPL_BIN"
		fi
	fi

	unset GUIPL_DIR GUIPL_CFG GUIPL_RES GUIPL_BIN GUIPL_LANG GUIPL_GUI
	unset -f guipl_bound guipl_bind guipl_unbind
fi
