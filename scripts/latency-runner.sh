#!/usr/bin/env bash
# Runs latency one time.
# 1. checks the latency binary exists
# 2. pins both target CPUs' frequency (scripts/pin_freq.sh), one per process
# 3. runs latency (it pins its own CPU affinity internally - see src/latency.c - so no taskset
#    is needed here, unlike clockres-runner.sh)
# 4. unpins both CPUs' frequency again (scripts/unpin_freq.sh), even if the run fails
#
# Must be run with sudo (steps 2 and 4 need root). The actual benchmark in step 3 is dropped
# back to the invoking user via sudo -u, so latency itself never runs as root.
#
# CPU 0 (parent) and CPU 1 (child) are both P-cores, pinned to a constant 4000 MHz.
#
# Usage: sudo ./scripts/latency-runner.sh > results/latency_run1.csv
# Name the outputs results/latency_run<N>.csv (N = 1, 2, 3 ...): scripts/analyze_latency.py takes the
# minimum over all of them and shows how reproducible it is, and trace_summary.py compares its traced
# CPU work against them. (results/latency.csv is the old UNPINNED smoke test; nothing reads it.)
# Repeat runs:  for i in 1 2 3; do sudo make latency.run > results/latency_run$i.csv; done

set -euo pipefail

PARENT_CPU=0
CHILD_CPU=1
FREQ=4000000 # kHz = 4000 MHz
WARMUP=20      # untimed rounds per message size (also where memcmp runs)
ITERATIONS=2000 # timed rounds per message size

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BIN="$ROOT_DIR/out/latency"

if [ ! -x "$BIN" ]; then
    echo "error: $BIN not found or not executable (run 'make' first)" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "error: must be run with sudo (needed to pin/unpin CPU frequency)" >&2
    exit 1
fi

cleanup() {
    echo "== unpinning CPU $PARENT_CPU (parent) ==" >&2
    "$SCRIPT_DIR/unpin_freq.sh" "$PARENT_CPU" >&2
    echo "== unpinning CPU $CHILD_CPU (child) ==" >&2
    "$SCRIPT_DIR/unpin_freq.sh" "$CHILD_CPU" >&2
}
trap cleanup EXIT

echo "== pinning CPU $PARENT_CPU (parent) ==" >&2
"$SCRIPT_DIR/pin_freq.sh" "$PARENT_CPU" "$FREQ" >&2
echo "== pinning CPU $CHILD_CPU (child) ==" >&2
"$SCRIPT_DIR/pin_freq.sh" "$CHILD_CPU" "$FREQ" >&2

echo "== running latency (parent on CPU $PARENT_CPU, child on CPU $CHILD_CPU," \
     "$WARMUP warmup + $ITERATIONS timed rounds per size) ==" >&2
if [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$SUDO_USER" "$BIN" "$WARMUP" "$ITERATIONS"
else
    "$BIN" "$WARMUP" "$ITERATIONS"
fi
