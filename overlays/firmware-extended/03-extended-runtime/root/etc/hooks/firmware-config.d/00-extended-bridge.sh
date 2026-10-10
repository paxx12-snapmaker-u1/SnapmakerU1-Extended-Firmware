# Bring /extended module settings into the Firmware Config UI. Sourced by
# S99firmware-config (see /etc/hooks/firmware-config.d, overlays/firmware-extended/02-firmware-config).
# See docs/design/modules.md for the activation model this implements.
#
# firmware-config.py takes a single --functions-dir, loaded as a flat glob, so this
# gathers the stock functions and every non-disabled module's
# share/firmware-config.d/config/*.yaml as symlinks into one merged directory and
# repoints FIRMWARE_CONFIG_FUNCTIONS_DIR there for S99firmware-config to use. Unlike
# klipper.d/moonraker.d, there is no activate step here: showing a settings panel has no
# side effect to gate, so every non-disabled module's functions are always gathered.
#
# Unlike S60klipper/S61moonraker, S99firmware-config's restart() calls its own start/stop
# shell functions directly instead of re-invoking the script — so this hook only runs
# once per invocation, with $1 possibly "restart", not "start". The merge always runs
# regardless of $1, rather than being gated on it, so a plain restart doesn't skip it.

FIRMWARE_CONFIG_FUNCTIONS_DIR=/home/lava/printer_data/config/extended/firmware-config
export FIRMWARE_CONFIG_FUNCTIONS_DIR

_extended_stock_functions_dir=/usr/local/share/firmware-config/functions

# Symlinks <source> to <link>, refusing (and logging) rather than silently overwriting
# anything already there that this pass's sweep didn't already clear — e.g. two modules
# (or a module and the stock functions) shipping a same-named file.
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

mkdir -p "$FIRMWARE_CONFIG_FUNCTIONS_DIR"

# Drops any symlink that is dangling, or whose target is under /extended/ or the
# stock functions directory — every run rebuilds these from scratch.
for link in "$FIRMWARE_CONFIG_FUNCTIONS_DIR"/*; do
	[ -L "$link" ] || continue
	if [ ! -e "$link" ]; then
		rm -f "$link"
		continue
	fi
	case "$(readlink "$link")" in
		/extended/*|"$_extended_stock_functions_dir"/*) rm -f "$link" ;;
	esac
done

for source in "$_extended_stock_functions_dir"/*; do
	[ -f "$source" ] || continue
	_extended_link "$source" "$FIRMWARE_CONFIG_FUNCTIONS_DIR/$(basename "$source")"
done

for MODULE_DIR in /extended/*; do
	[ -d "$MODULE_DIR" ] || continue
	[ ! -e "$MODULE_DIR/disabled" ] || continue
	MODULE=$(basename "$MODULE_DIR")
	export MODULE MODULE_DIR
	for source in "$MODULE_DIR"/share/firmware-config.d/config/*; do
		[ -f "$source" ] || continue
		_extended_link "$source" "$FIRMWARE_CONFIG_FUNCTIONS_DIR/$(basename "$source")"
	done
done
unset MODULE_DIR MODULE
