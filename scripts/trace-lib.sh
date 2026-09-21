#!/usr/bin/env bash
# trace-lib.sh - shared plumbing for the tracing scripts (latency-trace.sh, and later
# throughput-trace.sh). SOURCE this file, do not run it.
#
# What the tracing scripts are for: explaining WHERE THE TIME GOES for a benchmark (the assignment's
# third part). They are not for the reported latency/throughput numbers - tracing perturbs timing,
# so those come from the clean runs (latency-runner.sh / throughput-runner.sh).
#
# The caller must define, before sourcing:
#   TRACE_NAME   "latency" or "throughput"; results go to results/trace/<TRACE_NAME>/<timestamp>/
#   BIN          path of the benchmark binary
#   SCRIPT_DIR   the scripts/ directory
#   ROOT_DIR     the repository root
#
# Design points (each one fixes something that made the first captures useless):
#  * Everything runs as root (perf_event_paranoid=2 and /sys/kernel/tracing is root-only), but the
#    benchmark itself is dropped to $SUDO_USER with setpriv. setpriv exec()s the benchmark in place,
#    so a tool that follows the command sees only the benchmark, not a sudo/PAM wrapper.
#  * The benchmark is run with -n (no ~1 s TSC calibration): calibration was ~99% of each run, so
#    system-wide counters were measuring an idle machine and the desktop, not the benchmark.
#  * Counters are per-process (perf stat on the benchmark and its child only), one small group per
#    run so nothing is multiplexed, with a baseline run subtracted out (startup cost).
#  * Every tracepoint occurrence is recorded (-c 1, throttling disabled) instead of sampled.
#  * Commands are bash arrays, never eval'ed strings (a stray newline in an eval'ed string once ran
#    perf with no workload, forever).

PARENT_CPU=0
CHILD_CPU=1
TRACE_CPU=2 # the tracers run here, away from the benchmark's two cores
FREQ=4000000 # kHz = 4000 MHz

SIZES=(4 16 64 256 1024 4096 16384 65536 262144 524288)

# Overridable so the flow can be tested without root (see the stub test in CLAUDE.md).
TRACEFS="${TRACEFS:-/sys/kernel/tracing}"
PIN_FREQ="${PIN_FREQ:-$SCRIPT_DIR/pin_freq.sh}"
UNPIN_FREQ="${UNPIN_FREQ:-$SCRIPT_DIR/unpin_freq.sh}"

# Counter groups for `perf stat`; each group is its own run so it fits the PMU's few counters.
STAT_GROUPS=(
    "context-switches,cpu-migrations,page-faults,task-clock,cycles,instructions,branch-misses"
    "cache-references,cache-misses,L1-dcache-load-misses,LLC-load-misses"
    # loads served from the OTHER core's cache = data moving between core 0 and core 1
    "mem_load_l3_hit_retired.xsnp_hitm,mem_load_l3_hit_retired.xsnp_fwd,mem_load_retired.l2_miss"
    "dTLB-load-misses,iTLB-load-misses"
    # P-core top-down; the slots event has to lead the group
    "{cpu_core/slots/,cpu_core/topdown-retiring/,cpu_core/topdown-fe-bound/,cpu_core/topdown-be-bound/,cpu_core/topdown-bad-spec/}"
)

# Tracepoints for `perf record`, every occurrence. What each is for:
#   sched:sched_switch / sched_waking / sched_wakeup / sched_migrate_task - context switches and the
#     waking->running gap (scheduler latency), and core migrations
#   syscalls:sys_{enter,exit}_{read,write} - exact time inside read()/write()
#   power:cpu_idle - a sleeping core waking up is a big part of ping-pong latency
#   ipi:ipi_send_cpu - the cross-core interrupt that wakes the other side
#   lock:contention_begin/end - pipe mutex contention (large messages)
#   kmem:mm_page_alloc, kmem:kmem_cache_alloc - pipe buffer allocation
#   irq:softirq_entry - softirq noise on the benchmark's cores
TRACE_EVENTS=(
    sched:sched_switch sched:sched_waking sched:sched_wakeup sched:sched_migrate_task
    syscalls:sys_enter_read syscalls:sys_exit_read syscalls:sys_enter_write syscalls:sys_exit_write
    power:cpu_idle ipi:ipi_send_cpu lock:contention_begin lock:contention_end
    kmem:mm_page_alloc kmem:kmem_cache_alloc irq:softirq_entry
)

