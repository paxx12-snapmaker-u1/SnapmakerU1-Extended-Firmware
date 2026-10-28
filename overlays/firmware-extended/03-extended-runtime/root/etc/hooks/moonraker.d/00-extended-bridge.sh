# Bring /extended module activation into the Moonraker service lifecycle. Sourced by
# S61moonraker (see /etc/hooks/moonraker.d, overlays/firmware-extended/01-system-utils).
# See docs/design/modules.md for the activation model this implements.
#
# Config lands in printer_data/config/extended/moonraker/, which `02-firmware-config`
# already globs into moonraker.conf (`[include extended/moonraker/*.cfg]`). Components
# land directly in Moonraker's own moonraker/components/ — its loader imports
# `moonraker.components.<name>` as an ordinary package member, so placing the file where
# that package actually looks is simpler and more certain than relying on PYTHONPATH/
# namespace-package resolution. The rootfs overlay is writable at runtime (see
# docs/data_persistence.md), so a symlink there works the same as
# 38-feature-spoollink's build-time file does today.

_extended_moonraker_config_dir=/home/lava/printer_data/config/extended/moonraker
_extended_moonraker_components_dir=/home/lava/moonraker/moonraker/components

# Drops any symlink in <dir> that is dangling, or whose target is under /extended/ —
# every module that wants a file there recreates it below, so nothing that survives this
# is stale.
_extended_clean_dir() {
	dir=$1
	for link in "$dir"/*; do
		[ -L "$link" ] || continue
		if [ ! -e "$link" ]; then
			rm -f "$link"
			continue
		fi
		case "$(readlink "$link")" in
			/extended/*) rm -f "$link" ;;
		esac
	done
}

# Symlinks <source> to <link>, refusing (and logging) rather than silently overwriting
# anything already there that this pass's sweep didn't already clear — a real file (eg a
# stock Moonraker component) or another mechanism's symlink under the same name.
_extended_link() {
	source=$1
	link=$2
	if [ -e "$link" ] || [ -L "$link" ]; then
		logger -p user.err -t "extended[$$]" -- "refusing to overwrite existing $link"
		echo "extended: refusing to overwrite existing $link" >&2
		return 1
	fi
	ln -s "$source" "$link"
}

extended_activate_config() {
	for source in "$MODULE_DIR"/share/moonraker.d/config/*; do
		[ -f "$source" ] || continue
		link="$_extended_moonraker_config_dir/$(basename "$source")"
		_extended_link "$source" "$link" && chown -h lava:lava "$link"
	done
}

extended_activate_components() {
	for source in "$MODULE_DIR"/share/moonraker.d/components/*; do
		[ -f "$source" ] || continue
		_extended_link "$source" "$_extended_moonraker_components_dir/$(basename "$source")"
	done
}

if [ "$1" = start ]; then
	mkdir -p "$_extended_moonraker_config_dir"
	_extended_clean_dir "$_extended_moonraker_config_dir"
	_extended_clean_dir "$_extended_moonraker_components_dir"
fi

for MODULE_DIR in /extended/*; do
	[ -d "$MODULE_DIR" ] || continue
	[ ! -e "$MODULE_DIR/disabled" ] || continue
	MODULE=$(basename "$MODULE_DIR")
	export MODULE MODULE_DIR
	activate="$MODULE_DIR/share/moonraker.d/activate"
	[ -r "$activate" ] || continue
	. "$activate" "$1"
done
unset MODULE_DIR MODULE activate
