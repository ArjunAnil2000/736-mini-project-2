#!/usr/bin/env bash
# Tracing harness for throughput: explains WHERE THE TIME GOES (perf stat, perf record, strace, ftrace).
# The throughput twin of latency-trace.sh; the shared logic is in trace-lib.sh. The numbers to REPORT for
# throughput come from throughput-runner.sh, not from here: tracing perturbs timing.
#
# Steps (all output in results/trace/throughput/<timestamp>/):
#   1. perf stat   - per chunk size, 5 counter groups, plus 3 baselines per group (startup cost,
#                    subtracted by trace_summary_throughput.py)          -> stat_<group>_<size>.csv
#   2. perf record - every scheduling/syscall/idle/IPI/... tracepoint    -> perf_trace.data
#   2b. perf record - ONLY scheduler/IPI/idle events, per size, much more data (enough wakeups to
#                    measure: streaming wakes a task about once per 64 KB moved)   -> perf_wakeup_<size>.data
#   3. perf record - cycles sampled with call graphs, per chunk size     -> perf_cycles_<size>.data
#   4. strace      - every syscall of the benchmark                      -> strace_throughput.<pid>
#   5. ftrace      - kernel call graph under read()/write()              -> trace.dat
#   then trace_summary_throughput.py turns it into summary.txt, and two further scripts write their own
#   directories (shared with latency-trace.sh):
#     wakeup/       wakeup_timeline.py   - every wakeup broken into phases (from the 2b captures)
#     flamegraphs/  ftrace_flamegraph.py - kernel call tree under read()/write() (from trace.dat)
#
# Must be run with sudo. CPU 0 (parent) and CPU 1 (child) are P-cores pinned to 4000 MHz; the tracers
# run on CPU 2 (also pinned). The benchmark is dropped to the invoking user.
#
# Usage: sudo ./scripts/throughput-trace.sh 2>&1 | tee results/throughput-trace-run.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TRACE_NAME=throughput
BIN="$ROOT_DIR/out/throughput"

# How much data each traced phase moves. This differs from latency, on purpose: throughput streams data
# ONE way in chunks of a given size, with a single 1-byte ack at the end, so there is no "round trip" to
# count. Counters are normalised per chunk and per MiB instead, and every phase moves a chosen number of
# BYTES per chunk size (so the pipe fills and the writer blocks, i.e. the steady state, not just the start):
#   perf stat     one run per size moving STAT_BYTES (dwarfs the fixed startup cost, which is also
#                 measured by 3 baseline runs of a single 4-byte chunk and subtracted). Tiny chunks would
#                 need millions of syscalls to move that much, so the chunk count is capped.
#   tracepoints   ALL sizes in one run, TRACE_BYTES per size (128 KiB = 2x the 64 KB pipe buffer): every
#                 event is recorded, so keep it small. A chunk larger than that moves as one chunk.
#   wakeups       one recording PER SIZE, only 5 event types (no per-syscall events, which is what makes the
#                 general capture huge), WAKE_BYTES each (128 MiB) with the chunk count capped, so small chunks
#                 move less (4 B: 8M chunks = 32 MB = ~500 wakeups). A wakeup is rare in streaming (~1 per
#                 64 KB moved); the 128 KiB general capture yielded only 2 wakeups at 4 B.
#   cycles        one profile per size (4 B, 4 KB, 64 KB, 512 KB: syscall-bound, near the peak, at the pipe
#                 size, past the drop-off), CYCLES_BYTES each, capped in chunks, ~0.3-0.5 s of work.
#   strace        strace slows every syscall and, with stacks, writes ~1 KB per call: STRACE_BYTES only.
#   ftrace        function_graph is heavier still: FTRACE_BYTES only.
# Every traced run uses -n (no clock calibration) AND -q (no CSV printing, whose cost would otherwise be
# counted as if it were pipe cost) and -r 1 (one repeat: a repeat only adds another identical transfer).
STAT_BYTES=268435456;   STAT_MAX_CHUNKS=1000000;   STAT_BASELINES=3
TRACE_BYTES=131072
CYCLES_BYTES=1073741824; CYCLES_MAX_CHUNKS=1000000
CYCLES_SIZES=(4 4096 65536 524288)
STRACE_BYTES=8192
FTRACE_BYTES=4096
WAKE_BYTES=134217728;   WAKE_MAX_CHUNKS=8000000

