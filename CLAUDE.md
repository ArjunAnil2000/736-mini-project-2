# Project state snapshot (pre-reset)

This snapshot documents the directory structure and file purposes from the first pass at
this project, taken before wiping the working tree to restart the build in a clean,
step-by-step flow for learning purposes. `lap-specs.md` and `736-p2.pdf` (the assignment
spec) were kept; everything else listed below was deleted.

## Directory structure at time of snapshot

```
.
├── 736-p2.pdf              # assignment spec (kept)
├── lap-specs.md            # hardware/software specs, command->output format (kept)
├── results.md              # (deleted) written summary of major experimental findings
├── Makefile                # (deleted) built src/*.c -> out/*
├── src/                    # (deleted) C sources
│   ├── timing.h            #   rdtsc/rdtscp wrappers + TSC calibration vs CLOCK_MONOTONIC
│   ├── clockres.c          #   timer resolution test (clock_gettime/gettimeofday/rdtsc) + sleep(2) check
│   ├── latency.c            #   fork+pipe ping-pong round-trip latency, sizes 4B-512K, CSV output
│   ├── throughput.c         #   one-way bulk pipe transfer + single ack, same size range, CSV output
│   └── latency_rusage.c     #   single-size latency variant that also reports getrusage() ctxsw counts
├── out/                    # (deleted) compiled binaries (clockres, latency, throughput, latency_rusage)
├── scripts/
│   └── run_all.sh          # (deleted) ran all benchmarks + strace -c + perf stat, wrote results/
├── results/                # (deleted) raw CSV/text output from run_all.sh
│   ├── clockres.txt
│   ├── latency_raw.csv
│   ├── throughput_raw.csv
│   ├── ctxsw_by_size.csv
│   ├── strace_small.txt / strace_large.txt
│   ├── perf_small.txt / perf_large.txt
│   └── latency_err.txt      #   stray file from a shell-pipeline (head/tee SIGPIPE) artifact, not real data
└── paper/                  # (deleted) empty placeholder for the eventual conference-style write-up
```

## What each piece was for (for quick recall when rebuilding)

- **timing.h**: shared timing primitives — `rdtsc`/`rdtscp` with lfence serialization, and
  a `calibrate_tsc_hz()` that times a 100ms `nanosleep` against `CLOCK_MONOTONIC` (best of 5
  trials) to convert TSC cycles to seconds.
- **clockres.c**: measured smallest non-zero back-to-back delta for `clock_gettime`,
  `gettimeofday`, and `rdtsc`; cross-checked all three against a real `sleep(2)`.
- **latency.c**: parent/child connected by two pipes (p2c, c2p); child echoes back whatever
  it reads; parent times full round trip per message size (4B-512K per assignment spec),
  printing per-iteration CSV rows after a warmup period.
- **throughput.c**: parent streams a large total (~64MB, scaled by size with min/max
  iteration bounds) to the child in fixed-size chunks; child acks once with 1 byte after
  consuming the whole transfer; parent times start-of-transfer to ack-received, best of 3
  repeats.
- **latency_rusage.c**: same round-trip idea as latency.c but fixed single size + iteration
  count, and additionally snapshots `getrusage(RUSAGE_SELF)` before/after to get voluntary/
  involuntary context-switch deltas — used because `perf_event_paranoid=2` blocked
  unprivileged `perf stat` from counting kernel context-switch events.
- **run_all.sh**: orchestrated everything above, plus `strace -f -c` and `perf stat`
  (cycles/instructions/task-clock only, once perf was installed) for small (4B) vs. large
  (256K) message comparisons, writing all output into results/.

## Key findings from the first pass (see git history / prior conversation for full detail)

- clock_gettime and rdtsc both resolve to ~10ns; gettimeofday only ~1us; all three agreed
  on a sleep(2) check within ~50us.
- Pipe RTT latency was flat (~1.2us one-way) up to ~4KB messages, then grew sharply past
  the 64KB default pipe buffer size.
- Throughput peaked (~5.9 GB/s) at 16KB chunks and *dropped* ~35% for larger chunks rather
  than plateauing.
- Context switches per round trip scaled as `ceil(size/65536)` — confirmed via getrusage,
  strace, and perf — explaining both the latency knee and the throughput drop-off.
- This machine (Intel Core Ultra 7 258V) is a hybrid P/E-core CPU; perf showed the process
  running on both core types mid-measurement, meaning `taskset` core-pinning should be
  added for cleaner measurements in the next pass.

## Current pass: state as of 2026-09-21 (session paused here, resuming later)

Both open design notes above (protocol split, keeping `memcmp` out of the timed loop) have
since been acted on - see below. This section is the actual up-to-date snapshot to read when
resuming. Work is now done by two people: Arjun (user, wrote the benchmarks) and teammate Aidan
Liaw (added tracing/perf work on branch `aidan-branch`, merged to master in `3106851`) - see
"Teammate changes" below.

The repo root is now `projects/p2/` itself (the git repo); `736-p2.pdf`, `CLAUDE.md` and
`clockres-learnings.md` were moved into it and are still untracked.

### Directory structure (current)

```
.
├── 736-p2.pdf                        # assignment spec (untracked)
├── README.md                         # short repo blurb (points at lap-specs.md)
├── lap-specs.md                      # hardware/software specs, command->output format
│                                        NOTE: some sections (CPU flags line, frequency
│                                        scaling / turbo, memory, perf_event_paranoid) were
│                                        deliberately trimmed by the user at one point; the
│                                        governor section was later re-added reflecting a
│                                        drifted baseline (powersave, not performance).
│                                        Aidan added a `getconf PIPE_BUF /` block that still
│                                        says "TODO: RUN THIS."; the hybrid-CPU paragraph is
│                                        duplicated and the 2nd copy still says `taskset`.
├── clockres-learnings.md             # writeup of clockres.c's timer findings (untracked)
├── CLAUDE.md                         # this file (untracked)
├── report_suggestions.txt            # Aidan's idea list: posix_memalign, recompiling PIPE_BUF,
│                                        one-shot vs burst vs stream, perf_event API, SIGUSR2
│                                        snapshots, some PMCs only on real hardware
├── perf_events.txt                   # ~650KB `sudo perf list --details` (make get_perf_list)
├── results/
│   ├── perf_list_op.txt              # ~500KB `sudo perf list` dump (Arjun, d07d633)
│   └── latency.csv                   # 20001 lines; direct `./out/latency` run, NOT via runner
│                                        (see "Smoke-test results"); untracked
├── .idea/                            # JetBrains config, tracked by accident (deployment.xml
│                                        lists remote hosts: CloudLab, Lambda, CAE Euler/Tux)
├── Makefile                          # builds src/*.c -> out/*, plus *.run targets;
│                                        -g in CFLAGS; setcap after each link; get_perf_list
├── scripts/
│   ├── pin_freq.sh                   # sudo; locks one CPU's governor+min/max freq, saves
│   │                                    prior state to scripts/.freq_state/cpu<N>.env
│   ├── unpin_freq.sh                 # sudo; restores from that saved state file
│   ├── clockres-runner.sh            # pins CPU 0 to 4000MHz, runs clockres, unpins
│   ├── round-trip-pipes-runner.sh    # pins CPU 0+1 to 4000MHz, runs round-trip-pipes, unpins
│   ├── latency-runner.sh             # plain pin CPU 0+1 -> run latency -> unpin again (restored
│   │                                    from ddddd8a after the split; same pattern as the others)
│   ├── trace-lib.sh                  # SOURCED by *-trace.sh: root checks, per-run dir, state save/
│   │                                    restore (ftrace, perf sysctls, CPU freq), the do_perf_stat/
│   │                                    do_perf_trace/do_perf_cycles/do_strace/do_ftrace helpers
│   ├── trace_summary.py              # turns a results/trace/<name>/<ts>/ dir into summary.txt tables
│   ├── latency-trace.sh              # Aidan's perf/strace/ftrace harness, split out of the old
│   │                                    latency-runner.sh; all known bugs fixed 2026-09-21 but
│   │                                    UNTESTED with sudo (see "Known issues" #2); output goes
│   │                                    to results/trace/<timestamp>/; needs trace-cmd (installed)
│   └── throughput-runner.sh          # same pattern, for throughput (status msgs -> stderr)
└── src/
    ├── common.h                     # shared declarations only (SIZES/NUM_SIZES, PIPE_LENGTH,
    │                                   pin_to_cpu, set_pipe_length, timespec_to_ns,
    │                                   calibrate_tsc_hz, full_write/full_read)
    ├── common.c                     # implementations; #define _GNU_SOURCE first line (needed
    │                                   for sched.h's CPU_ZERO/CPU_SET); CALIBRATION_TRIALS=10
    │                                   is private to this file, not exposed in the header
    ├── clockres.c                   # timer resolution + accuracy tool (see clockres-learnings.md)
    ├── round-trip-pipes.c           # fork+2 pipes correctness check (echo, memcmp every
    │                                   iteration, no timing) - kept as-is, NOT renamed despite
    │                                   being superseded by latency.c for the real measurement
    ├── latency.c                    # THE latency benchmark: rdtsc-timed round trip, per size,
    │                                   `warmup` untimed rounds (also does memcmp correctness check
    │                                   here, NOT in the timed phase) + `iterations` timed rounds,
    │                                   both RUNTIME ARGS since 2026-09-21: `latency [warmup
    │                                   iterations]`, defaults DEFAULT_WARMUP=20 /
    │                                   DEFAULT_ITERATIONS=2000 when no args (bad/odd args ->
    │                                   message + exit 1; warmup>=0, iterations>=1). latency-
    │                                   runner.sh passes 20 2000 explicitly; latency-trace.sh
    │                                   passes 5 10 (a full-size traced run wrote ~40 MB/s of
    │                                   perf.data). Raw CSV to stdout (size_bytes,iteration,
    │                                   rtt_ns). Needs a rebuild (`make`) to take effect.
    └── throughput.c                 # THE throughput benchmark: one-way bulk transfer + single
                                        1-byte ack (NOT a round trip - different protocol from
                                        latency, per the assignment), TARGET_BYTES=64MB scaled
                                        per size (clamped [64, 2_000_000] iterations), REPEATS=3,
                                        CSV to stdout (size_bytes,total_bytes,repeat,elapsed_ns,
                                        mb_per_sec)
```

