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
BIN="nice -n -20 sudo -u \"$SUDO_USER\" $ROOT_DIR/out/latency"

HW_EVENTS="$(perf --no-pager list --raw-dump hw | xargs | tr ' ' ',')"
SW_EVENTS="$(perf --no-pager list --raw-dump sw | xargs | tr ' ' ',')"
CACHE_EVENTS="$(perf --no-pager list --raw-dump cache | xargs | tr ' ' ',')"
PIPELINE_EVENTS="$(sudo perf --no-pager list --raw-dump pipeline | xargs | tr ' ' ',')"
VIRTUAL_MEMORY_EVENTS="$(sudo perf --no-pager list --raw-dump 'virtual memory' | xargs | tr ' ' ',')"
MEMORY_EVENTS="$(sudo perf --no-pager list --raw-dump memory | xargs | tr ' ' ',')"
FRONTEND_EVENTS="$(sudo perf --no-pager list --raw-dump frontend | xargs | tr ' ' ',')"

# Consider adding cpu-clock if you suspect that fixing the DVFS has failed.
PERF="taskset -c 2 nice -n -20 sudo -u \"$SUDO_USER\" perf record \
-e $HW_EVENTS \
-e $SW_EVENTS \
-e $CACHE_EVENTS \
-e $PIPELINE_EVENTS \
-e $VIRTUAL_MEMORY_EVENTS \
-e $MEMORY_EVENTS \
-e $FRONTEND_EVENTS \
-e '{cycles,msr/aperf/,msr/mperf/,msr/pperf/,msr/smi/}:S' \
-e syscalls:sys_*_pipe \
-e syscalls:sys_*_pipe2 \
-e syscalls:sys_*_read* \
-e syscalls:sys_*_write* \
--latency \
--cpu 0-1 \
--realtime=99 \
--output=perf.data \
--freq=max \
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
--synth=all \
-e mem-loads \
-e mem-stores \
-e intel_pt// \
-e context_tracking:* \
-e damon:* \
-e ftrace:* \
-e io_uring:* \
-e kmem:* \
-e lock:* \
-e msr:rdpmc \
-e osnoise:osnoise_sample \
-e osnoise:sample_threshold \
-e osnoise:thread_noise \
-e percpu:* \
-e power:cpu_idle \
-e power:cpu_idle_miss \
-e sched:* \
-e task:task_newtask \
-e vmscan:* --
"
# perf record captures and dumps data
# major-faults records "major" page faults
# minor-faults record "minor" page faults
# You may consider combining the top two and use page-faults instead
# REF: https://sandpile.org/x86/msr.htm
# msr/aperf/ records the "actual performance clock count" MSR
# msr/mperf/ records the "maximum performance clock count" MSR
# msr/pperf/ records the "productive performance clock count" MSR
# msr/smi/ records miscellaneous CPU resource management operations
# msr/tsc/ records the TSC, yes the thing your code already does, but it affiliates events to the TSC
# latency records latency
# cpu records CPUs 0 to 1
# realtime=99 ensures the highest schedule priority, which should be fine since it's pinned to a CPU core
# freq=max ensures maximum frequency profiling
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
# mem-loads and mem-stores are self-explanatory
# intel_pt// is the Processor Trace
# context_tracking:* records context switches between userland and kernel
# damon:* records Data Access MONitor related data for DRAM level operations
# ftrace:* records a subset of ftrace functionality
# io_uring:* records its async i/o, just in case
# kmem:* record kernel memory operations
# lock:* records kernel-level lock contentions
# msr:rdpmc records the RDPMC (Read Performance-Monitoring Counters) register
# osnoise:osnoise_sample records OS noise
# osnoise:sample_threshold records the OS noise threshold
# osnoise:thread_noise records OS thread noise
# percpu:* records per CPU information
# power:cpu_idle records when the CPU idles
# power:cpu_idle_miss records when the CPU idles wrongly
# sched:* record all scheduling events
# task:task_newtask records when a new task is created
# vmscan:* records all vmem scanning, for page reclaim
# uncore_imc_free_running:* records integrated memory counters (iMC) operations

STRACE="taskset -c 2 nice -n -20 sudo -u \"$SUDO_USER\" strace \
-e all \
--follow-forks \
--output-separately \
--output=strace_latency \
--status=all \
--verbose=all \
--decode-fds=all \
--decode-pids=comm,pidns \
--instruction-pointer \
--syscall-number \
--arg-names \
--stack-trace=source \
--relative-timestamps=ns \
--absolute-timestamps=format:unix,precision:us \
--syscall-times=us \
--no-abbrev \
--const-print-style=verbose
"

# No singular man page, so https://docs.kernel.org/trace/ftrace.html
# https://man7.org/linux/man-pages/man1/trace-cmd.1.html
# https://man7.org/linux/man-pages/man1/trace-cmd-record.1.html
FTRACE="taskset -c 2 nice -n -20 sudo -u \"$SUDO_USER\" trace-cmd record \
-p function_graph \
-K \
-a \
-T \
-r 99 \
--date \
--proc-map \
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
    eval "$PERF \"$BIN\""
    echo "==== running strace capture ====" >&2
    eval "$STRACE \"$BIN\""

    echo "==== running ftrace capture ====" >&2
    sudo sh -c "
    echo 0                    >  /sys/kernel/tracing/tracing_on
    echo                      >  /sys/kernel/tracing/trace
    echo                      >  /sys/kernel/tracing/set_event
    echo 'sched:*'            >> /sys/kernel/tracing/set_event
    echo 'syscalls:*'         >> /sys/kernel/tracing/set_event
    echo 'irq:*'              >> /sys/kernel/tracing/set_event
    echo 'workqueue:*'        >> /sys/kernel/tracing/set_event
    echo 'kmem:*'             >> /sys/kernel/tracing/set_event
    echo 'context_tracking:*' >> /sys/kernel/tracing/set_event
    echo 'damon:*'            >> /sys/kernel/tracing/set_event
    echo 'ftrace:*'           >> /sys/kernel/tracing/set_event
    echo 'io_uring:*'         >> /sys/kernel/tracing/set_event
    echo 'lock:*'             >> /sys/kernel/tracing/set_event
    echo 'osnoise:*'          >> /sys/kernel/tracing/set_event
    echo 'percpu:*'           >> /sys/kernel/tracing/set_event
    echo 'power:*'            >> /sys/kernel/tracing/set_event
    echo 'task:*'             >> /sys/kernel/tracing/set_event
    echo 'vmscan:*'           >> /sys/kernel/tracing/set_event
    "

    sudo sh -c "
        echo 0                    >  /sys/kernel/tracing/tracing_on
        echo                      >  /sys/kernel/tracing/set_event
        "
else
    "$BIN"
fi
