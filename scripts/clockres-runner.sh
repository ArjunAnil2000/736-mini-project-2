#!/usr/bin/env bash
# Runs clockres one time.
# 1. checks the clockres binary exists
# 2. pins the target CPU's frequency (scripts/pin_freq.sh)
# 3. runs clockres (it pins its own CPU affinity internally - see CPU in src/clockres.c)
# 4. unpins the CPU's frequency again (scripts/unpin_freq.sh), even if the run fails
#
# Must be run with sudo (steps 2 and 4 need root). The actual benchmark in step 3 is dropped back to
# the invoking user via sudo -u, so clockres itself never runs as root.
#
# CPU here must match #define CPU in src/clockres.c. CPU 0 is a P-core, pinned to 4000 MHz.
#
# Usage: sudo ./scripts/clockres-runner.sh

set -euo pipefail

CPU=0
FREQ=4000000 # kHz = 4000 MHz

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BIN="$ROOT_DIR/out/clockres"

if [ ! -x "$BIN" ]; then
    echo "error: $BIN not found or not executable (run 'make' first)" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must be run with sudo (needed to pin/unpin CPU frequency)" >&2
    exit 1
fi

cleanup() {
    echo "== unpinning CPU $CPU =="
    "$SCRIPT_DIR/unpin_freq.sh" "$CPU"
}
trap cleanup EXIT

echo "== pinning CPU $CPU =="
"$SCRIPT_DIR/pin_freq.sh" "$CPU" "$FREQ"

echo "== running clockres on CPU $CPU =="
if [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" "$BIN"
else
    "$BIN"
fi