All four binaries (`clockres`, `round-trip-pipes`, `latency`, `throughput`) build clean with
`-Wall -Wextra -std=gnu11 -g`, no warnings, and have been smoke-tested (unpinned, i.e. without
`sudo make X.run`) with plausible output - see "Smoke-test results" below. All four currently
carry `cap_sys_nice=ep` (verified with `getcap out/*`). **Not yet done: an actual frequency-
pinned real data-collection run of latency or throughput** - `latency-runner.sh` was restored
to the plain version (2026-09-21) but has not been run since; throughput's runner is untouched.

### Key design decisions made this pass (with rationale, in case they need re-justifying)

- **CPU pinning is done via `sched_setaffinity()` inside the C programs themselves**, not via
  external `taskset`, for round-trip-pipes/latency/throughput. Reason: these spawn a child via
  `fork()`, which inherits the parent's affinity mask - external `taskset` on the whole process
  tree can't give parent and child *different* cores. `clockres` (single process, no fork) was
  later switched to self-pin too, for consistency, even though external `taskset` would have
  sufficed for it alone.
- **Parent pinned to CPU 0, child to CPU 1** for round-trip-pipes/latency/throughput - both are
  P-cores with identical 400-4800MHz ranges (see `lap-specs.md`), deliberately avoiding a
  P-core/E-core mismatch as an unstated confound in the baseline measurement. Asymmetric
  core-frequency experiments were discussed as a legitimate *separate* follow-up, not part of
  the baseline sweep.
- **Frequency pinning is external** (`pin_freq.sh`/`unpin_freq.sh`, root-only, sysfs-based),
  orchestrated by per-binary `<name>-runner.sh` scripts. Both CPU 0 and CPU 1 get pinned before
  latency/throughput/round-trip-pipes run, since the *child's* execution speed is a real
  component of round-trip time, not just parent-side overhead - DVFS on either core would leak
  into the measured latency as noise unrelated to the IPC mechanism itself.
- **`rdtsc` timing lives only in the parent process.** This sidesteps the "TSC not comparable
  across cores" problem entirely, per the assignment's own stated reasoning: round-trip timing
  only ever diffs two reads made by the same process, so it doesn't matter which core the child
  runs on.
- **TSC calibration (`calibrate_tsc_hz()` in common.c)**: brackets each `rdtsc` read between two
  `clock_gettime` reads and uses the midpoint as that read's effective timestamp, then takes the
  **minimum** Hz across `CALIBRATION_TRIALS=10` trials. The bracketing matters: without it, the
  read ordering (clock_gettime-then-rdtsc at the start, rdtsc-then-clock_gettime at the end)
  means any scheduling delay can only inflate the measured wall-clock window relative to the
  true cycle-counted window, biasing computed Hz **downward** on every trial - so *min* would
  pick the worst trial, not the best. Bracketing makes the residual error roughly symmetric,
  which is what makes taking the min meaningful again (mirrors the assignment's own "min over
  mean" guidance, which is specifically about *elapsed-time* measurements like latency/
  throughput, not explicitly restated for the clock-precision section's timer comparisons -
  re-verified this distinction directly against the PDF text at one point).
- **`memcmp` correctness verification happens only during each size's WARMUP iterations**, never
  during the measured/timed iterations, in both `round-trip-pipes.c` (does it every iteration,
  since it has no timed phase at all) and `latency.c` (only in the warmup rounds).
- **`common.h`/`common.c` split** (not a header-only `static inline` file): declarations only in
  the header (plain types, no `<sched.h>`/`<x86intrin.h>` leaking through), implementations in
  `common.c`. This means files that only need e.g. `pin_to_cpu()` don't need `_GNU_SOURCE`
  themselves anymore (`common.c` owns that requirement) - `clockres.c` and `round-trip-pipes.c`
  both had their own `#define _GNU_SOURCE` removed as a result. One gotcha hit from this: files
  that call `__rdtsc()` *directly* (not through `calibrate_tsc_hz()`) still need their own
  `#include <x86intrin.h>`, since that's no longer pulled in transitively via the header
  (`clockres.c` needed this added back after the split).
- **Function definition style**: opening brace on its own line for every function *definition*
  (not control-flow blocks like `if`/`for`/`while`, which keep K&R brace placement). Parameter
  lists stay on one line if short; if the full one-line signature would exceed ~100 chars, break
  one parameter per line, aligned to the opening paren column. Applied consistently across all
  of `clockres.c`, `common.c`, `round-trip-pipes.c`, `latency.c`, `throughput.c`.
- **Makefile `*.run` targets**: one generic pattern rule `%.run: out/% ; ./scripts/$*-runner.sh`
  instead of one explicit rule per binary. Gotcha hit and fixed: explicitly listing a
  pattern-matched target name in `.PHONY` breaks GNU Make's ability to find the pattern rule for
  it at all (reproduced in isolation to confirm) - fixed by simply not marking these targets
  phony, which is safe since they never correspond to real files anyway.
