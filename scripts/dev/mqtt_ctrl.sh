#!/bin/bash

set -eo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <user@host> {upgrade-check-remote}"
  exit 1
fi

SSH_HOST="$1"
shift

PASSWORD="${PASSWORD:-snapmaker}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

case "$1" in
  upgrade-check-remote)
    topic="system"
    json='{"method":"system.upgrade_check_remote","jsonrpc":"2.0","id":4}'
    ;;

  *)
    echo "Usage: $0 <user@host> {upgrade-check-remote}"
    exit 1
    ;;
esac

sshpass -p "$PASSWORD" ssh $SSH_OPTS "$SSH_HOST" "
  mosquitto_sub -v -h localhost -t $topic/# -C 2 &
  mosquitto_pub -h localhost -t $topic/request -m '$json'
  wait
"
