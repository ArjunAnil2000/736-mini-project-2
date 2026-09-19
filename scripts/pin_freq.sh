#!/usr/bin/env bash
# Pin a single CPU core's governor to "performance" and lock its min/max scaling frequency together,
# so it runs at one constant frequency instead of ramping under intel_pstate's normal DVFS behavior.
#
# Saves the core's prior governor/min/max settings to a state file so scripts/unpin_freq.sh can
# restore them afterwards.
#
# Must be run with sudo. Run this BEFORE the benchmark, on the same core you pass to the benchmark
# binary (via taskset).
#
# Usage: sudo ./scripts/pin_freq.sh <cpu> [freq_khz]
#   cpu       - which CPU core to pin (e.g. 0)
#   freq_khz  - frequency to lock to, in kHz (default: that core's max)

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "must be run as root (sudo)" >&2
    exit 1
fi

CPU="${1:?usage: $0 <cpu> [freq_khz]}"
CPUFREQ_DIR="/sys/devices/system/cpu/cpu${CPU}/cpufreq"

if [ ! -d "$CPUFREQ_DIR" ]; then
    echo "no such cpufreq dir: $CPUFREQ_DIR (bad CPU number?)" >&2
    exit 1
fi

STATE_DIR="$(dirname "$0")/.freq_state"
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/cpu${CPU}.env"

if [ -f "$STATE_FILE" ]; then
    echo "note: $STATE_FILE already exists (from a previous pin); leaving it as the original " \
         "state to restore to. Run unpin_freq.sh first if you want to re-baseline it." >&2
else
    {
        echo "GOVERNOR=$(cat "$CPUFREQ_DIR/scaling_governor")"
        echo "MIN_FREQ=$(cat "$CPUFREQ_DIR/scaling_min_freq")"
        echo "MAX_FREQ=$(cat "$CPUFREQ_DIR/scaling_max_freq")"
    } > "$STATE_FILE"
fi

# use max frequency if a frequency is not passed to this script
MAX_FREQ=$(cat "$CPUFREQ_DIR/cpuinfo_max_freq")
FREQ="${2:-$MAX_FREQ}"

# the "pinning" frequency part
echo "performance" > "$CPUFREQ_DIR/scaling_governor"
echo "$FREQ" > "$CPUFREQ_DIR/scaling_min_freq"
echo "$FREQ" > "$CPUFREQ_DIR/scaling_max_freq"

# print earlier vals
echo "CPU $CPU pinned (prior state saved to $STATE_FILE):"
echo "  governor:       $(cat "$CPUFREQ_DIR/scaling_governor")"
echo "  min_freq:       $(cat "$CPUFREQ_DIR/scaling_min_freq") kHz"
echo "  max_freq:       $(cat "$CPUFREQ_DIR/scaling_max_freq") kHz"
echo "  current freq:   $(cat "$CPUFREQ_DIR/scaling_cur_freq") kHz"
echo "run 'sudo ./scripts/unpin_freq.sh $CPU' to restore prior settings"
echo ""
echo "=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-="
echo ""
