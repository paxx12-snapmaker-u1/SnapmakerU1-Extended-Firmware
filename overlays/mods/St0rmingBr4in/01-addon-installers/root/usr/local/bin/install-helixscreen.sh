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

# BusyBox wget has no TLS. The extended firmware ships a full curl at
# /usr/local/bin/curl, which is NOT on the non-interactive PATH (e.g. when
# run from the firmware-config web UI). Put it on PATH for the official
# installer too - it needs an HTTPS downloader itself.
export PATH="/usr/local/bin:$PATH"
fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    else
        wget -q "$1" -O "$2"
    fi
}

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
fetch "$INSTALLER_URL" "$tmpinstaller" \
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

# ---- pin display backend to fbdev + touch device ----------------------------

# HelixScreen's U1 platform hooks default to the DRM backend (tear-free page
# flipping), but DRM rendering bypasses /dev/fb0, so the extended firmware's
# Remote Screen (fb-http mirrors fb0) shows a frozen splash forever. Its
# evdev touch fallback also probes /dev/input/event1 while the U1's tlsc6x
# touch controller lives on event0. Pin both in helixscreen.env (sourced by
# helix-launcher.sh on every start; kept across HelixScreen self-updates).
HELIX_ENV=/userdata/helixscreen/config/helixscreen.env
if [ -f "$HELIX_ENV" ] && ! grep -q '^# paxx12 extended firmware overrides$' "$HELIX_ENV"; then
    log "pinning fbdev backend + touch device in helixscreen.env"
    cat >> "$HELIX_ENV" <<'ENVEOF'

# paxx12 extended firmware overrides
# fbdev keeps /dev/fb0 current so the Remote Screen feature (/screen/) can
# mirror HelixScreen; DRM (the U1 default) renders past the framebuffer.
HELIX_DISPLAY_BACKEND=fbdev
HELIX_FB_DEVICE=/dev/fb0
# U1 touch controller (tlsc6x) - auto-detection probes event1, which
# does not exist on this device.
HELIX_TOUCH_DEVICE=/dev/input/event0
ENVEOF
fi

# ---- switch to fbdev and light up the panel ----------------------------------

# The installer started HelixScreen with its default DRM backend; restart it
# so it picks up the fbdev pin above. In fbdev mode nothing performs a DRM
# commit, so the VOP2 controller keeps scanning the U-Boot boot-logo buffer
# and the panel stays on the boot logo while /dev/fb0 updates invisibly - a
# blank/unblank cycle forces the fbdev emulation to mode-set /dev/fb0 onto
# the panel. (S99fb-http-restore repeats this at every boot.)
if [ -x "$HELIX_INIT" ]; then
    log "restarting HelixScreen on the fbdev backend"
    "$HELIX_INIT" restart || true
    sleep 3
fi
if [ -w /sys/class/graphics/fb0/blank ]; then
    log "forcing panel scanout of /dev/fb0"
    echo 1 > /sys/class/graphics/fb0/blank 2>/dev/null || true
    sleep 1
    echo 0 > /sys/class/graphics/fb0/blank 2>/dev/null || true
fi

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