- **`SCHED_FIFO` (priority 99) is set in `pin_to_cpu()`** (Aidan's change), so it applies to all
  four programs, parent and child. Unprivileged use is enabled via **file capabilities, not
  RLIMIT_RTPRIO**: the Makefile's `out/%` rule runs `sudo setcap cap_sys_nice+ep $@` right after
  linking (the capability is wiped by every relink, hence in the link rule). `ulimit -r` is 0 on
  this machine, so without it `sched_setscheduler` gets EPERM. Alternative considered and not
  chosen: `/etc/security/limits.d` rtprio (needs re-login; unsure it survives the runners'
  `sudo -u "$SUDO_USER"` hop).
- **`sched_setscheduler` failure is a `Continue without SCHED_FIFO? [y/N]` prompt**, not a hard
  exit. Prompt goes to stderr (stdout carries the CSV); `n`/Enter/EOF/non-tty stdin -> exit 1. A
  static `rt_declined` flag in common.c survives `fork()` so the child does not ask again
  (tested with a scratchpad build of round-trip-pipes: n, y, and </dev/null all behave).
  Untested: the setcap path itself was applied by the user, and the capability under `strace`
  (an unprivileged tracer generally does not get file caps honoured - expect the prompt there).
- **`set_pipe_length()`** (`F_SETPIPE_SZ` to `PIPE_LENGTH`=65536, non-fatal on failure) is called
  on both pipes in round-trip-pipes/latency/throughput before `fork()`. 65536 is already the
  default, so it is currently a no-op; it is the hook for a pipe-size experiment.

### Teammate changes (Aidan Liaw, commits 4516585, 6690749, 5b0cd08)

C benchmark logic (timing, protocol, sizes, iteration counts) is unchanged. He added: SCHED_FIFO
in `pin_to_cpu()`; `set_pipe_length()`; Makefile `-g` + `get_perf_list`; the tracing rewrite of
`scripts/latency-runner.sh` (TRACE_CPU=2 also freq-pinned; huge `perf record` event list built
from `perf list --raw-dump`, a very verbose `strace`, an unused `$FTRACE` trace-cmd string, and an
ftrace block that only sets/clears `set_event` without enabling tracing or running the binary);
`report_suggestions.txt`; `perf_events.txt`; `.idea/`. Note `results/perf_list_op.txt` is
Arjun's, not his.

### Known issues (as of 2026-09-21)

1. **`latency-runner.sh` was split (2026-09-21)**: the plain runner is restored (BIN is a plain
   path, no perf/strace/ftrace, CPU 0+1 only) so `sudo make latency.run > results/latency.csv`
   should work again - syntax-checked only, not yet run. Aidan's tracing code now lives,
   deliberately unfixed, in `scripts/latency-trace.sh`.
2. **`scripts/latency-trace.sh` bug fixes (all applied 2026-09-21, ALL UNTESTED end to end -
   Claude could not use sudo in-session; only `bash -n`, the non-root error path, the event-list
   logic and trace-cmd/strace option *parsing* as a normal user were checked).** Aidan's original
   is `git show 5b0cd08:scripts/latency-runner.sh`. Fixed, in the order they were handled:
   a. `BIN` was a command string (`nice -n -20 sudo -u ... out/latency`): broke `[ ! -x ]` and
      would have been passed to perf/strace as one quoted arg. Now `BIN="$ROOT_DIR/out/latency"`.
   b. `perf record` ran as `$SUDO_USER` but `perf_event_paranoid`=2 and `/sys/kernel/tracing` is
      root-only. Now perf runs as root and the benchmark is dropped to the user via
      `eval "$PERF sudo -u \"$SUDO_USER\" \"$BIN\""`.
   c. The ftrace step never ran anything (only set/cleared `set_event`; `$FTRACE` was unused).
      Now `eval "$FTRACE \"$BIN\""` runs it. (First attempt was a hand-rolled tracefs
      `tracing_on` capture; user asked to use Aidan's `trace-cmd` string instead - reverted.)
   d. The root check came after `sudo perf list` calls (died with "sudo: a password is
      required"). `id -u` + `: "${SUDO_USER:?...}"` + the `-x $BIN` check now come first.
   e. Output location: everything goes to `results/trace/<YYYYmmdd-HHMMSS>/` (`perf.data*`,
      `strace_latency.<pid>`, `trace.dat`), one dir per run so runs never mix; `cleanup()` chowns
      it back to `$SUDO_USER`; `results/trace/` added to `.gitignore`.
   f. Cleanup: `cleanup()` (EXIT trap) now does `set +e` + `|| true` per step so one failure
      can't skip the rest; saves `current_tracer`/`tracing_on`/`set_event` at start and restores
      them; unpins CPUs 0/1/2; chowns the run dir. Limits: the ftrace *buffer contents* can't be
      restored (trace-cmd resets buffers); SIGKILL/power loss still leaves CPUs pinned at 4 GHz
      (recover with `sudo ./scripts/unpin_freq.sh <cpu>`); output dirs are never deleted.
   g. `trace-cmd` was not installed - user installed it (v3.3.1). Script now pre-checks
      `perf`/`strace`/`trace-cmd` with a `dnf install trace-cmd` hint.
   h. `$FTRACE` ran as the user (tracefs is root-only; confirmed `Permission denied opening
      /sys/kernel/tracing/tracing_on`). Now runs as root with `--user "$SUDO_USER"` dropping only
      the benchmark.
   i. **`-K` is INVALID in trace-cmd 3.3.1** (`record: invalid option -- 'K'`, confirmed) so
      Aidan's string would have aborted at once. The man page says `-K` = "don't reset events
      before recording", i.e. it was what made his pre-set `set_event` block work; without it
      trace-cmd wipes those events. So his 16-line `set_event` block was removed and its 15
      event systems (sched, syscalls, irq, workqueue, kmem, context_tracking, damon, ftrace,
      io_uring, lock, osnoise, percpu, power, task, vmscan) are passed as `-e` options instead;
      also added `-i` (don't fail on a missing event) and `-o $RUN_DIR/trace.dat`. `-a`,
      `--date`, `--proc-map`, `-T`, `-r 99` are accepted (parse-checked). NOTE: an earlier
      claim in this file that the block was "redundant" was wrong for the man-page semantics of
      `-K`, but is effectively right for the installed version, which rejects `-K`.
   j. (SUPERSEDED by the stat/record split in (l) - the merged `ALL_EVENTS` list no longer exists;
      history kept for the failures it hit.) perf event list: the seven `perf list --raw-dump` groups + `mem-loads`/`mem-stores` are
      merged into ONE list (`ALL_EVENTS`) with aliases (`cs`, `faults`, `migrations`) and tool
      events (`duration_time`, `num_*`, `user_time`, ...) dropped and duplicates removed.
      FIRST REAL RUN (2026-09-21 11:00, user ran `sudo ./scripts/latency-trace.sh`) FOUND A BUG
      IN THIS: `perf record` died with `Bad event name 'Fed'`. `perf list --raw-dump <group>`
      also returns **metrics** (`tma_*`) and **metric groups** (`Default`, `Fed`, ...), which only
      `perf stat -M` accepts - 124 of the 510 names. Fixed: `NOT_EVENTS` = `perf list --raw-dump
      metrics` + `metricgroups` is subtracted (`grep -vxFf`), leaving 386 real events. Verified
      as a normal user with `perf stat -e <all 386> -- true`: 0 syntax errors, all parse and
      open (output also shows multiplexing, e.g. `(12.50%)`/`(25.00%)` counted-time).
      Not fixable: 386 events on a handful of PMU counters means perf **multiplexes**; counts are
      estimates. The explicit tracepoint/other `-e` options (`syscalls:sys_*_pipe`, `intel_pt//`,
      `msr:rdpmc`, the `{cycles,msr/...}:S` group, ...) are still unverified - they need root.
      The run got no further than perf; strace and trace-cmd have still never run.
      SECOND REAL RUN (11:03): the 386-event list now passes (perf only warns "events were
      regrouped to match PMUs"), then `perf record` died on `-e ftrace:*`: "unknown tracepoint ...
      /sys/kernel/tracing//events/ftrace/timerlat not found" (perf lists `ftrace:timerlat` but it
      doesn't exist on this kernel). Fixed by REMOVING `-e ftrace:*` from `$PERF` (also useless
      there: they're the output records of ftrace tracers). trace-cmd's `-e ftrace` is left in
      (`-i` should make a missing event non-fatal) - unverified, may need the same removal. The
      other wildcard groups in `$PERF` (`sched:*`, `kmem:*`, `lock:*`, `damon:*`, ...) all list
      real entries in `results/perf_list_op.txt`, but still unverified until perf gets past them.
      Also seen: the fingerprint prompt makes `sudo` flaky in the `!` prompt ("Failed to match
      fingerprint") - run `sudo -v` first.
      Positive result of that run: pinning CPUs 0/1/2 worked, and `cleanup()` correctly restored
      all three (governor `powersave`, 400000-4800000/4700000 kHz, `.freq_state` emptied) after
      the failure, so the EXIT-trap cleanup works on the failure path.
   k. strace: now runs as root with the benchmark dropped to the user (`eval "$STRACE sudo -u
      ... "`), because an unprivileged tracer doesn't get the `cap_sys_nice` file capability
      honoured, which would trigger the `SCHED_FIFO` `[y/N]` prompt and block on stdin.
      Side effect: `--follow-forks` also traces the sudo wrapper (extra `strace_latency.<pid>`).
   l. **perf split into `perf stat` + `perf record` (2026-09-21, user approved)**, replacing the
      ~386-event single `perf record`. Rationale: the assignment asks where time goes (user vs
      kernel, context switches, cross-core memory traffic); 386 events on a few PMU counters
      multiplexed at 12-25% counted time and produced ~40 MB/s of perf.data.
      - **perf stat** (5 runs, one counter group each, so no multiplexing; `-a -C 0-1 -x,`; output
        `$RUN_DIR/perf_stat_<i>.csv`; benchmark CSV sent to /dev/null): (0) context-switches,
        cpu-migrations, page-faults, task-clock, cycles, instructions, branch-misses; (1)
        cache-references, cache-misses, L1-dcache-load-misses, LLC-load-misses; (2)
        mem_load_l3_hit_retired.xsnp_hitm / .xsnp_fwd + mem_load_retired.l2_miss (data moving
        between core 0 and 1); (3) dTLB/iTLB-load-misses; (4) P-core top-down group
        {slots,topdown-retiring,-fe-bound,-be-bound,-bad-spec}. Each run covers ALL message sizes
        together (the binary can't be limited to one size yet) - a per-size breakdown would need a
        size argument on `latency`.
      - **perf record**: `-e cycles -g` (call graphs, for `perf report` by function) + tracepoints
        sched:{sched_switch,sched_waking,sched_wakeup,sched_migrate_task},
        syscalls:sys_{enter,exit}_{read,write} + sys_enter_pipe2, power:cpu_idle,
        ipi:ipi_send_cpu, lock:contention_{begin,end}, kmem:{mm_page_alloc,kmem_cache_alloc},
        irq:softirq_entry. Aidan's option flags are kept unchanged (incl. the heavy
        --phys-data/--sample-mem-info/--branch-any/--intr-regs/--user-regs/--raw-samples);
        dropping those is a suggested but NOT done follow-up. `-g` is frame-pointer based, and
        the benchmark is built -O2 without frame pointers, so user-space callers above the leaf
        function will be missing (kernel frames are fine).
      - Dropped: intel_pt//, damon, io_uring, vmscan, percpu, osnoise, task, context_tracking,
        msr:rdpmc, the aperf/mperf/pperf/smi group, the ~350 other PMU events.
      - Checked as a normal user: all 5 stat groups parse and count 100% on a P-pinned control;
        the loop/eval quoting/output files work; `perf record` option combination raises no
        conflict beyond permissions; all tracepoint names exist in `results/perf_list_op.txt`.
        (The `<not counted>` I saw in a user-level per-process test did NOT happen in the real
        system-wide root run - see the third real run below.)
      THIRD REAL RUN (11:41, `results/trace/20260921-114112/`): **perf stat worked** - all 5 groups
      counted 100% (the `cpu_atom` "requested CPU 0-1 not supported" warnings are harmless). First
      real numbers, whole run (all sizes + warmup + TSC calibration), system-wide CPUs 0-1:
      context-switches 3452, cpu-migrations 118, page-faults 1264, task-clock 2041 ms, cycles
      299.9M, instructions 312.4M (IPC ~1.04), branch-misses 1.19M; cache-refs 4.74M, cache-miss
      1.53M, L1d-load-miss 1.76M, LLC-load-miss 0 (suspicious - maybe never counted properly or
      all hits); xsnp_hitm 44,135, xsnp_fwd 9,252, l2_miss 208k; dTLB-miss 100k, iTLB-miss 64.6k;
      top-down slots 1.725G: retiring 10.4%, fe-bound 28.4%, be-bound 57.5%, bad-spec 3.3%.
      Treat as preliminary (they include warmup rounds, all sizes together, and any other work on
      those cores). **perf record FAILED** ("Failure to open any events"): (1) every tracepoint got
      `PMU Hardware or event type doesn't support branch stack sampling` = `--branch-any` applies
      to all events; (2) `cpu_core/cycles/` got `Invalid argument` = `--intr-regs` (reproduced as a
      user: `--intr-regs` alone makes cycles fail with EINVAL, and software events reject it
      too). FIXED by removing `--branch-any` and `--intr-regs` from `$PERF` (rest of Aidan's
      flags kept; each was accepted alone by cycles:u / cpu-clock:u as a normal user). Still
      untested as root: whether the remaining flags (--phys-data, --sample-mem-info, --data, ...)
      are accepted for tracepoints. The empty `perf.data` and the 5 stat CSVs from that run are
      still in `results/trace/20260921-114112/` (kept: the stat CSVs are real data).
      FOURTH REAL RUN (11:43-11:46): `perf record` now opened all its events, but the script
      HUNG there (perf.data growing ~1.8 MB/s, no `latency` process anywhere). ROOT CAUSE (found
      via `/proc/<pid>/cmdline`, which ended in a bare `--`, and reproduced with a stubbed-taskset
      test): the `$PERF` and `$STRACE` strings ended with a **newline before the closing quote**, so
      `eval "$PERF sudo -u ... $BIN ..."` split into TWO commands - `perf record ... --` with NO
      workload (records system-wide until killed) and, only after that, a separate untraced `sudo
      -u ... latency`. (`$FTRACE` ended in a `\` continuation so it was fine; `$STAT` is one line.)
      FIXED by ending both strings with ` \` (comment added above the perf flag list); the stub
      test now shows the workload inside each tool's own argv. This also RE-EXPLAINS the earlier
      "orphaned perf writing 40 MB/s": it was never orphaned by Ctrl-C - it never had a workload.
      Aidan's original strings had the same defect. Left-over data from these hung runs is in
      `results/trace/` (two dirs of 2.4 GB and 1.9 GB, one of 275 MB) - useless, not yet deleted.
      FIFTH RUN (11:49, `results/trace/20260921-114920/`) = FIRST FULL SUCCESS (user deleted the
      old dirs): ~15 s, CPUs restored, no leftover perf. Files: perf_stat_0..4.csv, perf.data.<ts>
      (11.8 MB), strace_latency.<pid> x5 (sudo x3, latency parent 1183781, child 1183784),
      trace.dat (13 MB). WHAT WE LEARNED / LIMITATIONS OF THE CAPTURES:
      - **Everything except strace is dominated by the ~1 s TSC calibration**, not the benchmark:
        perf.data spans 1.027 s but the benchmark threads only appear in the last ~11.4 ms
        (parent calibrates: strace shows 10x clock_nanosleep = 1001.7 ms). So the perf stat
        counters (system-wide CPUs 0-1, whole run) mostly measure idle + desktop noise (foot, sway,
        waybar, browser). Evidence: instructions were 312M in the 11:41 run and 69.7M in this one
        for the same workload; task-clock ~2046 ms = 2 CPUs x wall (idle counts). IPC 0.43, top-down
        6.1% retiring / 34.4% fe / 56.8% be / 3.0% bad-spec, LLC-load-misses 0 - NOT usable as
        benchmark numbers until counting is limited to the timed loop.
      - **perf record tracepoints are SAMPLED, not exhaustive**: `--freq=max` puts every event in
        frequency mode; perf.data shows THROTTLE x27 and only ~80 of the benchmark's 300 pipe
        writes recorded (strace proves 300). So wake-latency stats from it (median ~68 us, n=199,
        all wakeups cross-CPU, 0 migrations, ~104/115 IPIs to CPUs 0/1, CPUs 0/1 entering
        C-state idx 1 ~35x each) are indicative only and inflated by tracing overhead (the real
        small-message RTT is ~2.3 us). 69% of perf.data records are MMAP2 from `--synth=all`.
      - **strace is exact and useful**: parent 649 syscalls, 150 pipe writes (15/size = 5 warmup + 10
        timed, correct) and 300 pipe reads; child 150 writes / 300 reads. Reads per message =
        ceil(size/65536): 8 sizes x 15 x 1 + 256K 15x4 + 512K 15x8 = 300 (matches the first pass's
        finding). Writes are always 1 syscall (blocks until the 64 KB pipe drains). strace slows
        the run ~5x (child wall 59 ms vs ~11 ms).
      - **trace-cmd capture is mostly useless as configured**: 1.06M report lines, 73% from
        trace-cmd's own threads (comm `trace`, CPU 2), only ~28k (2.7%) from `latency`; no
        `pipe_read`/`pipe_write` frames found; many "CPU:2 [N EVENTS DROPPED]"; `-T` makes the
        stack unwinder itself (`unwind_next_frame`, 52k lines) a big part of the trace.
      REDESIGN DONE (2026-09-21, "I want the perf results to make sense rather than be noise") - the
      four proposals above were all implemented, plus more; ALL UNTESTED AS ROOT:
      - **`latency` got `-s SIZE` (one size) and `-n` (skip the ~1 s TSC calibration; CSV column becomes
        `rtt_cycles`)**: `latency [-s size] [-n] [warmup iterations]`. Tested (scratch build).
      - **Makefile: `-fno-omit-frame-pointer`** so `perf --call-graph fp` sees user callers. `make` will
        NOT rebuild common.o (full_read/full_write live there) for a flag change: run `make clean`
        first, then `make` (setcap runs per binary).
      - **Output folder is now `results/trace/<latency|throughput>/<timestamp>/`** (user asked for
        this so a throughput trace can sit next to it). The existing 114920 run was moved to
        `results/trace/latency/20260921-114920` (old file layout, not readable by trace_summary.py).
      - **Shared `scripts/trace-lib.sh`** + thin `latency-trace.sh`; a `throughput-trace.sh` is the
        user's stated NEXT wish (needs `-s`/`-n`-style args added to throughput.c first).
      - **perf stat**: per-process (not system-wide), one counter group per run, 5 groups x 10 sizes
        + a baseline per group (`-n -s 4 0 1`, startup cost) = 55 runs; STAT_WARMUP=10,
        STAT_ITERATIONS=1000; trace_summary subtracts the baseline and divides by rounds -> per
        round trip per size. Verified per-process counting works on the new binary (cpu_core counted
        100%; cpu_atom "not counted" is normal). NB `context-switches:u` is 0 as a user - as root it
        counts.
      - **perf record split in two**: (a) tracepoints, every occurrence (`-c 1`, `--cpu 0,1`,
        `-m 8192`, `--synth=task`, no `--freq`), with `kernel.perf_event_max_sample_rate=100000000`
        and `perf_cpu_time_max_percent=0` set for the duration and restored by cleanup (the default
        15000 was what THROTTLED the first capture); `check_perf_data` warns loudly if THROTTLE/LOST
        appears; (b) `cycles -F 9999 --call-graph fp`, per-process, 20 warmup + 2000 iterations. Aidan's
        heavy flags (--phys-data, --sample-mem-info, --data*, --raw-samples, --user-regs,
        --timestamp-filename) were DROPPED, plus --branch-any/--intr-regs earlier.
      - **Workload is dropped to the user with `setpriv --reuid/--regid/--init-groups`** (exec's in
        place) instead of `sudo -u`: no PAM/sudo process in perf/strace output (sudo used to make
        3 of 5 strace files and add ms of startup that would dominate a 10 ms run).
      - **Commands are bash ARRAYS, not eval'ed strings** (root cause of the earlier hang class).
      - **strace** unchanged flags (Aidan's) but with `-n 5 10`; **trace-cmd**: `-F -c` (only the
        benchmark), `-g ksys_read -g ksys_write` (kernel side of the pipe I/O), `-T` dropped.
      - **trace_summary.py** (runs automatically, writes summary.txt): 1 counters per RTT per size
        (+top-down %), 2 tracepoints per size (ctx-sw, wakeups, wake->run median, cross-CPU wake %,
        reads/writes, IPIs, idle entries, page allocs, lock contention, read/write syscall times;
        size windows come from the benchmark's pipe writes; completeness line), 3 cycles by dso and
        by function, 4 strace reads/writes per RTT per size (reproduced ceil(size/64K) exactly on the
        real data), 5 ftrace function times. Parsers were tested on the real 114920 data; the counter
        arithmetic with fake stat files of known values.
      - **Whole flow tested as a FAKE root with stub perf/strace/trace-cmd/taskset/nice/setpriv/id/
        sysctl** (they log argv and run the real benchmark; TRACEFS/PIN_FREQ/UNPIN_FREQ env vars in
        trace-lib.sh exist for this): exit 0, 55 stat + 2 record + strace + trace-cmd calls with the
        right workload args, sysctls lowered and restored, CPUs pinned/unpinned, fake tracefs
        restored, summary produced. NOT verified as root: that perf accepts `-c 1` on these
        tracepoints + `--sample-cpu --synth=task -m 8192`, that the sysctl values are accepted, that
        trace-cmd takes `-F -c -g ... --user` together and `-n` after the command, that the comm of
        the benchmark is `latency` in perf script (BENCH_COMM env overrides), and how much data the
        cycles run produces. Expect the first real run to need another round of fixes.
      FIRST REAL RUN OF THE REDESIGN (12:11, `results/trace/latency/20260921-121127/`) - WORKED, 14 s,
      no throttled/lost samples, CPUs restored. Problems it exposed, all FIXED afterwards:
      (1) `sysctl: setting key perf_event_max_sample_rate: Invalid argument` in cleanup: the kernel
      refuses to change the rate while perf_cpu_time_max_percent is 0/100, and cleanup restored in
      the wrong order, so the rate stayed at 100000000, the kernel derived a 2 ns interrupt budget
      and auto-lowered the rate (dmesg: "took too long (2891 > 2), lowering ... to 69000", later
      19000; original was 15000). Now `perf_limits_raise/restore` wrap ONLY the tracepoint
      recording, restore percent THEN rate. The system's rate is still 19000 (harmless; restore
      with `sudo sysctl -w kernel.perf_event_max_sample_rate=15000`). (2) `perf script` prints
      whole-microsecond timestamps by default -> added `--ns`. (3) ftrace parser dropped every call
      >10 us because function_graph puts a marker (+ ! # @ * $) before long durations; fixed
      (ksys_read 601 / ksys_write 301 now match the raw report). trace_summary's kernel-symbol
      resolution needs ROOT (as a normal user cycles show raw addresses) - the script's own run
      of it is root, so that is fine; when re-running by hand use sudo.
      KEY NUMBERS (per round trip, both tasks): ctx-sw 2 for sizes <=64K, 14 @256K, 30 @512K
      (= 2*(2n-1), n = size/64K; the tracepoint counts agree exactly); migrations 0; small
      messages: 7.1k cycles, 13.1k instructions, IPC ~1.9, 2 wakeups + 2 IPIs + 2 idle exits, 100%
      of wakeups cross-CPU, wake->run ~2 us under tracing, 27 xsnp_hitm loads (cross-core cache
      line transfers); large messages: IPC ~0.77, back-end bound 63-66%, 823k cycles @512K; cycles
      profile (all sizes weighted, so 512K dominates): 99% kernel, _copy_to_iter 52.6%,
      _copy_from_iter 10.3%, anon_pipe_put_page 4.7%, page zeroing/memcg charge small; strace:
      1 write and ceil(size/64K) reads per message per side; ftrace self time: schedule 23%
      (blocked), mutex_spin_on_owner 22% (probably induced by function_graph's slowdown - the
      perf tracepoints saw 0 lock contention), then page alloc/free + memcg charging ~1-3% each.
      (That limitation - one weighted cycle profile - was addressed next, see below.)
      ROUND 2 (2026-09-21, "finish the latency one first"; NOTHING COMMITTED, per the user), all
      offline-tested only, to be verified by the user's next runs:
      - **`latency -q`** (quiet): no per-iteration printf. Reason: with the CSV printed inside the
        loop, printf formatting was being counted in perf stat / cycles as if it were pipe cost
        (~1-2k of the ~13k instructions per small round trip). All traced runs now use `-n -q`
        (quiet prints one "size N: min rtt C cycles" line per size to stderr - a sanity check).
      - **perf stat**: STAT_ITERATIONS 1000 -> 3000 and 3 baselines per group (summary takes the
        MEDIAN) to cut the `n/a` cells that appeared when a small counter fell below one noisy
        baseline. 65 stat runs now.
      - **Cycle profiles per size** (`perf_cycles_<4|65536|524288>.data`; 200000 / 50000 / 5000
        iterations, ~0.5-1 s of work each) instead of one mixed profile dominated by 512 KB.
      - **trace_summary section 3 rewritten**: heuristic NAME-based categories (copying, pipe logic,
        page alloc/free + memcg, locking, scheduler/wakeup/idle, interrupts/IPIs, syscall entry/VFS/
        security, other kernel, user, unresolved) x cycles-per-RTT from perf stat = a "contribution of
        each component" table (3a), CPU work vs the measured untraced RTT incl. a "not on a CPU"
        idle/wakeup gap (3b; goes negative for >64 KB because both cores work in parallel - overlap,
        not an error), top functions per size (3c), and a stacked-bar chart `breakdown.png/.pdf` in the
        run dir (for the paper). Categories were tested on real symbol names and the table on canned
        `perf report` output with known numbers; the chart was viewed.
      - **Convention: clean runner output goes to `results/latency_run<N>.csv`.** trace_summary reads
        ONLY `results/latency_run*.csv` (not the old unpinned `results/latency.csv`).
      - **`scripts/analyze_latency.py`** (new): `python3 scripts/analyze_latency.py
        results/latency_run*.csv` -> table of min RTT, ONE-WAY = min/2 (the assignment says use the
        minimum), median, p99, max, run-to-run spread of the minima, and `figures/latency.png/.pdf`
        (log-log, labelled axes, 64 KB pipe-buffer line, min + median). Refuses `rtt_cycles` (traced)
        files. Tested on the 10:23 smoke-test CSV.
      - strace parent detection now = the process that wait4()s (the parent no longer writes a CSV
        header under -q). lap-specs.md `getconf PIPE_BUF /` TODO filled in (4096; noted it is the
        atomic-write size, not the capacity).
      - Stub-flow re-test (fake root): 65 stat + 4 perf record + strace + trace-cmd, all with the right
        `-n -q` args, exit 0, summary + chart produced, empty profiles degrade gracefully.
      HOW TO RUN NEXT (user does this): `make` (latency.c changed; needs sudo for setcap), then
      `sudo -v`, then `for i in 1 2 3; do sudo make latency.run > results/latency_run$i.csv; done`,
      then `sudo ./scripts/latency-trace.sh 2>&1 | tee results/trace-run.log`, then
      `python3 scripts/analyze_latency.py results/latency_run*.csv`.
      STILL PENDING for latency: those runs; paper text/figures; commit (user said not yet); optional
      `sudo sysctl -w kernel.perf_event_max_sample_rate=15000` (currently 19000, harmless); the
      duplicated hybrid-CPU paragraph in lap-specs.md (left alone, it is the user's doc).
      THEN: throughput (needs `-s`/`-n`/`-q` on throughput.c + a throughput-trace.sh on trace-lib.sh,
      output to results/trace/throughput/<ts>/).

      ROUND 2 RESULTS (user ran the runner x3 + the tracer, 12:28-12:30; nothing committed):
      - **BUG FOUND: `analyze_latency.py` "did not work"** because each `results/latency_run<N>.csv`
        began with the line `./scripts/latency-runner.sh`: `make` echoes the recipe to STDOUT, which
        the runners use for the CSV. FIXED: Makefile `%.run` recipe is now `@./scripts/$*-runner.sh`,
        and that one junk first line was `sed -i 1d`'d from the three existing CSVs (data rows were
        intact, 20000 each). The same junk line made trace_summary skip them, so the first summary had
        no "untraced RTT" rows.
      - **CLEAN LATENCY DATA (pinned 4 GHz, 3 runs x 2000 iters/size)**: min RTT us / ONE-WAY us:
        4B 1.95/0.98, 16B 1.70*/0.85, 64B 1.99/0.99, 256B 2.13/1.07, 1K 2.39/1.20, 4K 4.22*/2.11,
        16K 8.90/4.45, 64K 25.01/12.51, 256K 112.59/56.30, 512K 225.63/112.81. Medians ~3.1 us small.
        Run-to-run spread of the minimum: 0.5-3% for >=16K but up to 26% (16B), 8% (4K), 7% (64B).
        `*` = minimum is >15% below the 1st percentile: it is a handful of lucky iterations (16B: 1 of
        2000 iterations within 5% of it); p1 is a steadier ~2.05-2.1 us for all small sizes. The assignment
        says report the minimum, so it stays the headline number, but the paper should say so.
        analyze_latency.py now also prints p1 + the `*` flag and plots the 1st percentile.
      - **Trace run 20260921-122902**: clean, no warnings, 26 MB, sysctls not touched by cleanup any
        more (rate still the kernel-lowered 19000). Counters per RTT: ctx-sw 2 (<=64K), 14 (256K), 30
        (512K); migrations 0; kcycles 8.8/8.4/9.8/13.2/29.7/96.4/405/817 (4B/64B/1K/4K/16K/64K/256K/512K),
        IPC 1.4 small -> 0.76 large; back-end bound 41-45% small, 61-66% large; xsnp_hitm ~27 per RTT
        constant for small messages. Cycle breakdown (name-based categories): 4 B = scheduler/wakeup 19%,
        locking 17% (pipe mutex), syscall entry/VFS 12%, pipe logic 10.5%, copy 7%, interrupts 6%, user 5%,
        other kernel 23% (biggest: `cpupri_set` 7.8% = RT-scheduler bookkeeping, a cost of SCHED_FIFO;
        `native_write_msr` 3.9% = programming IPIs/timers) - i.e. death by a thousand cuts, NOT copying;
        64 KB and 512 KB = copying 65-67%, page alloc/free + memcg 10-12%, pipe logic 7%, locking 4-5%.
      - **Wall time vs CPU work (median untraced RTT minus CPU work at 4.0 GHz = time NEITHER task ran)**:
        4B 0.89 us (29%), 1K 0.87, **4K 2.19 (40%)**, 16K 2.10, 64K 2.07 (8%), 256K 16.8 (14%), 512K 34.8
        (15%). Per wakeup that is ~0.45-0.5 us for <=1 KB but ~1.05-1.2 us for >=4 KB. **The latency step at
        4 KB (3.3 -> 5.5 us median) is ~40% CPU work (+0.85 us of copying) and ~60% this extra idle/wakeup
        time (+1.3 us)**, with the same 2 wakeups/2 IPIs per RTT. UNEXPLAINED. Rejected hypothesis: a deeper
        C-state (perf shows C1 at every size incl. 4 KB). Untested candidates: something at exactly
        PAGE_SIZE in anon_pipe_write/read wakeup timing; would need sizes around 4096 (2K,3K,4095,4096,
        4097,8K) - `latency -s` currently only accepts the assignment's ten sizes.
      - trace_summary changes: 3b now compares against the MEDIAN untraced RTT (perf stat counts the average),
        shows min for reference; `cpupri`/`native_write_msr` etc. categorised; the largest symbols left in
        "other kernel" are listed per size; no root-owned __pycache__ any more (sys.dont_write_bytecode +
        PYTHONDONTWRITEBYTECODE). `scripts/__pycache__` is currently ROOT-OWNED from the last run: remove
        with `sudo rm -rf scripts/__pycache__` (now gitignored).
      - **RULE (user, 2026-09-21): summary.txt must contain ONLY what `trace_summary.py` prints. Never
        hand-edit or hand-splice it, and put any analysis worth quoting into the script.** I had violated
        this (spliced a 3b block into run 122902's summary.txt and mixed sections in run 121127's); both
        were undone: 121127's summary.txt was restored to the script's own original output, and 122902's
        was deleted. REGENERATE IT (needs root for kernel symbols; `tee` keeps the file user-owned):
        `sudo python3 -B scripts/trace_summary.py results/trace/latency/20260921-122902 | tee results/trace/latency/20260921-122902/summary.txt`
      - The two analyses I had computed ad hoc in chat are now script sections: **2c** (idle states entered
        on CPUs 0-1 per size + median stay) and **6** (per size: median untraced RTT vs CPU work, "both
        idle" time, wakeups/RTT, idle per wakeup - the table behind the 4 KB finding). Their output on the
        real run matched the ad hoc numbers.
      - The stubs/unit tests could not have caught the make-echo bug: it only appears when a real `make`
        recipe's stdout is redirected. Lesson: the runner CSVs should have their header checked.
      .gitignore now ignores only the LARGE raw trace files (`results/trace/*/*/perf_*.data`, `trace.dat`,
      `strace_*`), so `summary.txt`, `params.txt`, `breakdown.png/pdf` and the small stat CSVs of a run can be
      committed (verified with `git check-ignore`). The user re-runs the whole latency experiment once more
      (5 runner runs + the tracer) and then wants it committed - NOT committed yet.
      PENDING: paper; commit (deferred by user); throughput (needs `-s`/`-n`/`-q` on throughput.c + a
      throughput-trace.sh on trace-lib.sh, output to results/trace/throughput/<ts>/); explain the 4 KB
      step; consider a size argument beyond the ten sizes for probing it.
   Left as-is on purpose: CPU 2 is still freq-pinned (the tracers are `taskset` onto it, so a
   stable frequency is reasonable - not a bug); the dead `else "$BIN"` branch; benchmark CSV
   still goes to the terminal in each traced run. Open risks: function_graph over 20k round trips
   may make a very large `trace.dat` (trace-cmd streams to disk, so ring-buffer overflow is less
   likely than first suspected); everything above is unverified until run with sudo.
   CORRECTION kept from earlier: `intel_pt//`, `damon:*`, `ftrace:*` etc. are NOT unsupported -
   they appear in `results/perf_list_op.txt` (full `sudo perf list`); `perf_events.txt`
   (`--details`) is missing several, so don't use it for availability checks.
3. `lap-specs.md` TODO/duplicate-paragraph items noted in the directory listing above.
4. Stale-binary gotcha: `sudo make` would build `out/` as root; run plain `make` first, then
   `sudo make X.run`.

### Smoke-test results (unpinned runs, just sanity-checking correctness/plausibility)

- `latency`: 2000 iterations x 10 sizes, min RTT ~2.1us at 4B down to ~200us at 512KB, one-way
  ~1.0us floor for small messages growing sharply past the 64KB pipe buffer - consistent in
  shape with the very first (deleted) pass's numbers.
- `results/latency.csv` (2026-09-21, direct `./out/latency > results/latency.csv`, with
  SCHED_FIFO capability, CPU freq NOT pinned - governor is `powersave`, min 400 MHz, no
  `.freq_state` files): min RTT per size in ns: 4B 2286, 16B 2297, 64B 2338, 256B 2421, 1K 2676,
  4K 3239, 16K 7640, 64K 22012, 256K 96936, 512K 195992. 2000 rows per size (20 warmup rounds not
  printed). CSV columns: `size_bytes,iteration,rtt_ns`; rtt_ns is the raw per-iteration round
  trip (one-way = /2), no summary stats baked in. Treat as a smoke test, not final data.
- `throughput`: peaks ~5GB/s around 4KB chunks, drops to a ~3GB/s plateau past the 64KB buffer -
  also consistent in shape with the first pass, repeats agree closely with each other (stable).

### Not yet done / natural next steps when resuming

1. Run `make` followed by `sudo make latency.run > results/latency.csv` and
   `sudo make throughput.run > results/throughput.csv` for real, frequency-pinned data.
2. "Where the time goes" investigation (perf/strace/context-switch breakdown), per the
   assignment - Aidan's `scripts/latency-trace.sh` is the start of this. All known bugs were
   fixed 2026-09-21 (see "Known issues" #2) but it has never been run with sudo, so expect
   further failures on the first real run (`sudo ./scripts/latency-trace.sh`); nothing collected
   yet. It only covers latency - no throughput tracing script exists.
3. Plots/figures for the paper.
4. The paper itself (2-page, 2-column, 11pt, conference-style, per the assignment's explicit
   structure requirements).
5. Optional follow-ups from `report_suggestions.txt` (PIPE_BUF/pipe-size experiment via the
   existing `set_pipe_length` hook, posix_memalign, one-shot vs burst vs stream).
6. Minor open question, never resolved: `round-trip-pipes.c` was deliberately kept as a
   separate, unrenamed correctness-check tool (not merged into or replaced by `latency.c`) -
   fine as-is, but worth remembering it's intentionally redundant with `latency.c`'s WARMUP
   phase, not a leftover mistake.

### Wakeup timeline + ftrace flame graphs (2026-09-21, latency; user asked for options 1 and 2)

Why: the objective is to see the internal wakeups. A perf CYCLES flame graph cannot show them fully:
halted CPUs generate no cycles (the both-idle gap is 30-40% of a small round trip), and the sleeping CPU's
IPI/idle-exit work runs in the idle task, which the per-process cycles recording excludes. The tracepoint
capture (`perf_trace.data`, every occurrence, ns timestamps) does contain every step, so:

- **`scripts/wakeup_timeline.py <run_dir>`** -> `<run_dir>/wakeup/`: `wakeup_timeline.txt`,
  `wakeup_phases.csv` (one row per hand-off), `wakeup_phases.png/.pdf`. Splits every hand-off (one benchmark
  task waking the other) into phases: waking -> IPI sent -> CPU leaves idle -> wakeup handled -> context
  switch -> syscall returns. Read PROPORTIONS only (the capture slows the benchmark several times).
  On run 20260921-124321: 900 hand-offs, none skipped; waking->running ~2.0 us at EVERY size (0.2 waker side,
  0.73 IPI+idle exit, 0.63-0.7 wakeup handling, 0.37-0.43 switch), i.e. the wakeup path itself does not
  change with message size; only "switch -> syscall returns" grows (the copy). So the 4 KB latency step is
  NOT in the traced wake path (still unexplained; the traced timings are inflated and may hide it).
- **`scripts/ftrace_flamegraph.py <run_dir>`** -> `<run_dir>/flamegraphs/`: `ftrace_<all|4B|64KB|512KB>.svg`
  (interactive tooltips), `.pdf`, `_running.svg` (blocked/off-CPU `schedule` time removed), `.folded`
  (Brendan Gregg format), `ftrace_top_frames.txt`. Kernel call tree under read()/write(), weights =
  function_graph SELF time, split by message size using the sys_enter_write events in trace.dat,
  colours = the same component categories as the cycle breakdown chart. Cross-checked against the
  independent parser in trace_summary section 5 (schedule 26.86 ms / 24.1% in both). The 4 B graph shows the
  waker path (`anon_pipe_write > __wake_up_sync_key > __wake_up_common > autoremove_wake_function >
  default_wake_function > try_to_wake_up > ttwu_*`) and, on the read side, the blocked `schedule` box then
  `pick_next_task`/`dequeue`/`psi_*`; SELinux checks (`security_file_permission > selinux_*`) show on both.
  `mutex_spin_on_owner` does not appear at 4 B (only large sizes) - consistent with it being tracer-induced.
- Both are called at the end of `latency-trace.sh` (failure-tolerant: an empty capture prints a message and
  the run still exits 0; verified with the fake-root stub flow) and can be re-run by hand on any run dir
  (no root needed). New code lives in NEW files; they import trace_summary.py read-only.
- Not done / options left: wakeup-path flame graph from tracepoint call graphs (`sched_waking -g`; needs a
  re-record with sudo), system-wide cycles on CPUs 0-1 (idle-task/IPI cost), strace flame graph (shallow: only
  user-side call sites), per-size flame graphs beyond 4 B/64 KB/512 KB.
- Note: the user committed the latency results (`c297a86`) incl. raw capture files; `.gitignore` in HEAD is the
  short version, so the new `flamegraphs/*.folded` (~3.8 MB) are NOT ignored.


## THROUGHPUT SETUP (built 2026-09-21 by a background fork; NOTHING RUN AS ROOT, NOTHING COMMITTED)

Goal: the same treatment latency got - clean runs, a tracing harness, a summary, an analysis script + figure.
All of it was tested OFFLINE only (stub tools / synthetic inputs); see "Untested" below.

### Files
- `src/throughput.c` (edited): runtime options, default path unchanged. `throughput [-s size] [-n] [-q]
  [-b total_bytes | -i chunks] [-r repeats]`. No args = exactly the old behaviour (all sizes, 64 MB/repeat with
  chunks clamped to [64, 2,000,000], 3 repeats, header `size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec`).
  `-s` one size; `-n` no ~1 s TSC calibration (columns become `elapsed_cycles,bytes_per_cycle`, never mislabelled);
  `-q` no CSV rows, one stderr line per size; `-b` bytes per repeat per size, chunks = max(1, bytes/size), NO
  clamp; `-i` exact chunks per repeat (exclusive with `-b`); `-r` repeats. Verified against the original built
  from git HEAD: same 31 lines, same header, first three columns byte-identical on every row, columns 4-5 same format.
- `scripts/throughput-runner.sh` (comment/usage only): outputs go to `results/throughput_run<N>.csv`.
- `scripts/throughput-trace.sh` (new): like latency-trace.sh, on `trace-lib.sh`, TRACE_NAME=throughput ->
  `results/trace/throughput/<ts>/`. 5 steps: perf stat (5 groups x 10 sizes + 3 baselines/group = 65 runs),
  perf record tracepoints, perf record cycles per size (4 B, 4 KB, 64 KB, 512 KB), strace, ftrace; all with
  `-n -q -r 1`. `params.txt` records exactly how much each phase moved (`stat_chunks`, `cycles_chunks`,
  `trace_bytes`, `strace_bytes`, `ftrace_bytes`).
- `scripts/trace_summary_throughput.py` (new): imports `trace_summary` for its helpers (not modified), sets
  BENCH_COMM=throughput before import. Sections: 1 counters per chunk and per MiB (baseline median subtracted;
  top-down), 2 tracepoints per size (ctx-sw, wakeups, wake->run, cross-CPU %, child reads per chunk, IPIs, idle
  entries, page allocs/MiB, lock contention/MiB, read/write time, idle states, completeness), 3 cycles by
  component per size as cycles/KiB + `breakdown.png/pdf`, 4 strace (parent writes/chunk, child reads/chunk, avg
  bytes returned per read), 5 ftrace (reuses `trace_summary.section_ftrace`), 6 measured throughput
  (`results/throughput_run*.csv` ONLY) vs CPU work per chunk incl. "cores busy". Every section is wrapped so a
  failure cannot hide the others. RULE still applies: summary.txt = script output only, never hand-edited.
- `scripts/analyze_throughput.py` (new): `python3 scripts/analyze_throughput.py results/throughput_run*.csv` ->
  per size BEST (= max MiB/s = min elapsed; the assignment prefers extremal statistics), median, worst, run-to-run
  spread of the per-run bests, `*` when one run is >15% above the median of the per-run bests, and
  `figures/throughput.png/.pdf` (log-log, 64 KB pipe-buffer line). UNIT: the benchmark's `mb_per_sec` is
  bytes/2^20 per second = MiB/s; GB/s (10^9) is printed beside it. Refuses `elapsed_cycles` (traced) files and any
  file whose first line is not the CSV header (clear hint about make's echo), warns if the file's own mb_per_sec
  column disagrees with total_bytes/elapsed_ns.

### How throughput differs from latency (design)
- One-way stream + a single ack, so NO round trips: counters are per chunk (one write() + one read()) and per
  MiB; cycle breakdown is cycles/KiB; "cores busy" = CPU ns per chunk / wall ns per chunk (parent and child run
  concurrently as a pipeline, so this is 0-2, unlike latency's "both idle" time where the tasks alternate).
- Volumes are chosen per phase in BYTES so the 64 KB pipe fills and the writer blocks (steady state): perf stat
  256 MiB per size (chunks capped at 1,000,000 for tiny chunks), tracepoints 128 KiB per size in ONE run, cycles 1
  GiB per profile (capped), strace 8 KiB, ftrace 4 KiB per size (they are far heavier). Chunk counts for
  all-sizes runs follow `chunks = max(1, bytes // size)`, which the summary re-derives.
- Reuses trace-lib.sh unchanged (do_perf_stat, do_perf_trace, do_perf_cycles, do_strace, do_ftrace, sysctl
  handling, cleanup). Commands are bash arrays.

### Tested (offline)
- throughput.c: builds with the Makefile's flags, no warnings; default path vs the HEAD original; every option;
  11 bad-argument cases exit 1 with a message; `-b 8192` gives 8192 B at 4 B chunks but 1 chunk from 16 KB up.
- analyze_throughput.py: SYNTHETIC CSVs with hand-computed answers (best/median/worst/spread/`*`/GB/s), refusal of a
  traced file, a junk first line, a unit mismatch; exit code 1 on refusals; figure rendered and viewed.
- trace_summary_throughput.py: SYNTHETIC stat files (known cycles/chunk, three baselines with a known median),
  SYNTHETIC perf-script events (known counts per chunk, wakeups, cross-CPU %, latencies, idle states, IPIs, page
  allocs), SYNTHETIC strace files (known reads/chunk, bytes/read), SYNTHETIC throughput_run CSVs (also proves a
  stray `throughput.csv` is ignored), canned `perf report` text for the cycle breakdown; all matched the expected
  values. Chart viewed (labels abbreviated when a value is huge). Empty dir, missing params and garbage files
  degrade without crashing.
- throughput-trace.sh: `bash -n`; a whole FAKE-ROOT run with stub perf/strace/trace-cmd/taskset/nice/setpriv/id/
  sysctl in a scratch tree: exit 0, 65 stat + 5 perf record + 1 strace + 1 trace-cmd calls, every distinct
  benchmark argument set accepted by the real scratch binary, sysctls raised then restored (percent first, then
  rate), CPUs pinned/unpinned, fake tracefs restored, params.txt and summary produced, no __pycache__.

### Untested / unknown (needs the user's sudo run)
- Real perf/strace/trace-cmd output for throughput (formats are assumed identical to latency's); that the
  benchmark's comm is `throughput` in perf script (BENCH_COMM env overrides); real run time (guess: a couple of
  minutes, NOT measured); size of perf_trace.data (4 B chunks = 32768 chunks in one all-sizes run, maybe tens of
  MB; check_perf_data warns on lost samples) and of the strace files; whether the cycle runs are long enough
  (~0.3-0.5 s each is an estimate); kernel-symbol resolution needs root.
- Nothing about the actual throughput numbers is known: no throughput data was collected by this work.

### Commands for the user (mirror of the latency list)
```
cd /home/aanil/Desktop/courses/736-adv-os/projects/p2
make                                   # rebuilds out/throughput with the new options (sudo for setcap)
getcap out/throughput                  # expect: out/throughput cap_sys_nice=ep
sudo -v
for i in 1 2 3 4 5; do sudo make throughput.run > results/throughput_run$i.csv; done
head -1 results/throughput_run1.csv    # MUST print: size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec
wc -l results/throughput_run*.csv      # each should be 31
sudo ./scripts/throughput-trace.sh 2>&1 | tee results/throughput-trace-run.log
python3 -B scripts/analyze_throughput.py results/throughput_run*.csv
```
Run the runner x5 BEFORE the tracer (section 6 of the summary compares against those CSVs).

### Observations, no action taken
- The committed `.gitignore` contains only `out/*` and `__pycache__/`: raw captures (`perf_*.data`, `trace.dat`,
  `strace_*`) under `results/trace/` would be picked up by `git add`. Re-add ignore patterns if that is not wanted.
- While this work ran, `scripts/latency-trace.sh` gained calls to `wakeup_timeline.py` / `ftrace_flamegraph.py`
  (not from this work); throughput-trace.sh does not call them - they are latency-specific as far as this fork can tell.

- **BUG FOUND (2026-09-21): no `breakdown.png` was ever produced by a real tracer run.** matplotlib is installed only
  in the USER's site-packages (`~/.local/lib/python3.14/site-packages`); the tracer runs the summary as root, whose
  python cannot import it, and `chart_breakdown` skipped silently (same latent problem for the wakeup chart and the
  flame-graph PDFs). FIXED: `latency-trace.sh` now sets `PYTHONPATH` to the invoking user's site-packages (computed
  with `getent passwd` + `site.getusersitepackages()`, `MPLCONFIGDIR` in a temp dir removed afterwards), and the three
  chart functions now PRINT a warning when matplotlib is missing instead of skipping quietly. Verified: an
  interpreter without the user site prints the warning and skips only the chart; with `PYTHONPATH` it renders.
  To get the missing chart for an existing run (kernel symbols need root): `sudo env PYTHONPATH="$(python3 -c
  'import site;print(site.getusersitepackages())')" MPLCONFIGDIR=/tmp/mpl python3 -B scripts/trace_summary.py
  <run_dir> | tee <run_dir>/summary.txt`, then `sudo chown -R $USER <run_dir>`.

- (2026-09-21) The throughput tracer (`scripts/throughput-trace.sh`, written by the background agent before the
  matplotlib-as-root bug above was found) had the same silent chart skip. Applied the same fix there (PYTHONPATH to
  the invoking user's site-packages, temp MPLCONFIGDIR removed afterwards) and made `trace_summary_throughput.py`'s
  breakdown chart print a warning instead of skipping quietly. Re-tested with the fake-root stub flow: exit 0, no
  traceback, no leftover hidden dirs. Checked with `git diff HEAD` that the agent left every latency/shared file
  untouched (latency.c, common.*, trace-lib.sh, analyze_latency.py, pin/unpin, Makefile, .gitignore, lap-specs.md,
  latency-runner.sh).

### Throughput: wakeup timeline + flame graphs (2026-09-21, user: "flamegraph and table required for throughput too")

Finding: the first throughput run had NO `wakeup/` or `flamegraphs/` (`throughput-trace.sh` never called the scripts).
Run as-is on that data the scripts worked, but the wakeup table was thin: only 98 hand-offs (2 at 4 B, 2 at 1 KB, 2 at
4 KB, 17 at 512 KB) because streaming wakes a task about once per 64 KB moved and the general tracepoint capture moved
only 128 KiB per size. The ftrace side was fine (80,947 call trees, 1,390 reads / 1,389 writes; schedule 34.8%).
Done (all offline-tested only):
- `throughput-trace.sh` now calls `wakeup_timeline.py` and `ftrace_flamegraph.py` (failure-tolerant, matplotlib fix
  already in place), and has a NEW step **2b: dedicated wakeup captures** `perf_wakeup_<size>.data`: one recording per
  size, only sched_waking / sched_wakeup / sched_switch / ipi_send_cpu / cpu_idle (no per-syscall events), WAKE_BYTES=128
  MiB per size with chunks capped at 8M (4 B: 32 MB ~ 500 wakeups; 64 B+: ~2000). `do_perf_wakeup` lives in
  throughput-trace.sh (trace-lib.sh untouched). Params: wake_bytes, wake_max_chunks, wake_chunks.
- `wakeup_timeline.py`: prefers the per-size `perf_wakeup_*.data` when present (whole file = that size; no syscall
  events, so the "switch -> syscall returns" phase is dropped), else uses `perf_trace.data` as before; the process name
  now comes from `params.txt` (`name=`), no BENCH_COMM env needed; prints its source, warns for sizes with < 30
  hand-offs. `ftrace_flamegraph.py` also reads the name from params.
- Regression-checked against the real latency run: wakeup_phases.csv identical, flame-graph top-frames/.folded identical
  (only header/wording lines of wakeup_timeline.txt differ). Dedicated mode tested with real latency events minus the
  syscall events. Fake-root run of the whole throughput tracer: exit 0, 10 wakeup captures with the right args, empty
  stub captures make both new steps fail safe.
- The existing throughput results were deleted by the user at ~13:26, so there was nothing to regenerate; the next
  throughput-trace run produces `wakeup/` and `flamegraphs/` itself. Small chunk sizes will still be sparse by nature.

### Throughput: first full run (2026-09-21 13:32, `results/trace/throughput/20260921-133230/`, 5 runner runs) - nothing committed

Clean run: no warnings except `perf_cycles_524288.data` "INCOMPLETE" = ONE throttle event in 6061 samples (negligible).
All outputs present incl. `wakeup/` and `flamegraphs/` (the dedicated `perf_wakeup_<size>.data` worked first time).
- **Runner numbers (best of 15 rows, MiB/s; GB/s in brackets)**: 4B 21.0* (0.022), 16B 67.7, 64B 193.8, 256B 690.8,
  1K 1,981, 4K 4,500 (4.72), **16K 4,742 (4.97) = peak**, 64K 3,578, 256K 3,634, 512K 3,556 (3.73): a ~25% DROP past the
  64 KB pipe buffer (64K/256K/512K are within spread of each other). `*` = 4 B best is >15% above the median of the
  per-run bests (lucky run; spread 39%). 4 B / 16 B only move 7.6 / 30.5 MiB because of the ORIGINAL 2,000,000-chunk cap
  (MAX_ITERS), so they are short and noisy (spread 39% / 29.5% vs 6-13% elsewhere). Possible follow-up: raise that cap.
- **Why (script-generated tables)**: ctx-sw per chunk 0.000-0.06 below 64K (tasks overlap), then 2.0 @64K, 8.0 @256K,
  16.0 @512K. "Cores busy" (CPU ns/chunk over wall ns/chunk) ~1.93-2.04 for 64B-16K but 0.86-0.91 for >=64K: past the pipe
  size the writer must wait for the reader to drain, so the two stop overlapping. Cross-check: throughput ~ cores_busy x 4 GHz /
  cycles_per_KiB: 16K = 1.93 x 4e9 / 1,741 = ~4.5 GiB/s (measured 4.3-4.7); 64K = 0.86 x 4e9 / 991 = ~3.3 GiB/s (measured 3.4).
  cycles/KiB is LOWER at 64K (991) than at 16K (1,741): the data path gets cheaper per byte, the loss is parallelism.
  Cycle breakdown: 4 B = locking 43% (pipe mutex: mutex_lock/unlock/__mutex_lock) + syscall entry/VFS/security 30% + user 10% +
  copy 10%; 4 KB = locking 56% (mutex_spin_on_owner 32%: spinning on the pipe mutex) + copy 28%; 64K/512K = copy 58-62%
  + page alloc/free/memcg 12-15% + locking 12%/5%. Top-down: back-end bound 72-90% for chunks <=16K, 63-67% above.
- **Wakeups**: dedicated capture gave 12,506 hand-offs (was 98). >=64K: n = 3,748-4,096, waking->running ~2.0 us at 64K/256K/512K
  with the same phases as latency (0.20 waker, 0.74-0.76 IPI+idle exit, 0.65 wakeup handling, 0.41 switch). MY ESTIMATE WAS WRONG for
  small chunks: I expected ~500 hand-offs at 4 B, got 20 (16 B 248, 64 B 30, 256 B 6, 1 KB 230, 4 KB 9, 16 KB 24): with a
  concurrent reader the pipe rarely empties, so wakeups are far rarer than 1 per 64 KB; the script warns for n < 30. Also many
  small-chunk wakeups have no IPI/idle exit (target CPU not idle). Section 2a/2c of summary.txt still come from the small general
  capture (128 KiB/size), so their small-chunk wakeup columns rest on 0-2 events: use `wakeup/` instead.
- **Flame graphs**: schedule (blocked) 23.3%, anon_pipe_read 5.4%, SELinux permission checks together ~15% (avc_has_perm 4.4,
  __file_has_perm 3.3, security_file_permission 3.3, selinux_file_permission 2.0, inode_has_perm 2.0), mutex_spin_on_owner 3.8%.
  ftrace capture is small (4 KiB/size), so "all" is dominated by small chunks; per-size graphs exist for 4B/4KB/64KB/512KB.
- `cores busy` slightly above 2 at 16 B (2.30) is over the physical limit: the perf stat run (1M chunks) and the runner (2M chunks,
  median) differ, treat it as approximate for tiny chunks.
