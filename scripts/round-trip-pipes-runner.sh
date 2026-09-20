#!/usr/bin/env bash
# Runs round-trip-pipes one time.
# 1. checks the round-trip-pipes binary exists
# 2. pins both target CPUs' frequency (scripts/pin_freq.sh), one per process
# 3. runs round-trip-pipes (it pins its own CPU affinity internally - see src/round-trip-pipes.c
#    - so no taskset is needed here, unlike clockres-runner.sh)
# 4. unpins both CPUs' frequency again (scripts/unpin_freq.sh), even if the run fails
#
# Must be run with sudo (steps 2 and 4 need root). The actual benchmark in step 3 is dropped
# back to the invoking user via sudo -u, so round-trip-pipes itself never runs as root.
#
# CPU 0 (parent) and CPU 1 (child) are both P-cores, pinned to a constant 4000 MHz.
#
# Usage: sudo ./scripts/round-trip-pipes-runner.sh

set -euo pipefail

PARENT_CPU=0
CHILD_CPU=1
FREQ=4000000 # kHz = 4000 MHz

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BIN="$ROOT_DIR/out/round-trip-pipes"

if [ ! -x "$BIN" ]; then
    echo "error: $BIN not found or not executable (run 'make' first)" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must be run with sudo (needed to pin/unpin CPU frequency)" >&2
    exit 1
fi

cleanup() {
    echo "== unpinning CPU $PARENT_CPU (parent) =="
    "$SCRIPT_DIR/unpin_freq.sh" "$PARENT_CPU"
    echo "== unpinning CPU $CHILD_CPU (child) =="
    "$SCRIPT_DIR/unpin_freq.sh" "$CHILD_CPU"
}
trap cleanup EXIT

echo "== pinning CPU $PARENT_CPU (parent) =="
"$SCRIPT_DIR/pin_freq.sh" "$PARENT_CPU" "$FREQ"
echo "== pinning CPU $CHILD_CPU (child) =="
"$SCRIPT_DIR/pin_freq.sh" "$CHILD_CPU" "$FREQ"

echo "== running round-trip-pipes (parent on CPU $PARENT_CPU, child on CPU $CHILD_CPU) =="
if [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" "$BIN"
else
    "$BIN"
fi
