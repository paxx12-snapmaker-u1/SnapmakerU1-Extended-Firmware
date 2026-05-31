#!/bin/bash

ROOT_DIR="$(dirname "$(realpath "$0")")/.."

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <host>"
  exit 1
fi

set -xeo pipefail

scp -r "$ROOT_DIR/root/." "$1":/
ssh -t "$1" /etc/init.d/S60klipper restart
