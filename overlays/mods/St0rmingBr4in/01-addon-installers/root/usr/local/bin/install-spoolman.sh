#!/bin/sh
#
# install-spoolman.sh - Install Spoolman filament management on a Snapmaker U1
#                      running paxx12's extended firmware, and wire it into
#                      Moonraker so AFC can read spool metadata.
#
# What it installs
# ----------------
# Spoolman v0.23.1 (Donkie/Spoolman) with the SQLite backend, plus:
#   - Python venv at /home/lava/spoolman/.venv
#   - Data + SQLite DB at /home/lava/printer_data/spoolman/
#     (that path is bind-mounted from /userdata, always persistent)
#   - init.d startup script at /etc/init.d/S65spoolman
#   - Moonraker `[spoolman]` block in the persistent extension dir
#     (/home/lava/printer_data/config/extended/moonraker/05_spoolman.cfg)
#
# Then Spoolman answers on http://<printer-ip>:7912
#
# Design notes (things this script does that aren't obvious)
# ----------------------------------------------------------
#
#   1. Spoolman's official `scripts/install.sh` uses `uv` and assumes apt/pacman/dnf
#      package managers. Buildroot has none, so we bypass it and pip-install a
#      trimmed dependency list against the U1's system Python 3.11.
#
#   2. We drop the `psycopg2-binary`, `aiomysql`, `asyncpg`, `sqlalchemy-cockroachdb`,
#      `httptools`, and `uvloop` deps. SQLite mode doesn't need any of them and
#      some fail to install without headers/compiler.
#
#   2b. FastAPI and starlette are hard-pinned. Spoolman v0.23.1's `client.py`
#       calls `FileResponse(..., method=method)`, which starlette >=1.0 removed.
#       Upstream's permissive `fastapi~=0.115` lets pip resolve to newer
#       fastapi/starlette that break every static-file request with a 500.
#       Pin to fastapi 0.115.x + starlette 0.41.x and the UI works.
#
#   3. Spoolman's startup calls `subprocess.run(["alembic", ...])` via PATH
#      lookup, not the venv's bin. So we set PATH in .env with the venv bin
#      prepended, and the init.d script sources .env before exec'ing uvicorn.
#
#   4. Busybox `start-stop-daemon` doesn't support `-d` (chdir), so the init.d
#      script uses `/bin/sh -c 'cd ... && exec ...'` as the target executable.
#
#   5. The Moonraker `[spoolman]` block goes in extended/moonraker/ not directly
#      in moonraker.conf, so a bin re-flash won't drop it.
#
#   6. Requires /oem/.debug (paxx12's overlay-persist flag) or everything the
#      script writes to /etc/init.d, /home/lava/spoolman, and /usr/local gets
#      wiped on the next reboot by S01aoverlayfs.
#
# Idempotent: rerunning replaces the install cleanly.
#
# Uninstall (--uninstall) stops the service and removes the init script, the
# Moonraker [spoolman] config, and the application + venv. The spool database
# ($SPOOLMAN_DATA) is preserved so a reinstall picks it up again.
#
# Usage: ssh root@<printer-ip> 'sh -s' < install-spoolman.sh
#        install-spoolman.sh [--uninstall]
#

set -eu

SPOOLMAN_VERSION=v0.23.1
SPOOLMAN_ZIP_URL="https://github.com/Donkie/Spoolman/releases/download/${SPOOLMAN_VERSION}/spoolman.zip"
SPOOLMAN_DIR=/home/lava/spoolman
SPOOLMAN_DATA=/home/lava/printer_data/spoolman
SPOOLMAN_LOG=/home/lava/printer_data/logs/spoolman.log
MOONRAKER_EXT_DIR=/home/lava/printer_data/config/extended/moonraker
SPOOLMAN_PORT=7912

log() { printf '[install-spoolman] %s\n' "$*"; }
die() { printf '[install-spoolman] ERROR: %s\n' "$*" >&2; exit 1; }

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

if ! grep -q '^ID=buildroot' /etc/os-release 2>/dev/null; then
    die "not a Buildroot system - refusing to run"
fi

if ! id lava >/dev/null 2>&1; then
    die "user 'lava' not found - is this really the extended firmware?"
fi

# ---- uninstall --------------------------------------------------------------

if [ "$MODE" = "uninstall" ]; then
    log "stopping Spoolman"
    if [ -x /etc/init.d/S65spoolman ]; then
        /etc/init.d/S65spoolman stop || true
    fi
    pkill -f "uvicorn spoolman.main:app" 2>/dev/null || true

    log "removing init script, Moonraker config, and application"
    rm -f /etc/init.d/S65spoolman
    rm -f "$MOONRAKER_EXT_DIR/05_spoolman.cfg"
    rm -rf "$SPOOLMAN_DIR"

    if grep -q "BEGIN spoolman-boot-hook" /etc/init.d/S49extended-config 2>/dev/null; then
        log "removing boot hook from S49extended-config"
        sed -i "/# BEGIN spoolman-boot-hook/,/# END spoolman-boot-hook/d" /etc/init.d/S49extended-config
    fi

    log "restarting Moonraker"
    if [ -x /etc/init.d/S61moonraker ]; then
        /etc/init.d/S61moonraker restart || true
    fi

    log ""
    log "uninstall complete. The spool database was preserved at:"
    log "  $SPOOLMAN_DATA"
    log "Reinstalling picks it up again; delete that directory if unwanted."
    exit 0
