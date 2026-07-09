#!/bin/sh
#
# install-helixscreen.sh - Install (or uninstall) HelixScreen on a Snapmaker U1
#                          running paxx12's extended firmware.
#
# HelixScreen (https://helixscreen.org) is a native LVGL touchscreen UI for
# Klipper with a dedicated Snapmaker U1 backend (RFID spool recognition,
# per-slot spool assignment, runout recovery). Installing it replaces the
# stock Snapmaker touchscreen UI until it is uninstalled.
#
# This script wraps the official HelixScreen installer and adds two
# extended-firmware-specific behaviors:
#
#   1. Overlay persistence is required up front (/oem/.debug), like the other
#      add-on recipes - enable it via firmware-config (Settings > System >
#      Overlay Persistence). The official installer would silently create the
#      flag itself; failing fast keeps it an explicit user choice.
#
#   2. Remote Screen restore: HelixScreen's autostart hook replaces
#      /etc/init.d/S99fb-http (the extended firmware's Remote Screen launcher)
#      with a delegate that starts HelixScreen INSTEAD of the fb-http daemon,
#      preserving the original as S99fb-http.stock. That silently breaks
#      http://<printer-ip>/screen/ on the next boot. We start the preserved
#      stock launcher right after install; at boot the same is done by
#      /etc/init.d/S99fb-http-restore (shipped in the firmware image by the
#      St0rmingBr4in mod overlay). S99fb-http.stock itself no-ops unless
#      Remote Screen is enabled in extended2.cfg, so this is always safe.
#
# What the official installer does on the U1 (for reference):
#   - installs to /userdata/helixscreen/
#   - hooks boot via overlay copies of S99screen / S99fb-http /
#     S99input-event-daemon
#   - disables the stock UI binary with `chmod a-x /usr/bin/gui` (the stock
#     supervisor lmd keeps trying to respawn it and gives up; harmless)
#
# Uninstall restores the stock init scripts and the /usr/bin/gui exec bit,
# but the stock UI only reappears after a reboot.
#
# Idempotent: rerunning updates HelixScreen to the latest release.
#
# Usage: ssh root@<printer-ip> 'sh -s' < install-helixscreen.sh
#        install-helixscreen.sh [--uninstall]
#

set -eu

INSTALLER_URL="https://raw.githubusercontent.com/prestonbrown/helixscreen/main/scripts/install.sh"
HELIX_INIT=/userdata/helixscreen/config/helixscreen.init

log() { printf '[install-helixscreen] %s\n' "$*"; }
die() { printf '[install-helixscreen] ERROR: %s\n' "$*" >&2; exit 1; }

MODE=install
if [ "${1:-}" = "--uninstall" ]; then
    MODE=uninstall
elif [ $# -gt 0 ]; then
    die "unknown argument: $1 (only --uninstall is supported)"
fi

# ---- sanity checks ---------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "must run as root"

if ! grep -q '^ID=buildroot' /etc/os-release 2>/dev/null; then
    die "not a Buildroot system - refusing to run"
fi

if ! id lava >/dev/null 2>&1; then
    die "user 'lava' not found - is this really the extended firmware?"
fi

if [ "$MODE" = "uninstall" ] && [ ! -x "$HELIX_INIT" ]; then
    die "HelixScreen does not appear to be installed ($HELIX_INIT missing)"
fi

# ---- overlay persistence ---------------------------------------------------

# Without /oem/.debug the boot hooks and the stock-UI disable are wiped on the
# next reboot. (The official installer would create the flag itself; requiring
# it up front keeps persistence an explicit choice, consistent with the other
# add-on recipes.)
if [ "$MODE" = "install" ] && [ ! -f /oem/.debug ]; then
    die "overlay persistence is disabled - enable it first (firmware-config: Settings > System > Overlay Persistence, or 'touch /oem/.debug') and rerun"
fi

# ---- fetch the official installer ------------------------------------------

log "downloading official HelixScreen installer"
tmpinstaller=$(mktemp -t helixscreen-install.XXXXXX.sh)
trap 'rm -f "$tmpinstaller"' EXIT
wget -q "$INSTALLER_URL" -O "$tmpinstaller" \
    || die "download failed - is the printer online?"

# ---- run it ----------------------------------------------------------------

if [ "$MODE" = "uninstall" ]; then
    log "running official uninstaller"
    sh "$tmpinstaller" --uninstall
    log ""
    log "uninstall complete. The stock Snapmaker UI returns after a reboot"
    log "(firmware-config: Actions > System > Reboot System)."
    exit 0
fi

log "running official installer (this downloads the release and takes a few minutes)"
sh "$tmpinstaller"

# ---- restore the Remote Screen service --------------------------------------

# The installer's autostart hook just replaced /etc/init.d/S99fb-http with a
# HelixScreen delegate that never starts the fb-http daemon (see header). The
# already-running fb-http instance survives until reboot; S99fb-http-restore
# handles subsequent boots. Start the preserved stock launcher now anyway so a
# stopped Remote Screen comes back without waiting for a reboot ("already
# running" exits non-zero, hence the || true).
if [ -x /etc/init.d/S99fb-http.stock ]; then
    log "re-starting Remote Screen service (fb-http)"
    /etc/init.d/S99fb-http.stock start || true
fi

# ---- done -------------------------------------------------------------------

log ""
log "install complete."
log ""
log "HelixScreen:    /userdata/helixscreen"
log "Init script:    $HELIX_INIT"
log "Remote screen:  http://<printer-ip>/screen/ (unchanged, mirrors whatever"
log "                UI owns the framebuffer)"
log ""
log "NOTE: a Snapmaker firmware upgrade wipes the overlay and brings the stock"
log "      UI back - rerun this script afterwards to reinstall HelixScreen."
