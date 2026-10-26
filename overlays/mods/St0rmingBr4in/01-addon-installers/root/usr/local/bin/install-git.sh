#!/bin/sh
#
# install-git.sh - Install git (aarch64, Debian-derived) on a Snapmaker U1
#                  running paxx12's extended firmware.
#
# Why this is needed
# ------------------
# The extended firmware image ships without git. AFC's `update-afc.sh -b DEV`
# and any other GitOps workflow needs a working `git` binary. Buildroot
# doesn't ship one, and installing from source needs gcc/make which also
# aren't on the image, so we extract Debian's official aarch64 .deb.
#
# Two subtleties this script handles automatically:
#
#   1. Debian's `git-remote-https` is linked against libcurl-gnutls.so.4.
#      Buildroot only ships the OpenSSL variant (libcurl.so.4). The libcurl
#      ABI is identical between the two builds (only the TLS backend differs),
#      so we symlink `libcurl-gnutls.so.4 -> libcurl.so.4`. HTTPS clones then
#      work; you'll see one harmless warning "no version information available"
#      because the OpenSSL libcurl lacks the GnuTLS build's symbol versions.
#
#   2. `/oem/.debug` toggles paxx12's overlay-persist mode. Without it,
#      /etc/init.d writes, /usr/local/bin writes, and /lib symlinks are all
#      wiped at every reboot by S01aoverlayfs. This script requires the flag
#      to be set (firmware-config: Settings > System > Overlay Persistence)
#      so the install persists.
#
# Idempotent: rerunning skips already-installed steps.
#
# Uninstall (--uninstall) removes everything the install created: the git
# binary, the git-core helpers, and the two compatibility symlinks.
#
# Usage: ssh root@<printer-ip> 'sh -s' < install-git.sh
#        install-git.sh [--uninstall]
#

set -eu

GIT_DEB_URL="http://ftp.debian.org/debian/pool/main/g/git/git_2.47.3-0+deb13u1_arm64.deb"
GIT_DEB_NAME="git_2.47.3-0+deb13u1_arm64.deb"
WORKDIR="/tmp/install-git.$$"

log() { printf '[install-git] %s\n' "$*"; }
die() { printf '[install-git] ERROR: %s\n' "$*" >&2; exit 1; }

# BusyBox wget has no TLS. The extended firmware ships a full curl at
# /usr/local/bin/curl, which is NOT on the non-interactive PATH (e.g. when
# run from the firmware-config web UI).
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

# Refuse to run on anything that isn't the extended firmware.
if ! grep -q '^ID=buildroot' /etc/os-release 2>/dev/null; then
    die "not a Buildroot system - refusing to run"
fi

case "$(uname -m)" in
    aarch64) ;;
    *) die "expected aarch64 CPU, got $(uname -m)" ;;
esac

# ---- uninstall --------------------------------------------------------------

if [ "$MODE" = "uninstall" ]; then
    log "removing git binary, helpers, and compatibility symlinks"
    rm -f /usr/bin/git
    rm -f /usr/local/bin/git
    rm -rf /usr/local/libexec/git-core
    rm -f /usr/lib/git-core
    rm -f /lib/libcurl-gnutls.so.4
    log "uninstall complete."
    exit 0
fi

# Overlay-persist flag. Without this, everything the script writes to /lib,
# /usr/local, /usr/lib, and /etc gets wiped on every reboot.
[ -f /oem/.debug ] || die "overlay persistence is disabled - enable it first (firmware-config: Settings > System > Overlay Persistence, or 'touch /oem/.debug') and rerun"

# ---- short-circuit if already installed -----------------------------------

if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
    installed_ver=$(git --version | awk '{print $NF}')
    log "git $installed_ver already installed at $(command -v git)"
    log "re-running to ensure symlinks are in place..."
fi

# ---- download + extract .deb ----------------------------------------------

mkdir -p "$WORKDIR"
trap 'rm -rf "$WORKDIR"' EXIT

log "downloading $GIT_DEB_NAME"
fetch "$GIT_DEB_URL" "$WORKDIR/git.deb"

log "extracting .deb"
cd "$WORKDIR"
busybox ar -x git.deb
mkdir -p extracted
xzcat data.tar.xz | tar -x -C extracted

# ---- install --------------------------------------------------------------

log "installing binaries + git-core helpers"
mkdir -p /usr/local/bin /usr/local/lib /usr/local/libexec/git-core
cp extracted/usr/bin/git /usr/local/bin/git
chmod 755 /usr/local/bin/git
cp -a extracted/usr/lib/git-core/. /usr/local/libexec/git-core/
chmod 755 /usr/local /usr/local/libexec /usr/local/libexec/git-core /usr/local/lib
chmod -R go+rX /usr/local/libexec/git-core/

# git's compiled-in default exec-path is /usr/lib/git-core (Debian's convention).
# Symlink there so git finds its helpers without needing GIT_EXEC_PATH set.
ln -sfn /usr/local/libexec/git-core /usr/lib/git-core

# PATH: /usr/bin is on everyone's PATH; /usr/local/bin isn't in the non-interactive
# shell PATH by default. Symlink into /usr/bin for universal visibility.
ln -sf /usr/local/bin/git /usr/bin/git

# libcurl-gnutls ABI shim (see comment at top of file).
ln -sf /lib/libcurl.so.4 /lib/libcurl-gnutls.so.4

# ---- verify ---------------------------------------------------------------

log "verifying git works"
git --version || die "git --version failed"

log "verifying HTTPS clone (will produce 'no version information available'"
log "warning from libcurl-gnutls shim - that's harmless)"
if git ls-remote --heads https://github.com/AFCProject/AFC-Klipper-Add-On.git >/dev/null 2>&1; then
    log "HTTPS clone works"
else
    die "HTTPS clone test failed - check network + shim"
fi

log ""
log "install complete."
log "git:                $(command -v git)  ($(git --version | awk '{print $NF}'))"
log "git-core:           /usr/lib/git-core -> /usr/local/libexec/git-core"
log "libcurl-gnutls shim: /lib/libcurl-gnutls.so.4 -> /lib/libcurl.so.4"