fi

# Without /oem/.debug, everything this script writes outside
# /home/lava/printer_data is wiped on the next reboot.
[ -f /oem/.debug ] || die "overlay persistence is disabled - enable it first (firmware-config: Settings > System > Overlay Persistence, or 'touch /oem/.debug') and rerun"

# ---- stop existing instance ------------------------------------------------

if [ -x /etc/init.d/S65spoolman ]; then
    log "stopping existing Spoolman"
    /etc/init.d/S65spoolman stop || true
    sleep 1
fi
# Belt-and-braces
pkill -f "uvicorn spoolman.main:app" 2>/dev/null || true

# ---- download + extract ----------------------------------------------------

log "downloading Spoolman ${SPOOLMAN_VERSION}"
mkdir -p "$SPOOLMAN_DIR"
tmpzip=$(mktemp -t spoolman.XXXXXX.zip)
trap 'rm -f "$tmpzip"' EXIT
fetch "$SPOOLMAN_ZIP_URL" "$tmpzip"

log "extracting to $SPOOLMAN_DIR"
# Preserve the venv if it already exists (pip cache benefits)
venv_backup=""
if [ -d "$SPOOLMAN_DIR/.venv" ]; then
    venv_backup="$SPOOLMAN_DIR/.venv"
    mv "$venv_backup" "/tmp/.venv-backup.$$"
    venv_backup="/tmp/.venv-backup.$$"
fi
# Clear the app tree (but keep .venv-backup separately)
find "$SPOOLMAN_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
unzip -q -o "$tmpzip" -d "$SPOOLMAN_DIR"
if [ -n "$venv_backup" ] && [ -d "$venv_backup" ]; then
    mv "$venv_backup" "$SPOOLMAN_DIR/.venv"
fi
chown -R lava:lava "$SPOOLMAN_DIR"

# ---- create venv + install trimmed requirements ---------------------------

if [ ! -d "$SPOOLMAN_DIR/.venv" ]; then
    log "creating Python venv"
    su - lava -c "cd $SPOOLMAN_DIR && python3 -m venv .venv"
fi

log "writing requirements.txt (SQLite-only trimmed deps)"
cat > "$SPOOLMAN_DIR/requirements.txt" <<'REQEOF'
# Trimmed from pyproject.toml for SQLite-only mode. See install script header
# for the rationale on which upstream deps we skip.
#
# NOTE: fastapi and starlette are hard-pinned. Spoolman v0.23.1's client.py
# calls FileResponse(..., method=method) which was removed in starlette >=1.0.
# The permissive `~=` specifiers in Spoolman's upstream pyproject.toml let pip
# resolve to newer versions that break the web UI at 500 errors on every
# static-file request. Pinning to the 0.41.x line keeps the UI working.
fastapi>=0.115,<0.116
starlette>=0.40,<0.42
uvicorn~=0.34
SQLAlchemy[aiosqlite,asyncio]~=2.0
pydantic~=2.10
platformdirs~=4.3
alembic~=1.15
scheduler~=0.8
setuptools~=78.1
WebSockets~=15.0
prometheus-client~=0.21
httpx~=0.28
hishel~=0.1
python-dotenv~=1.0
REQEOF
chown lava:lava "$SPOOLMAN_DIR/requirements.txt"

log "installing Python deps in venv (this takes ~1 min)"
su - lava -c "$SPOOLMAN_DIR/.venv/bin/pip install --quiet --upgrade pip" >/dev/null
su - lava -c "$SPOOLMAN_DIR/.venv/bin/pip install --quiet -r $SPOOLMAN_DIR/requirements.txt"

# ---- write .env ------------------------------------------------------------

log "writing .env"
mkdir -p "$SPOOLMAN_DATA"
chown -R lava:lava "$SPOOLMAN_DATA"
cat > "$SPOOLMAN_DIR/.env" <<ENVEOF
# Spoolman runtime config
SPOOLMAN_HOST=0.0.0.0
SPOOLMAN_PORT=$SPOOLMAN_PORT
SPOOLMAN_DIR_DATA=$SPOOLMAN_DATA
# Spoolman calls \`alembic upgrade head\` at startup via subprocess PATH lookup,
# so the venv's bin must be first on PATH.
PATH=$SPOOLMAN_DIR/.venv/bin:/usr/bin:/bin
ENVEOF
chown lava:lava "$SPOOLMAN_DIR/.env"

# ---- init.d script ---------------------------------------------------------

log "installing /etc/init.d/S65spoolman"
cat > /etc/init.d/S65spoolman <<'INITEOF'
#!/bin/sh
# Spoolman - filament spool tracking service
### BEGIN INIT INFO
# Provides:       spoolman
# Required-Start: $network
# Required-Stop:  $network
# Default-Start:  S
# Default-Stop:
# Description:    Spoolman filament tracking (Moonraker integration for AFC)
### END INIT INFO