# strace flags, Aidan's, unchanged
STRACE_FLAGS=(
    -e all --follow-forks --output-separately --status=all --verbose=all --decode-fds=all
    --decode-pids=comm,pidns --instruction-pointer --syscall-number --arg-names --stack-trace=source
    --relative-timestamps=ns --absolute-timestamps=format:unix,precision:us --syscall-times=us
    --no-abbrev --const-print-style=verbose
)

# trace-cmd (ftrace). -F -c: only the benchmark and its child (before, 73% of the trace was
# trace-cmd tracing itself). -g: graph only ksys_read/ksys_write and what they call, i.e. the kernel
# side of the pipe read/write. No -T (a stack trace on every event made the stack unwinder a large
# part of the trace). -K was removed earlier (invalid in trace-cmd 3.3.1); -i skips missing events.
# The 15 event systems are Aidan's list.
FTRACE_FLAGS=(
    -p function_graph -g ksys_read -g ksys_write -F -c -a -r 99 --date --proc-map -i
    -e sched -e syscalls -e irq -e workqueue -e kmem -e context_tracking -e damon -e ftrace
    -e io_uring -e lock -e osnoise -e percpu -e power -e task -e vmscan
)

trace_setup()
{
    if [ "$(id -u)" -ne 0 ]; then
        echo "error: must be run with sudo (needed to pin/unpin CPU frequency)" >&2
        exit 1
    fi
    : "${SUDO_USER:?run this via sudo from a normal user account, not from a root login}"
    : "${SUDO_UID:?run this via sudo from a normal user account}"
    : "${SUDO_GID:?run this via sudo from a normal user account}"

    if [ ! -x "$BIN" ]; then
        echo "error: $BIN not found or not executable (run 'make' first)" >&2
        exit 1
    fi
    for tool in perf strace trace-cmd setpriv python3; do
        if ! command -v "$tool" > /dev/null; then
            echo "error: $tool not found (trace-cmd: sudo dnf install trace-cmd)" >&2
            exit 1
        fi
    done

    # Prefixes for every command: run the TRACER on the tracing core at top priority; the benchmark
    # is dropped to the invoking user (it re-pins itself to CPUs 0/1).
    TOOL_PIN=(taskset -c "$TRACE_CPU" nice -n -20)
    DROP=(setpriv --reuid "$SUDO_UID" --regid "$SUDO_GID" --init-groups)

    # One directory per run, so runs never mix. Written as root; cleanup() hands it back.
    RUN_DIR="$ROOT_DIR/results/trace/$TRACE_NAME/$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$RUN_DIR"

    # System state changed by the tracing, saved here and put back by cleanup(). (The ftrace
    # *buffer contents* cannot be restored; trace-cmd resets the buffers.)
    SAVED_TRACER="$(cat "$TRACEFS/current_tracer")"
    SAVED_TRACING_ON="$(cat "$TRACEFS/tracing_on")"
    SAVED_EVENTS="$(cat "$TRACEFS/set_event")"
    SAVED_SAMPLE_RATE="$(sysctl -n kernel.perf_event_max_sample_rate)"
    SAVED_CPU_PERCENT="$(sysctl -n kernel.perf_cpu_time_max_percent)"

    trap trace_cleanup EXIT

    local cpu
    for cpu in "$PARENT_CPU" "$CHILD_CPU" "$TRACE_CPU"; do
        echo "== pinning CPU $cpu ==" >&2
        "$PIN_FREQ" "$cpu" "$FREQ" >&2
    done
}

# Runs on every exit, including failures. `set +e` + `|| true` so one failing step cannot skip the
# steps after it.
trace_cleanup()
{
    set +e
    echo "== restoring system state ==" >&2
    echo nop > "$TRACEFS/current_tracer"
    echo > "$TRACEFS/set_event"
    echo "$SAVED_EVENTS" | while read -r ev; do
        [ -n "$ev" ] && echo "$ev" >> "$TRACEFS/set_event"
    done
    echo "$SAVED_TRACER" > "$TRACEFS/current_tracer"
    echo "$SAVED_TRACING_ON" > "$TRACEFS/tracing_on"
    perf_limits_restore
    local cpu
    for cpu in "$PARENT_CPU" "$CHILD_CPU" "$TRACE_CPU"; do
        echo "== unpinning CPU $cpu ==" >&2
        "$UNPIN_FREQ" "$cpu" >&2 || true
    done
    # results/trace and results/trace/<name> are created as root too; without this you could not
    # delete old runs
    chown "$SUDO_USER" "$ROOT_DIR/results/trace" "$ROOT_DIR/results/trace/$TRACE_NAME" 2> /dev/null
    chown -R "$SUDO_USER" "$RUN_DIR" 2> /dev/null
    echo "== output in $RUN_DIR ==" >&2
}

