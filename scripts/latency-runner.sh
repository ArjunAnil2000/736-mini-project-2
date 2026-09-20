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
# Usage: sudo ./scripts/latency-runner.sh [> results.csv]

set -euo pipefail

PARENT_CPU=0
CHILD_CPU=1
TRACE_CPU=2
FREQ=4000000 # kHz = 4000 MHz

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BIN="nice -n -20 $ROOT_DIR/out/latency"

# Consider adding cpu-clock if you suspect that fixing the DVFS has failed.
PERF="taskset -c 2 nice -n -20 perf record \
-e context-switches \
-e emulation-faults \
-e task-clock \
-e msr/pperf/ \
-e msr/smi/ \
-e syscalls:sys_*_pipe \
-e syscalls:sys_*_pipe2 \
--latency \
--cpu 0-1 \
--realtime=99 \
--output=perf.data \
--freq=max \
--call-graph lbr \
--stat \
--data \
--phys-data \
--data-page-size \
--code-page-size \
--timestamp \
--period \
--sample-cpu \
--sample-identifier \
--sample-mem-info \
--raw-samples \
--branch-any \
--intr-regs \
--user-regs \
--running-time \
--timestamp-filename \
--synth=all --
"
# perf record captures and dumps data
# context-switches records context switches
# emulation-faults records when software emulation is required for an unsupported instruction
# major-faults records "major" page faults
# minor-faults record "minor" page faults
# You may consider combining the top two and use page-faults instead
# REF: https://sandpile.org/x86/msr.htm
# msr/pperf/ records the "productive performance clock count" MSR
# msr/smi/ records miscellaneous CPU resource management operations
# msr/tsc/ records the TSC, yes the thing your code already does, but it affiliates events to the TSC
# latency records latency
# cpu records CPUs 0 to 1
# realtime=99 ensures the highest schedule priority, which should be fine since it's pinned to a CPU core
# freq=max ensures maximum frequency profiling
# call-graph ensures call graph recording, and if lbr fails, use dwarf with sufficient storage, then fp last
# stat measures per-thread event counts
# data records the sample virtual address
# phys-data records the sample physical address
# data-page-size records the sampled data address data page size
# code-page-size records the sampled code address (ip) page size
# timestamp records the sample timestamps
# period recors the sample period
# sample-cpu records the sample CPU
# sample-identifier records the sample identifier
# sample-mem-info records memory operations
# raw-samples records raw samples of all opened counters
# branch-any records all taken branch stacks
# weight enables weighted sampling
# intr-regs captures all registers at interrupts
# user-regs captures all registers at sample time
# running-time captures running and enabled time for read events
# timestamp-filename appends the timestamp to the output file name
# synth=all records events on FORK, COMM, MMAP, and CGROUP

PERF2="perf stat \
-M tma_bottleneck_irregular_overhead
"


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
    echo "== unpinning CPU $TRACE_CPU (trace) ==" >&2
    "$SCRIPT_DIR/unpin_freq.sh" "$TRACE_CPU" >&2
}
trap cleanup EXIT

echo "== pinning CPU $PARENT_CPU (parent) ==" >&2
"$SCRIPT_DIR/pin_freq.sh" "$PARENT_CPU" "$FREQ" >&2
echo "== pinning CPU $CHILD_CPU (child) ==" >&2
"$SCRIPT_DIR/pin_freq.sh" "$CHILD_CPU" "$FREQ" >&2
echo "== pinning CPU $TRACE_CPU (trace) ==" >&2
"$SCRIPT_DIR/pin_freq.sh" "$TRACE_CPU" "$FREQ" >&2

echo "== running latency (parent on CPU $PARENT_CPU, child on CPU $CHILD_CPU) ==" >&2
if [ -n "${SUDO_USER:-}" ]; then
    echo "==== running perf capture ====" >&2
    sudo -u "$SUDO_USER" "$BIN"
else
    "$BIN"
fi