source "$SCRIPT_DIR/trace-lib.sh"

# chunks_for <bytes> <size> <max_chunks>: how many chunks of <size> move <bytes>, at least 1, at most max
chunks_for()
{
    local n=$(( $1 / $2 ))
    if [ "$n" -lt 1 ]; then n=1; fi
    if [ "$n" -gt "$3" ]; then n=$3; fi
    echo "$n"
}

declare -A STAT_CHUNKS CYCLES_CHUNKS WAKE_CHUNKS
for sz in "${SIZES[@]}"; do
    STAT_CHUNKS[$sz]=$(chunks_for "$STAT_BYTES" "$sz" "$STAT_MAX_CHUNKS")
done
for sz in "${CYCLES_SIZES[@]}"; do
    CYCLES_CHUNKS[$sz]=$(chunks_for "$CYCLES_BYTES" "$sz" "$CYCLES_MAX_CHUNKS")
done
for sz in "${SIZES[@]}"; do
    WAKE_CHUNKS[$sz]=$(chunks_for "$WAKE_BYTES" "$sz" "$WAKE_MAX_CHUNKS")
done

# "size:chunks size:chunks ..." so the summary knows exactly how much each run moved
pairs()
{
    local -n arr=$1
    local sz out=""
    shift
    for sz in "$@"; do out+="$sz:${arr[$sz]} "; done
    echo "${out% }"
}

trace_setup

trace_params "stat_bytes=$STAT_BYTES" "stat_max_chunks=$STAT_MAX_CHUNKS" "stat_baselines=$STAT_BASELINES" \
    "stat_chunks=$(pairs STAT_CHUNKS "${SIZES[@]}")" \
    "trace_bytes=$TRACE_BYTES" "wake_bytes=$WAKE_BYTES" "wake_max_chunks=$WAKE_MAX_CHUNKS" \
    "wake_chunks=$(pairs WAKE_CHUNKS "${SIZES[@]}")" \
    "cycles_bytes=$CYCLES_BYTES" "cycles_sizes=${CYCLES_SIZES[*]}" \
    "cycles_chunks=$(pairs CYCLES_CHUNKS "${CYCLES_SIZES[@]}")" \
    "strace_bytes=$STRACE_BYTES" "ftrace_bytes=$FTRACE_BYTES" \
    "sizes=${SIZES[*]}" "groups=${#STAT_GROUPS[@]}" "freq_khz=$FREQ" \
    "parent_cpu=$PARENT_CPU" "child_cpu=$CHILD_CPU" "tracer_cpu=$TRACE_CPU"

echo "== running throughput (parent on CPU $PARENT_CPU, child on CPU $CHILD_CPU) ==" >&2

echo "==== 1/5 perf stat: ${#STAT_GROUPS[@]} groups x ${#SIZES[@]} sizes + baselines ====" >&2
for gi in "${!STAT_GROUPS[@]}"; do
    # baselines: the same program moving a single 4-byte chunk, i.e. (almost) only startup cost. Several,
    # and the summary takes the median, because one baseline's own noise shows up in every size.
    for k in $(seq 1 "$STAT_BASELINES"); do
        do_perf_stat "$RUN_DIR/stat_${gi}_baseline_${k}.csv" "${STAT_GROUPS[$gi]}" \
            "$BIN" -n -q -s 4 -i 1 -r 1
    done
    for sz in "${SIZES[@]}"; do
        do_perf_stat "$RUN_DIR/stat_${gi}_${sz}.csv" "${STAT_GROUPS[$gi]}" \
            "$BIN" -n -q -s "$sz" -i "${STAT_CHUNKS[$sz]}" -r 1
    done