# Key=value record of how the run was made, read by trace_summary.py.
trace_params()
{
    printf '%s\n' "name=$TRACE_NAME" "date=$(date -Is)" "kernel=$(uname -r)" "$@" > "$RUN_DIR/params.txt"
}

# perf stat: counters for ONE process tree (the benchmark and its child), root so kernel time counts.
#   do_perf_stat <out.csv> <event list> <benchmark command...>
do_perf_stat()
{
    local out=$1 events=$2
    shift 2
    "${TOOL_PIN[@]}" perf stat -x, -e "$events" -o "$out" -- "${DROP[@]}" "$@" > /dev/null
}

# perf's throttling drops events (the default rate limit silently threw away ~70% of the benchmark's
# syscalls in the first capture), so it is switched off ONLY around the tracepoint recording.
# Two sysctls, and the ORDER matters: the kernel refuses to change the sample rate while
# perf_cpu_time_max_percent is 0, so raise rate then percent, and restore percent then rate. (An
# earlier version restored them the wrong way round: the rate stayed at 100000000, the kernel
# computed a 2 ns interrupt budget and then kept lowering the rate by itself.)
perf_limits_raise()
{
    sysctl -qw kernel.perf_event_max_sample_rate=100000000
    sysctl -qw kernel.perf_cpu_time_max_percent=0
    LIMITS_RAISED=1
}

perf_limits_restore()
{
    [ "${LIMITS_RAISED:-0}" = 1 ] || return 0
    sysctl -qw kernel.perf_cpu_time_max_percent="$SAVED_CPU_PERCENT"
    sysctl -qw kernel.perf_event_max_sample_rate="$SAVED_SAMPLE_RATE"
    LIMITS_RAISED=0
}

# perf record of every tracepoint occurrence on the benchmark's two cores.
#   do_perf_trace <out.data> <benchmark command...>
do_perf_trace()
{
    local out=$1 ev args=()
    shift
    for ev in "${TRACE_EVENTS[@]}"; do args+=(-e "$ev"); done
    perf_limits_raise
    "${TOOL_PIN[@]}" perf record -c 1 "${args[@]}" --cpu "$PARENT_CPU,$CHILD_CPU" --realtime=99 \
        --sample-cpu --synth=task -m 8192 -o "$out" -- "${DROP[@]}" "$@" > /dev/null
    perf_limits_restore
    check_perf_data "$out"
}

# perf record of where the CPU cycles go, by function, with call graphs. Per-process, so only the
# benchmark. Needs many iterations to get enough samples.
#   do_perf_cycles <out.data> <benchmark command...>
do_perf_cycles()
{
    local out=$1
    shift
    "${TOOL_PIN[@]}" perf record -e cycles -F 9999 --call-graph fp --realtime=99 -o "$out" \
        -- "${DROP[@]}" "$@" > /dev/null
    check_perf_data "$out"
}

# Say so loudly if perf threw samples away; a capture with lost events must not be read as complete.
check_perf_data()
{
    local bad
    bad="$(perf report -i "$1" --stat 2> /dev/null | grep -E 'THROTTLE|LOST' | grep -vE ' 0 ' || true)"
    if [ -n "$bad" ]; then
        echo "WARNING: $1 is INCOMPLETE - perf dropped samples:" >&2
        echo "$bad" >&2
    fi
}

#   do_strace <benchmark command...>
do_strace()
{
    "${TOOL_PIN[@]}" strace "${STRACE_FLAGS[@]}" --output="$RUN_DIR/strace_$TRACE_NAME" \
        -- "${DROP[@]}" "$@" > /dev/null
}

#   do_ftrace <benchmark command...>
do_ftrace()
{
    "${TOOL_PIN[@]}" trace-cmd record "${FTRACE_FLAGS[@]}" -o "$RUN_DIR/trace.dat" \
        --user "$SUDO_USER" "$@" > /dev/null
}