PIDFILE=/var/run/spoolman.pid
SPOOLMAN_DIR=/home/lava/spoolman
LOGFILE=/home/lava/printer_data/logs/spoolman.log

case "$1" in
  start)
    echo "Starting Spoolman..."
    # Busybox start-stop-daemon has no -d (chdir), so we use /bin/sh -c 'cd && exec'
    # as the target. Sourcing .env brings in SPOOLMAN_HOST/PORT plus PATH for alembic.
    start-stop-daemon -S -b -c lava -m -p "$PIDFILE" \
      -x /bin/sh -- -c "cd $SPOOLMAN_DIR && set -a && . ./.env && set +a && exec .venv/bin/uvicorn spoolman.main:app --host \$SPOOLMAN_HOST --port \$SPOOLMAN_PORT >> $LOGFILE 2>&1"
    ;;
  stop)
    echo "Stopping Spoolman..."
    start-stop-daemon -K -q -p "$PIDFILE" -o
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  *) echo "Usage: $0 {start|stop|restart}"; exit 1 ;;
esac
INITEOF
chmod 755 /etc/init.d/S65spoolman

# ---- boot hook ---------------------------------------------------------------

# The busybox boot glob is frozen from the read-only squashfs before the
# overlay pivot, so the overlay-written S65spoolman above never runs at boot
# on its own. Firmware built with the St0rmingBr4in mod ships
# /etc/init.d/S65spoolman-boot in the squashfs for this; for ssh-only installs
# delegate from the squashfs-shipped S49extended-config instead. The hook goes
# at the TOP of the file (it ends in `exit 0`, so appending is dead code) and
# both mechanisms carry a pgrep guard so they never double-start uvicorn.
if [ -f /etc/init.d/S49extended-config ] && \
   ! grep -q "BEGIN spoolman-boot-hook" /etc/init.d/S49extended-config; then
    log "adding boot hook to S49extended-config"
    {
        head -n 1 /etc/init.d/S49extended-config
        cat <<'HOOKEOF'
# BEGIN spoolman-boot-hook (added by install-spoolman.sh, removed by --uninstall)
# Overlay-installed init scripts are invisible to the boot glob (frozen from
# the squashfs pre-overlay-pivot); start Spoolman from here instead.
if [ "$1" = "start" ] && [ -x /etc/init.d/S65spoolman ] && ! pgrep -f "uvicorn spoolman.main:app" >/dev/null 2>&1; then
    /etc/init.d/S65spoolman start
fi
# END spoolman-boot-hook
HOOKEOF
        tail -n +2 /etc/init.d/S49extended-config
    } > /tmp/S49-hooked.$$ && cat /tmp/S49-hooked.$$ > /etc/init.d/S49extended-config
    rm -f /tmp/S49-hooked.$$
fi

# ---- Moonraker integration -------------------------------------------------

log "adding Moonraker [spoolman] to extended/moonraker/ (survives bin re-flash)"
mkdir -p "$MOONRAKER_EXT_DIR"
cat > "$MOONRAKER_EXT_DIR/05_spoolman.cfg" <<MOONEOF
# Auto-generated by install-spoolman.sh
[spoolman]
server: http://127.0.0.1:$SPOOLMAN_PORT
sync_rate: 5
MOONEOF
chown lava:lava "$MOONRAKER_EXT_DIR/05_spoolman.cfg"

# ---- start Spoolman + restart Moonraker -----------------------------------

log "starting Spoolman"
/etc/init.d/S65spoolman start

# Wait for Spoolman to actually be listening (migrations take ~10s on first run)
log "waiting for Spoolman to be reachable"
for i in $(seq 1 30); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:$SPOOLMAN_PORT/api/v1/info', timeout=2)" >/dev/null 2>&1; then
        log "Spoolman is up (took ${i}s)"
        break
    fi
    sleep 1
    if [ "$i" -eq 30 ]; then
        die "Spoolman didn't come up - check $SPOOLMAN_LOG"
    fi
done

log "restarting Moonraker to pick up [spoolman]"
if [ -x /etc/init.d/S61moonraker ]; then
    /etc/init.d/S61moonraker restart
else
    log "WARNING: /etc/init.d/S61moonraker not found - restart Moonraker manually"
fi

# ---- verify ---------------------------------------------------------------

log ""
log "install complete."
log ""
log "Spoolman API:  http://<printer-ip>:$SPOOLMAN_PORT"
log "Spoolman UI:   http://<printer-ip>:$SPOOLMAN_PORT"
log "SQLite DB:     $SPOOLMAN_DATA/spoolman.db"
log "Init script:   /etc/init.d/S65spoolman"
log "Moonraker cfg: $MOONRAKER_EXT_DIR/05_spoolman.cfg"
log ""
log "Verify Moonraker sees Spoolman:"
log "  curl http://localhost:7125/server/info | grep spoolman"