done

echo "==== 2/5 perf record: tracepoints ====" >&2
do_perf_trace "$RUN_DIR/perf_trace.data" "$BIN" -n -q -b "$TRACE_BYTES" -r 1

# Wakeup captures: like do_perf_trace (trace-lib.sh) but only the events a wakeup consists of, and no syscall
# events. Defined here, not in trace-lib.sh, to keep the shared library unchanged.
WAKE_EVENTS=(sched:sched_waking sched:sched_wakeup sched:sched_switch ipi:ipi_send_cpu power:cpu_idle)
do_perf_wakeup()
{
    local out=$1 ev args=()
    shift
    for ev in "${WAKE_EVENTS[@]}"; do args+=(-e "$ev"); done
    "${TOOL_PIN[@]}" perf record -c 1 "${args[@]}" --cpu "$PARENT_CPU,$CHILD_CPU" --realtime=99 \
        --sample-cpu --synth=task -m 8192 -o "$out" -- "${DROP[@]}" "$@" > /dev/null
    check_perf_data "$out"
}

echo "==== 2b/5 perf record: wakeups only, per size (${#SIZES[@]} recordings) ====" >&2
perf_limits_raise
for sz in "${SIZES[@]}"; do
    do_perf_wakeup "$RUN_DIR/perf_wakeup_${sz}.data" "$BIN" -n -q -s "$sz" -i "${WAKE_CHUNKS[$sz]}" -r 1
done
perf_limits_restore

echo "==== 3/5 perf record: cycles + call graphs, per size ====" >&2
for sz in "${CYCLES_SIZES[@]}"; do
    do_perf_cycles "$RUN_DIR/perf_cycles_${sz}.data" \
        "$BIN" -n -q -s "$sz" -i "${CYCLES_CHUNKS[$sz]}" -r 1
done

echo "==== 4/5 strace ====" >&2
do_strace "$BIN" -n -q -b "$STRACE_BYTES" -r 1

echo "==== 5/5 ftrace (trace-cmd) ====" >&2
do_ftrace "$BIN" -n -q -b "$FTRACE_BYTES" -r 1

# matplotlib is installed for the invoking USER only; this script runs as root, whose python cannot see it, so
# the breakdown chart used to be skipped silently (found on the latency tracer). Point root's python at the
# user's site-packages (read-only use; PYTHONDONTWRITEBYTECODE keeps root from writing files there).
USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
USER_SITE="$(HOME="$USER_HOME" python3 -c 'import site; print(site.getusersitepackages())')"
MPLCONFIGDIR="$(mktemp -d)"   # matplotlib font cache, kept out of the results directory
export PYTHONPATH="$USER_SITE${PYTHONPATH:+:$PYTHONPATH}" MPLCONFIGDIR

echo "==== summary ====" >&2
PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/trace_summary_throughput.py" "$RUN_DIR" \
    | tee "$RUN_DIR/summary.txt" >&2

# Extra outputs, in their own directories (never mixed into summary.txt). A failure here must not fail
# the run: the raw captures are already saved.
echo "==== wakeup timeline -> $RUN_DIR/wakeup/ ====" >&2
PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/wakeup_timeline.py" "$RUN_DIR" >&2 \
    || echo "(wakeup_timeline.py failed; re-run it by hand on $RUN_DIR)" >&2
echo "==== ftrace flame graphs -> $RUN_DIR/flamegraphs/ ====" >&2
PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/ftrace_flamegraph.py" "$RUN_DIR" >&2 \
    || echo "(ftrace_flamegraph.py failed; re-run it by hand on $RUN_DIR)" >&2
rm -rf "$MPLCONFIGDIR"
