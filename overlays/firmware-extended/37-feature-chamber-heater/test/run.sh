#!/bin/bash
#
# SSH-iterate the chamber-heater overlay onto a LIVE U1 (no image rebuild), per
# @justinh-rahb's workflow: edit bash/Python/YAML here, push, test on the device,
# and only CI-build the full paxx image once for the final end-to-end run.
#
# Build-time bundling (03-bundle-dragonbreath-firmware.sh) is NOT run in this path,
# so pass a local dragonbreath.bin to stage it onto the device at the same path the
# migrate flasher reads by default. Get it from the pinned DragonBreath release, e.g.:
#   wget https://github.com/plastikman/DragonBreath/releases/download/v1.0.2/dragonbreath-v1.0.2.bin
#
# usage: run.sh <u1-host> [dragonbreath.bin]

ROOT_DIR="$(dirname "$(realpath "$0")")/.."

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <u1-host> [dragonbreath.bin]"
  exit 1
fi

U1="$1"
IMAGE="$2"
BUNDLE_PATH=/usr/local/share/chamber-heater/dragonbreath.bin

set -xeo pipefail

# 1. Push the runtime overlay (scripts, init.d guard, migrate.py, YAML, cfg tweaks).
scp -r "$ROOT_DIR/root/." "$U1":/

# 2. Stage the bundled device firmware (build-time bundle step is skipped here).
if [[ -n "$IMAGE" ]]; then
  ssh "$U1" "mkdir -p $(dirname "$BUNDLE_PATH")"
  scp "$IMAGE" "$U1:$BUNDLE_PATH"
fi

# 3. Run the first-boot migration guard (neutralizes a lingering panda_breath.cfg so
#    klippy starts clean and the dropdown offers "Convert to DragonBreath").
ssh -t "$U1" /etc/init.d/S58chamber-migration-guard start

# 4. Reload Klipper to pick up config changes.
ssh -t "$U1" /etc/init.d/S60klipper restart

set +x
echo
echo "Overlay pushed. To exercise the conversion end-to-end against a bench Panda:"
echo "  - via UI: Settings -> Chamber Heater -> Convert to DragonBreath (Accept)"
echo "  - or directly:  ssh $U1 /usr/local/bin/dragonbreath_migrate.py --host <panda-ip> migrate"
echo "Then confirm the device answers project=dragonbreath and klippy loads [dragonbreath]."
