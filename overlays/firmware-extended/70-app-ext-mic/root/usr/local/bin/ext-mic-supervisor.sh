#!/bin/sh
# Keep the phone microphone service alive across boot races and child exits.

DAEMON="/usr/local/bin/ext-mic.py"
PYTHON="/usr/bin/python3"
PAGE_DIR="/usr/local/share/ext-mic"
PAGE="/usr/local/share/ext-mic/phone.html"
DATA_DIR="/userdata/ext-mic"
SERVICE_DIR="/home/lava/printer_data/config/extended/ext-mic"
CAPTURE_DIR="/userdata/ext-mic/runs"
CERT_FILE="$SERVICE_DIR/server.crt"
KEY_FILE="$SERVICE_DIR/server.key"
LOGFILE="/userdata/ext-mic/service.log"
CHILD_PIDFILE="/run/ext-mic-child.pid"

stopping=0
child_pid=""

log() {
    printf '%s ext-mic-supervisor: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOGFILE" 2>/dev/null || true
}

prepare_storage() {
    mkdir -p "$CAPTURE_DIR" "$SERVICE_DIR" || return 1
    touch "$LOGFILE" || return 1
    chown -R lava:lava "$DATA_DIR" "$SERVICE_DIR" 2>/dev/null || return 1
}

child_pid_from_file() {
    [ -r "$CHILD_PIDFILE" ] || return 1
    pid=$(cat "$CHILD_PIDFILE" 2>/dev/null) || return 1
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$pid"
}

stop_child() {
    child=$(child_pid_from_file || true)
    if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
        kill -TERM "$child" 2>/dev/null || true
        attempt=1
        while [ "$attempt" -le 10 ] && kill -0 "$child" 2>/dev/null; do
            sleep 1
            attempt=$((attempt + 1))
        done
        # A stuck HTTP worker must not keep 9443 occupied after `stop`.
        if kill -0 "$child" 2>/dev/null; then
            kill -KILL "$child" 2>/dev/null || true
        fi
    fi
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM "$child_pid" 2>/dev/null || true
    fi
    [ -n "$child_pid" ] && wait "$child_pid" 2>/dev/null || true
    rm -f "$CHILD_PIDFILE"
    child_pid=""
}

on_exit() {
    stopping=1
    stop_child
}
trap on_exit TERM INT EXIT

find_lan_ip() {
    ip -4 -o addr show 2>/dev/null | awk '
        { split($4, a, "/"); addr = a[1]
          if (addr ~ /^10\./ || addr ~ /^192\.168\./ || addr ~ /^172\.(1[6-9]|2[0-9]|3[0-1])\./) {
              print addr; exit
          }
        }'
}

wait_for_dependencies() {
    while [ "$stopping" -eq 0 ]; do
        storage_ready=0
        grep -q ' /userdata ' /proc/mounts 2>/dev/null && storage_ready=1
        LAN_IP=$(find_lan_ip)
        if [ "$storage_ready" -eq 1 ] && [ -n "$LAN_IP" ]; then
            return 0
        fi
        log "waiting for /userdata and LAN"
        sleep 2
    done
    return 1
}

prepare() {
    [ -r "$DAEMON" ] || { log "missing $DAEMON"; return 1; }
    [ -x "$PYTHON" ] || { log "missing $PYTHON"; return 1; }
    [ -r "$PAGE" ] || { log "missing $PAGE"; return 1; }
    chmod 755 "$PAGE_DIR" || return 1
    chmod 644 "$PAGE" || return 1
    prepare_storage || { log "storage preparation failed"; return 1; }
    LAN_IP=$(find_lan_ip)
    [ -n "$LAN_IP" ] || return 1
    if [ ! -r "$CERT_FILE" ] || [ ! -r "$KEY_FILE" ]; then
        /usr/local/share/ext-mic/generate-cert.sh "$SERVICE_DIR" "$(hostname 2>/dev/null || echo lava)" "$LAN_IP" >>"$LOGFILE" 2>&1 || return 1
    fi
    chown lava:lava "$CERT_FILE" "$KEY_FILE" 2>/dev/null || return 1
    return 0
}

start_child() {
    rm -f "$CHILD_PIDFILE"
    start-stop-daemon -S -m -q -c lava -p "$CHILD_PIDFILE" -x "$PYTHON" -- \
        "$DAEMON" --host 0.0.0.0 --port 9443 \
        --certfile "$CERT_FILE" --keyfile "$KEY_FILE" \
        --captures "$CAPTURE_DIR" --phone-html "$PAGE"
}

while [ "$stopping" -eq 0 ]; do
    wait_for_dependencies || break
    if ! prepare; then
        log "prepare failed; retrying"
        sleep 2
        continue
    fi
    log "starting child (tls on 9443)"
    start_child >>"$LOGFILE" 2>&1 &
    child_pid=$!
    wait "$child_pid"
    exit_code=$?
    child_pid=""
    [ "$stopping" -eq 1 ] && break
    log "child exited with status $exit_code; restarting"
    sleep 2
done

exit 0
