# 736-adv-os--mini-project-2

This is the repo for code for mini-project-2 for 736.

> [!IMPORTANT]
> The code in this repo is made specifically for my laptop, running Fedora on an intel lake chip.  
> More details on the lap spec can be found in `lap-specs.md`

It measures the **latency** and **throughput** of a Unix pipe (a `fork()`ed parent and child, two pipes) and
explains where the time goes.

| | `latency` | `throughput` |
|---|---|---|
| What it does | parent sends a message, child echoes it back; time the round trip, one-way = RTT / 2 | parent streams chunks one way, child sends one 1-byte ack at the end |
| Sizes | 4 B ... 512 KB | 4 B ... 512 KB (the chunk size of each `write()`) |
| Reported number | **minimum** RTT / one-way latency per size | **best** (shortest elapsed) MiB/s per size |

Every experiment has two parts: a **runner** (clean, frequency-pinned data: the numbers you report) and a
**tracer** (perf / strace / ftrace, which explains the numbers but slows the benchmark, so its timings are never
reported).

## Requirements

- Linux with `sudo`. The runners and tracers pin CPU frequency through sysfs and need root.
- `gcc`, `make`, `perf`, `strace`, `trace-cmd` (`sudo dnf install trace-cmd`), `setpriv` (util-linux), `python3`
  with `matplotlib` (for the figures).
- CPUs are hard-coded for this machine: parent on CPU 0, child on CPU 1 (P-cores), tracing tools on CPU 2, all
  pinned to 4000 MHz. On another machine change `PARENT_CPU`, `CHILD_CPU`, `TRACE_CPU` and `FREQ` in
  `scripts/trace-lib.sh`, `scripts/latency-runner.sh` and `scripts/throughput-runner.sh`.
- Close heavy applications (browser, video) while running: the results are sensitive to background load.

## Build

```
make                     # builds out/clockres, out/round-trip-pipes, out/latency, out/throughput
getcap out/latency out/throughput      # each should show: cap_sys_nice=ep
```

`make` runs `sudo setcap cap_sys_nice+ep` on every binary so they can use `SCHED_FIFO` without root, so it asks for
your sudo password. The capability is wiped by every relink, which is why it is part of the build. After changing a
compiler flag run `make clean && make` (make does not notice flag changes).

## Run the latency experiment

Run the steps in this order: the tracer compares itself against the runner results.

```
sudo -v                                                                  # cache sudo credentials

# 1. clean runs, frequency-pinned (5 runs so the minimum can be checked for reproducibility)
for i in 1 2 3 4 5; do sudo make latency.run > results/latency_run$i.csv; done
head -1 results/latency_run1.csv       # must be: size_bytes,iteration,rtt_ns
wc -l results/latency_run*.csv         # each should be 20001

# 2. the trace: perf stat / perf record / strace / ftrace, plus the summary and extra outputs
sudo ./scripts/latency-trace.sh 2>&1 | tee results/latency-trace-run.log

# 3. the reported numbers and the figure
python3 -B scripts/analyze_latency.py results/latency_run*.csv
```

The output files must be named `results/latency_run<N>.csv`: the analysis script and the trace summary both look
for exactly that pattern. If the first line of a CSV is not the header above, the file is not usable.

## Run the throughput experiment

Same order, same idea:

```
sudo -v

# 1. clean runs
for i in 1 2 3 4 5; do sudo make throughput.run > results/throughput_run$i.csv; done
head -1 results/throughput_run1.csv    # must be: size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec
wc -l results/throughput_run*.csv      # each should be 31

# 2. the trace
sudo ./scripts/throughput-trace.sh 2>&1 | tee results/throughput-trace-run.log

# 3. the reported numbers and the figure
python3 -B scripts/analyze_throughput.py results/throughput_run*.csv
```

`MiB/s` here means bytes / 2^20 per second (the analysis prints GB/s next to it).

## What you get

| Output | Where |
|---|---|
| Raw runner data | `results/latency_run<N>.csv`, `results/throughput_run<N>.csv` |
| Reported table + figure | printed by `analyze_*.py`; `figures/latency.png/.pdf`, `figures/throughput.png/.pdf` |
| Trace summary (counters, scheduling, cycle breakdown, syscalls, ftrace) | `results/trace/<latency\|throughput>/<timestamp>/summary.txt` |
| Cycle-breakdown chart | `.../<timestamp>/breakdown.png/.pdf` |
| Wakeup phases (table, CSV, chart) | `.../<timestamp>/wakeup/` |
| ftrace flame graphs (`.svg` opens in a browser, hover for details) | `.../<timestamp>/flamegraphs/` |
| Raw captures (large) | `.../<timestamp>/perf_*.data`, `trace.dat`, `strace_*` |

`summary.txt` and everything in `wakeup/` and `flamegraphs/` are written by the scripts; do not edit them by hand.
The trace timings are inflated by the tracing itself: use them for proportions and counts, and take latency and
throughput numbers only from the runner data.

## Re-generating the analysis without re-running

The raw captures stay in the run directory, so the analysis can be redone. Use the run directory you want
(`RUN=results/trace/latency/<timestamp>`, or `throughput`):

```
python3 -B scripts/wakeup_timeline.py $RUN          # wakeup/   (no sudo needed)
python3 -B scripts/ftrace_flamegraph.py $RUN        # flamegraphs/   (no sudo needed)

# summary.txt needs root (kernel symbols) and matplotlib, which is installed for your user only:
sudo env PYTHONPATH="$(python3 -c 'import site;print(site.getusersitepackages())')" MPLCONFIGDIR=/tmp/mpl \
    python3 -B scripts/trace_summary.py $RUN | tee $RUN/summary.txt          # latency
    # use scripts/trace_summary_throughput.py for a throughput run
sudo chown -R $USER $RUN
```

## Running a benchmark by hand

```
./out/latency [-s size_bytes] [-n] [-q] [warmup iterations]     # defaults: 20 warmup, 2000 iterations, all sizes
./out/throughput [-s size_bytes] [-n] [-q] [-b total_bytes | -i chunks] [-r repeats]
```

`-s` runs one size, `-n` skips the ~1 s clock calibration (the CSV then holds raw TSC cycles, not nanoseconds), `-q`
prints no per-iteration rows. Run this way there is no frequency pinning, so the numbers are only a smoke test.
`out/round-trip-pipes` is a correctness check (echoes every size and compares the bytes); `out/clockres` measures
timer resolution (`sudo make clockres.run`).

## If a run is interrupted

The scripts restore CPU frequency, ftrace state and the perf sysctls when they exit, including on errors and Ctrl-C.
After a hard kill (SIGKILL, power loss) the cores can stay pinned at 4 GHz. Restore them with:

```
sudo ./scripts/unpin_freq.sh 0; sudo ./scripts/unpin_freq.sh 1; sudo ./scripts/unpin_freq.sh 2
```

## Repository layout

- `src/`: `latency.c`, `throughput.c`, `clockres.c`, `round-trip-pipes.c`, shared `common.c/.h`
- `scripts/`: `*-runner.sh` (clean runs), `*-trace.sh` + `trace-lib.sh` (tracing), `pin_freq.sh`/`unpin_freq.sh`,
  and the analysis scripts (`analyze_*.py`, `trace_summary*.py`, `wakeup_timeline.py`, `ftrace_flamegraph.py`)
- `results/`, `figures/`: data and figures produced by the commands above
- `lap-specs.md`: hardware and software of the machine used
