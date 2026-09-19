#!/usr/bin/env bash
# Restore a CPU core's governor/min/max frequency to whatever they were
# before scripts/pin_freq.sh last ran on it.
#
# Must be run with sudo.
#
# Usage: sudo ./scripts/unpin_freq.sh <cpu>

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "must be run as root (sudo)" >&2
    exit 1
fi

CPU="${1:?usage: $0 <cpu>}"
CPUFREQ_DIR="/sys/devices/system/cpu/cpu${CPU}/cpufreq"
STATE_FILE="$(dirname "$0")/.freq_state/cpu${CPU}.env"

if [ ! -f "$STATE_FILE" ]; then
    echo "no saved state for CPU $CPU at $STATE_FILE (never pinned, or already unpinned)" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$STATE_FILE"

echo "CPU $CPU pinned state before restore:"
echo "  governor:     $(cat "$CPUFREQ_DIR/scaling_governor")"
echo "  min_freq:     $(cat "$CPUFREQ_DIR/scaling_min_freq") kHz"
echo "  max_freq:     $(cat "$CPUFREQ_DIR/scaling_max_freq") kHz"
echo "  current freq: $(cat "$CPUFREQ_DIR/scaling_cur_freq") kHz"

# Max must be restored before min in case the current (pinned) min is
# above the original max, which the kernel would otherwise reject.
echo "$MAX_FREQ" > "$CPUFREQ_DIR/scaling_max_freq"
echo "$MIN_FREQ" > "$CPUFREQ_DIR/scaling_min_freq"
echo "$GOVERNOR" > "$CPUFREQ_DIR/scaling_governor"

rm -f "$STATE_FILE"

echo "CPU $CPU restored:"
echo "  governor: $(cat "$CPUFREQ_DIR/scaling_governor")"
echo "  min_freq: $(cat "$CPUFREQ_DIR/scaling_min_freq") kHz"
echo "  max_freq: $(cat "$CPUFREQ_DIR/scaling_max_freq") kHz"
echo ""
echo "=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-="
echo ""
