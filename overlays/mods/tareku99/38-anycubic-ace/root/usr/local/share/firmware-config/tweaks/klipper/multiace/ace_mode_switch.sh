#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="/home/lava"
EXTRAS_DIR="${HOME_DIR}/klipper/klippy/extras"
KINEMATICS_DIR="${HOME_DIR}/klipper/klippy/kinematics"
STATE_DIR="${HOME_DIR}/printer_data/config/extended/multiace"
BUNDLE_DIR="/usr/local/share/firmware-config/tweaks/klipper/multiace"
SEED_DIR="$BUNDLE_DIR"
VARS_FILE="${STATE_DIR}/ace_vars.cfg"
LOGDIR="${HOME_DIR}/printer_data/logs"
LOGFILE="${LOGDIR}/ace_mode_switch.log"
mkdir -p "$LOGDIR" 2>/dev/null || true
if [ -e "$LOGFILE" ] && [ ! -w "$LOGFILE" ]; then
    rm -f "$LOGFILE" 2>/dev/null || true
fi
touch "$LOGFILE" 2>/dev/null || true
chmod 0666 "$LOGFILE" 2>/dev/null || true
MODE="$1"
log() {
    msg="$(date '+%Y-%m-%d %H:%M:%S') [mUlt1ACE] $1"
    printf '%s\n' "$msg"
    printf '%s\n' "$msg" >> "$LOGFILE" 2>/dev/null || true
}
if [ "$MODE" != "ace" ] && [ "$MODE" != "normal" ]; then
    echo "Usage: $0 [ace|normal]"
    exit 1
fi
if [ ! -f "$SEED_DIR/ace_vars.cfg" ]; then
    SEED_DIR="$SCRIPT_DIR"
fi
mkdir -p "$STATE_DIR/i18n"
if [ ! -f "$VARS_FILE" ]; then
    cp "$SEED_DIR/ace_vars.cfg" "$VARS_FILE"
fi
if [ ! -f "$STATE_DIR/i18n/en.json" ]; then
    cp "$SEED_DIR/i18n/en.json" "$STATE_DIR/i18n/en.json"
fi
if [ "$SCRIPT_DIR" != "$STATE_DIR" ]; then
    cp "$SCRIPT_DIR/ace_mode_switch.sh" "$STATE_DIR/ace_mode_switch.sh"
    chmod 0755 "$STATE_DIR/ace_mode_switch.sh"
fi
log "=== Mode switch to: $MODE ==="
log "EXTRAS_DIR=$EXTRAS_DIR"
log "KINEMATICS_DIR=$KINEMATICS_DIR"
if [ ! -f "$EXTRAS_DIR/filament_feed_ace.py" ]; then
    log "ERROR: filament_feed_ace.py not found in $EXTRAS_DIR! Aborting."
    exit 1
fi
if [ ! -f "$KINEMATICS_DIR/extruder_ace.py" ]; then
    log "ERROR: extruder_ace.py not found in $KINEMATICS_DIR! Aborting."
    exit 1
fi
if [ ! -f "$EXTRAS_DIR/filament_switch_sensor_ace.py" ]; then
    log "ERROR: filament_switch_sensor_ace.py not found in $EXTRAS_DIR! Aborting."
    exit 1
fi
if [ ! -f "$EXTRAS_DIR/filament_feed_pre_multiace.py" ]; then
    log "First run: backing up stock filament_feed.py"
    cp "$EXTRAS_DIR/filament_feed.py" "$EXTRAS_DIR/filament_feed_pre_multiace.py"
fi
if [ ! -f "$KINEMATICS_DIR/extruder_pre_multiace.py" ]; then
    log "First run: backing up stock extruder.py"
    cp "$KINEMATICS_DIR/extruder.py" "$KINEMATICS_DIR/extruder_pre_multiace.py"
fi
if [ ! -f "$EXTRAS_DIR/filament_switch_sensor_pre_multiace.py" ]; then
    log "First run: backing up stock filament_switch_sensor.py"
    cp "$EXTRAS_DIR/filament_switch_sensor.py" "$EXTRAS_DIR/filament_switch_sensor_pre_multiace.py"
fi
copy_or_die() {
    local src="$1"
    local dst="$2"
    local err
    if ! err=$(cp "$src" "$dst" 2>&1); then
        log "ERROR: cp '$src' -> '$dst' failed: ${err:-cp failed}"
        exit 1
    fi
}
if [ "$MODE" = "ace" ]; then
    log "Activating ACE mode..."
    copy_or_die "$EXTRAS_DIR/filament_feed_ace.py" "$EXTRAS_DIR/filament_feed.py"
    copy_or_die "$KINEMATICS_DIR/extruder_ace.py" "$KINEMATICS_DIR/extruder.py"
    copy_or_die "$EXTRAS_DIR/filament_switch_sensor_ace.py" "$EXTRAS_DIR/filament_switch_sensor.py"
    log "ACE files activated"
elif [ "$MODE" = "normal" ]; then
    log "Activating NORMAL mode..."
    copy_or_die "$EXTRAS_DIR/filament_feed_pre_multiace.py" "$EXTRAS_DIR/filament_feed.py"
    copy_or_die "$KINEMATICS_DIR/extruder_pre_multiace.py" "$KINEMATICS_DIR/extruder.py"
    copy_or_die "$EXTRAS_DIR/filament_switch_sensor_pre_multiace.py" "$EXTRAS_DIR/filament_switch_sensor.py"
    log "Stock files restored"
fi
find "$EXTRAS_DIR/__pycache__" -name "filament_feed*" -delete 2>/dev/null || true
find "$EXTRAS_DIR/__pycache__" -name "filament_switch_sensor*" -delete 2>/dev/null || true
find "$KINEMATICS_DIR/__pycache__" -name "extruder*" -delete 2>/dev/null || true
log "Python cache cleared"
log "Files swapped. Manual reboot required!"
log "=== Mode switch complete ==="
