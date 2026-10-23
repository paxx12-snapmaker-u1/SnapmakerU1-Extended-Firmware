# Bring /extended module activation into the LMD service lifecycle. Sourced by S90lmd
# (see /etc/hooks/lmd.d, overlays/firmware-extended/01-system-utils). LMD has no
# config/extras equivalent, so this only sources each module's activate script.
# See docs/design/modules.md.

for MODULE_DIR in /extended/*; do
	[ -d "$MODULE_DIR" ] || continue
	[ ! -e "$MODULE_DIR/disabled" ] || continue
	MODULE=$(basename "$MODULE_DIR")
	export MODULE MODULE_DIR
	activate="$MODULE_DIR/share/lmd.d/activate"
	[ -r "$activate" ] || continue
	. "$activate" "$1"
done
unset MODULE_DIR MODULE activate
