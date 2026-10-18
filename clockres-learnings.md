# clockres learnings

***WARNING***: This is AI slop to be used as a base for learning.

Timers currently measured by `src/clockres.c`, and what each one is / what we found. Data below is from a run pinned to CPU 0 at a fixed 4000 MHz (`sudo make clockres.run`, see `scripts/pin_freq.sh` / `scripts/clockres-runner.sh`).

```
# sudo make clockres.run
...<pin frequency>...

=== Timer resolution (smallest observed non-zero delta, 2000 trials) ===
clock_gettime(CLOCK_MONOTONIC):             9.0 ns
clock_gettime(CLOCK_MONOTONIC_COARSE):      999983.0 ns
clock_gettime(CLOCK_PROCESS_CPUTIME_ID):    102.0 ns
gettimeofday:                               1000.0 ns
rdtsc_cycles:                               20 cycles
calibrated TSC frequency:                   3.302 GHz
rdtsc resolution (calibrated):              6.1 ns

=== Accuracy check: sleep(2) ===
clock_gettime(CLOCK_MONOTONIC) measured:          2000028959 ns
clock_gettime(CLOCK_MONOTONIC_COARSE) measured:   1999972781 ns
gettimeofday measured:                            2000027000 ns
clock_gettime(CLOCK_PROCESS_CPUTIME_ID) measured: 21609 ns (expected ~0: process was blocked in sleep(), not consuming CPU)
rdtsc (calibrated) measured:                      2000020517.5 ns

...<unpin frequency>...
```

## Timers used, and what each one is

### `gettimeofday()`
Legacy POSIX wall-clock API, returns a `struct timeval` (seconds + microseconds). Measured resolution floor is a hard **1000 ns (1 µs)** — its representation simply cannot express anything finer, regardless of how fine the underlying hardware clock actually is. Still tracks true elapsed time correctly (2000070000 ns over a `sleep(2)`, i.e. within ~70 µs of real).

### `clock_gettime(CLOCK_MONOTONIC)`
POSIX monotonic wall-clock timer, backed on this system by the vDSO fast path (avoids a syscall) reading a TSC-derived time source. Measured resolution: **~9 ns** — about 100x finer than `gettimeofday`. This is the timer used for all IPC latency/throughput measurements going forward, since it offers the best resolution without the cross-core comparability caveats that raw `rdtsc` has.

### `clock_gettime(CLOCK_MONOTONIC_COARSE)`
Same monotonic clock family, but deliberately updated only once per timer tick instead of via the fine vDSO/TSC path. Measured resolution: **~1,000,000 ns (1 ms)** — roughly 100,000x coarser than plain `CLOCK_MONOTONIC`, despite being read through the exact same API. This is the key lesson from adding it: **timer resolution is a property of what's backing the clock, not of the `clock_gettime()` interface itself.** It still tracks elapsed wall time correctly over the long run (1999976938 ns over a nominal 2s sleep), it's just quantized to ~1 ms steps.

### `clock_gettime(CLOCK_PROCESS_CPUTIME_ID)`
Measures **CPU time actually consumed by this process**, not wall-clock elapsed time. Measured resolution: **~103 ns**, similar order to `CLOCK_MONOTONIC`. The interesting result is in the accuracy check: across a full `sleep(2)` (2 real seconds elapsed), this clock only advanced **34192 ns (~34 µs)** — because the process was blocked/off-CPU for essentially the entire sleep and only briefly on-CPU for setup/tear-down and the timer reads themselves. This demonstrates concretely why picking the right *kind* of clock matters: a CPU-time clock and a wall-clock timer answer different questions, and mixing them up would silently produce nonsense (e.g. using this clock to measure something that mostly blocks, like a blocking `read()` on a pipe, would drastically under-report the true latency).

### `rdtsc` (calibrated)
Raw x86 cycle counter, read via the `__rdtsc()` intrinsic. Unlike the other timers, this one
doesn't return time directly — it returns a **cycle count**, which only becomes a time value
after converting through the TSC's actual tick rate (cycles/sec).

**Why calibration is needed at all:** this CPU has `constant_tsc`/`nonstop_tsc` (see
`lap-specs.md`), meaning the TSC ticks at one fixed reference rate that is *decoupled* from
whatever frequency the core is currently executing instructions at. So even though CPU 0 is
pinned to a constant 4000 MHz for this run, that number is **not** the TSC's tick rate — there
is no sysfs value to read it from; it has to be measured empirically.

**How it's calibrated:** `calibrate_tsc_hz()` times a fixed 100ms `nanosleep()` window against
`CLOCK_MONOTONIC` while also reading `rdtsc` at both ends, giving `cycles / seconds = Hz` for
that trial, repeated over `CALIBRATION_TRIALS` (10) independent trials.

Getting this right took two iterations:

- **Bracketing.** Each `rdtsc` read is sandwiched between *two* `clock_gettime` reads
  (`ts_before`, `rdtsc`, `ts_after`), and the **midpoint** of that pair is used as the read's
  effective timestamp, rather than trusting a single adjacent `clock_gettime` call. Reasoning:
  a naive single-clock_gettime-per-edge design (`clock_gettime` then `rdtsc` at the start,
  `rdtsc` then `clock_gettime` at the end) always has the true cycle-counted window
  `[c0, c1]` sitting *inside* the measured wall-clock window `[ts0, ts1]` — any scheduling
  delay between a pair can only stretch the wall-clock window relative to the cycle window,
  which biases the computed Hz **downward on every single trial, never upward**. Bracketing
  with the midpoint removes that systematic one-directional bias, leaving only small, roughly
  symmetric error instead.
- **Minimum, not average.** With the bias now symmetric rather than one-directional, taking
  the **minimum** Hz across the `CALIBRATION_TRIALS` trials is defensible — consistent with
  the assignment's broader guidance to prefer extremal statistics over averages. (Note this is
  the *opposite* extremum from the pipe latency case: there, noise can only inflate a timing
  measurement, so minimum is closest to truth; for the naive/unbracketed version of this Hz
  calculation, noise could only deflate it, so *maximum* would have been closest to truth. The
  bracketing fix is what makes "minimum" a reasonable choice here again, by removing that
  one-sided bias in the first place.)

`CALIBRATION_TRIALS` is a separate macro from the resolution-loop `TRIALS` (2000) —
reusing `TRIALS` directly would mean 2000 real 100ms sleeps, or ~200 extra seconds of runtime,
for no real accuracy benefit at this timescale.

**Result:** calibrated TSC frequency measured at **3.302 GHz** — notably *not* 4000 MHz,
confirming that the pinned core frequency and the TSC's own tick rate are genuinely
independent quantities on this hardware. The result was unchanged by the bracketing fix
(3.302 GHz before and after), consistent with any residual bias being far smaller than what's
visible at this measurement's precision on a quiet system — but the accuracy check below did
tighten noticeably.

Using that calibration, `rdtsc`'s resolution converts to **~6.1 ns** — in the same ballpark as
`CLOCK_MONOTONIC`'s ~9 ns, which makes sense since `CLOCK_MONOTONIC` is itself backed by a
TSC-derived time source on this system via the vDSO fast path.

**Accuracy check:** across the same `sleep(2)`, calibrated `rdtsc` measured
**2000020517.5 ns** (~2.0000205 s), agreeing with `CLOCK_MONOTONIC`'s 2000028959 ns to within
**~8 µs** — noticeably tighter than the ~40-43 µs gap seen before the bracketing fix, which is
itself decent evidence the fix genuinely improved calibration accuracy rather than just
changing the number for no reason.
