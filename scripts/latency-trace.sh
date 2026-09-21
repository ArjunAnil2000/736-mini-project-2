#!/usr/bin/env bash
# Tracing harness for latency: explains WHERE THE TIME GOES (perf stat, perf record, strace, ftrace).
# Started as Aidan's perf/strace/ftrace harness (split out of latency-runner.sh); the shared logic is
# now in trace-lib.sh so a throughput-trace.sh can reuse it. The numbers to REPORT for latency come
# from latency-runner.sh, not from here: tracing perturbs timing.
#
# Steps (all output in results/trace/latency/<timestamp>/):
#   1. perf stat   - per message size, 5 counter groups, plus a baseline per group (startup cost,
#                    subtracted by trace_summary.py)                     -> stat_<group>_<size>.csv
#   2. perf record - every scheduling/syscall/idle/IPI/... tracepoint    -> perf_trace.data
#   3. perf record - cycles sampled with call graphs, per size (4 B, 64 KB, 512 KB)
#                                                                        -> perf_cycles_<size>.data
#   4. strace      - every syscall of the benchmark                      -> strace_latency.<pid>
#   5. ftrace      - kernel call graph under read()/write()              -> trace.dat
#   then trace_summary.py turns it into summary.txt.
#
# Must be run with sudo. CPU 0 (parent) and CPU 1 (child) are P-cores pinned to 4000 MHz; the tracers
# run on CPU 2 (also pinned). The benchmark is dropped to the invoking user.
#
# Usage: sudo ./scripts/latency-trace.sh 2>&1 | tee results/trace-run.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TRACE_NAME=latency
BIN="$ROOT_DIR/out/latency"

# Round counts per phase. They differ on purpose:
#   perf stat     needs many rounds so the benchmark dwarfs the fixed startup cost (which is also
#                 measured and subtracted); warmup is counted too, hence the division in the summary
#   tracepoints,  small: they record every event, and 5+10 rounds per size is plenty to read
#   strace, ftrace
#   cycles        many rounds: sampling needs enough samples to be statistically meaningful; one
#                 profile per size, because the cost is dominated by different things at 4 B (fixed
#                 syscall + wakeup cost) and 512 KB (copying), and one mixed profile hides that. The
#                 iteration counts aim at roughly 0.5-1 s of benchmark work each.
# Every traced run uses -n (no clock calibration) AND -q (no per-iteration printf, whose cost would
# otherwise be counted as if it were pipe cost).
STAT_WARMUP=10;   STAT_ITERATIONS=3000;  STAT_BASELINES=3
TRACE_WARMUP=5;   TRACE_ITERATIONS=10
CYCLES_WARMUP=20
CYCLES_SIZES=(4 65536 524288)
declare -A CYCLES_ITERATIONS=([4]=200000 [65536]=50000 [524288]=5000)

source "$SCRIPT_DIR/trace-lib.sh"
trace_setup

trace_params "stat_warmup=$STAT_WARMUP" "stat_iterations=$STAT_ITERATIONS" \
    "trace_warmup=$TRACE_WARMUP" "trace_iterations=$TRACE_ITERATIONS" \
    "stat_baselines=$STAT_BASELINES" "cycles_warmup=$CYCLES_WARMUP" \
    "cycles_sizes=${CYCLES_SIZES[*]}" \
    "cycles_iterations_4=${CYCLES_ITERATIONS[4]}" "cycles_iterations_65536=${CYCLES_ITERATIONS[65536]}" \
    "cycles_iterations_524288=${CYCLES_ITERATIONS[524288]}" \
    "sizes=${SIZES[*]}" "groups=${#STAT_GROUPS[@]}" "freq_khz=$FREQ" \
    "parent_cpu=$PARENT_CPU" "child_cpu=$CHILD_CPU" "tracer_cpu=$TRACE_CPU"

# -n: no TSC calibration (see trace-lib.sh). All benchmark output is discarded by the do_* helpers.
echo "== running latency (parent on CPU $PARENT_CPU, child on CPU $CHILD_CPU) ==" >&2

echo "==== 1/5 perf stat: ${#STAT_GROUPS[@]} groups x ${#SIZES[@]} sizes + baselines ====" >&2
for gi in "${!STAT_GROUPS[@]}"; do
    # baselines: the same program doing a single round trip, i.e. (almost) only startup cost. Several,
    # and the summary takes the median, because one baseline's own noise shows up in every size.
    for k in $(seq 1 "$STAT_BASELINES"); do
        do_perf_stat "$RUN_DIR/stat_${gi}_baseline_${k}.csv" "${STAT_GROUPS[$gi]}" "$BIN" -n -q -s 4 0 1
    done
    for sz in "${SIZES[@]}"; do
        do_perf_stat "$RUN_DIR/stat_${gi}_${sz}.csv" "${STAT_GROUPS[$gi]}" \
            "$BIN" -n -q -s "$sz" "$STAT_WARMUP" "$STAT_ITERATIONS"
    done
done

echo "==== 2/5 perf record: tracepoints ====" >&2
do_perf_trace "$RUN_DIR/perf_trace.data" "$BIN" -n -q "$TRACE_WARMUP" "$TRACE_ITERATIONS"

echo "==== 3/5 perf record: cycles + call graphs, per size ====" >&2
for sz in "${CYCLES_SIZES[@]}"; do
    do_perf_cycles "$RUN_DIR/perf_cycles_${sz}.data" \
        "$BIN" -n -q -s "$sz" "$CYCLES_WARMUP" "${CYCLES_ITERATIONS[$sz]}"
done

echo "==== 4/5 strace ====" >&2
do_strace "$BIN" -n -q "$TRACE_WARMUP" "$TRACE_ITERATIONS"

echo "==== 5/5 ftrace (trace-cmd) ====" >&2
do_ftrace "$BIN" -n -q "$TRACE_WARMUP" "$TRACE_ITERATIONS"

echo "==== summary ====" >&2
PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/trace_summary.py" "$RUN_DIR" | tee "$RUN_DIR/summary.txt" >&2
