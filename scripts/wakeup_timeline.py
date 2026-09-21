#!/usr/bin/env python3
"""wakeup_timeline.py <run_dir> [--out DIR]

Breaks every WAKEUP of a benchmark task into its phases, from the tracepoint captures of a
latency-trace.sh or throughput-trace.sh run (the benchmark's process name is read from params.txt).

Two possible sources, the first preferred when present:
  * perf_wakeup_<size>.data  DEDICATED captures (throughput-trace.sh): only scheduler / IPI / idle events, one
    file per size, moving much more data, so there are enough wakeups per size. No syscall events in them, so
    the last phase (switch -> syscall returns) is not available and is left out.
  * perf_trace.data          the general tracepoint capture (latency-trace.sh; also in throughput runs): has
    the syscall events too, and the size is found from the benchmark's pipe writes. Every wakeup is in that capture (all tracepoint occurrences, nanosecond
timestamps), including the time both CPUs are idle, which no cycles profile can show.

A hand-off = one benchmark task waking the other. Its checkpoints, in order:

    waking      sched:sched_waking      the waker decides to wake the other task (waker's CPU)
    ipi         ipi:ipi_send_cpu        the waker sends an interrupt to the sleeping CPU
    idle exit   power:cpu_idle (exit)   the sleeping CPU leaves its idle state
    wakeup      sched:sched_wakeup      the woken task is made runnable (on its own CPU)
    switch      sched:sched_switch      the CPU switches to the woken task
    user        sys_exit_read/write     the woken task returns from its syscall

and the phases are the gaps between them. Not every hand-off has every checkpoint (if the target CPU was
not idle there is no idle exit), so each phase is a median over the hand-offs that have both ends, and the
tables say how many did.

CAUTION: the capture itself (15 tracepoints, every occurrence) slows the benchmark several times, so read
the PROPORTIONS between phases, not the absolute nanoseconds. Section 6 of summary.txt has the untraced
time per wakeup.

Writes, into <run_dir>/wakeup/ (or --out): wakeup_timeline.txt, wakeup_phases.csv (one row per
hand-off), wakeup_phases.png/.pdf.
"""
import argparse
import bisect
import collections
import csv
import io
import os
import re
import statistics as st
import sys
import contextlib
import glob

sys.dont_write_bytecode = True  # often run as root; do not leave a root-owned __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trace_summary as ts  # noqa: E402  (read-only reuse of its parsing helpers)

EXIT = 4294967295  # power:cpu_idle state value meaning "leaving idle"
PHASES = [  # (column, label, from-checkpoint, to-checkpoint)
    ("waking_to_ipi", "waking -> IPI sent", "waking", "ipi"),
    ("ipi_to_idle_exit", "IPI -> CPU leaves idle", "ipi", "idle exit"),
    ("idle_exit_to_wakeup", "idle exit -> wakeup handled", "idle exit", "wakeup"),
    ("wakeup_to_switch", "wakeup -> context switch", "wakeup", "switch"),
    ("switch_to_user", "switch -> syscall returns", "switch", "user"),
]
ORDER = ["waking", "ipi", "idle exit", "wakeup", "switch", "user"]
IPI_HORIZON = 30e-6      # an IPI / idle exit further than this after the waking is not ours
RUN_HORIZON = 500e-6     # wakeup / switch / return must follow within this


def first_after(times, t, horizon):
    i = bisect.bisect_left(times, t)
    return times[i] if i < len(times) and times[i] - t <= horizon else None


def pid_of(f, pat):
    m = re.search(pat, f)
    return int(m.group(1)) if m else None


def build_index(ev):
    ipi = collections.defaultdict(list)
    idle_exit = collections.defaultdict(list)
    wakeup = collections.defaultdict(list)
    switch_in = collections.defaultdict(list)
    sys_exit = collections.defaultdict(list)
    for e in ev:
        n = e["ev"]
        if n == "ipi:ipi_send_cpu":
            ipi[ts.field(e["f"], "cpu")].append(e["t"])
        elif n == "power:cpu_idle" and ts.field(e["f"], "state") == EXIT:
            idle_exit[e["cpu"]].append(e["t"])
        elif n == "sched:sched_wakeup":
            p = pid_of(e["f"], r":(\d+) \[")
            if p is not None:
                wakeup[p].append(e["t"])
        elif n == "sched:sched_switch":
            p = pid_of(e["f"], r"==> .*?:(\d+) \[")
            if p is not None:
                switch_in[p].append(e["t"])
        elif n in ("syscalls:sys_exit_read", "syscalls:sys_exit_write"):
            sys_exit[e["tid"]].append(e["t"])
    for d in (ipi, idle_exit, wakeup, switch_in, sys_exit):
        for v in d.values():
            v.sort()
    return ipi, idle_exit, wakeup, switch_in, sys_exit


def handoffs(ev, wins):
    """-> list of dicts, one per benchmark-wakes-benchmark hand-off, with checkpoint times."""
    bench = {e["tid"] for e in ev if e["comm"] == ts.BENCH_COMM}
    ipi, idle_exit, wakeup, switch_in, sys_exit = build_index(ev)
    out, incomplete = [], 0
    for size, t0w, t1w in wins:
        for e in ev:
            if not (t0w <= e["t"] < t1w) or e["ev"] != "sched:sched_waking" or e["tid"] not in bench:
                continue
            pid, tcpu = ts.field(e["f"], "pid"), ts.field(e["f"], "target_cpu")
            if pid not in bench:
                continue
            t0 = e["t"]
            cp = {"waking": t0}
            t_w = first_after(wakeup[pid], t0, RUN_HORIZON)
            t_s = first_after(switch_in[pid], t0, RUN_HORIZON)
            if t_w is None or t_s is None or t_s < t_w:
                incomplete += 1
                continue
            t_u = first_after(sys_exit[pid], t_s, RUN_HORIZON)
            t_i = first_after(ipi[tcpu], t0, IPI_HORIZON)
            t_x = first_after(idle_exit[tcpu], t0, IPI_HORIZON)
            if t_i is not None and t_i <= t_w:
                cp["ipi"] = t_i
            if t_x is not None and t_x <= t_w and t_x >= cp.get("ipi", t0):
                cp["idle exit"] = t_x
            cp["wakeup"], cp["switch"] = t_w, t_s
            if t_u is not None:
                cp["user"] = t_u
            out.append(dict(size=size, pid=pid, waker_cpu=e["cpu"], target_cpu=tcpu, cp=cp))
    return out, incomplete


def gap(h, a, b):
    ca, cb = h["cp"].get(a), h["cp"].get(b)
    return None if ca is None or cb is None else (cb - ca) * 1e9


def med(vals):
    vals = [v for v in vals if v is not None]
    return (st.median(vals), len(vals)) if vals else (None, 0)


def fmt(v, d=0):
    return "n/a" if v is None else f"{v:,.{d}f}"


def report(hs, incomplete, sizes, p, source):
    have_user = any("user" in h["cp"] for h in hs)
    phases = [ph for ph in PHASES if have_user or ph[0] != "switch_to_user"]
    print("WAKEUP TIMELINE  (every wakeup of one benchmark task by the other)")
    print(f"  source: {source}")
    print(f"  {len(hs)} complete hand-offs analysed, {incomplete} skipped (an end of the wakeup was not found).")
    print("  The capture slows the benchmark several times over: read the proportions between phases,")
    print("  not the absolute nanoseconds. Each phase is the median over the hand-offs that have both ends.\n")

    by = collections.defaultdict(list)
    for h in hs:
        by[h["size"]].append(h)
    rows = []
    for sz in sizes:
        g = by.get(sz, [])
        if not g:
            continue
        ipi_share = 100 * sum("ipi" in h["cp"] for h in g) / len(g)
        idle_share = 100 * sum("idle exit" in h["cp"] for h in g) / len(g)
        row = [ts.human_size(sz), len(g), f"{ipi_share:.0f}%", f"{idle_share:.0f}%"]
        for col, _, a, b in phases:
            m, _ = med(gap(h, a, b) for h in g)
            row.append(fmt(m))
        w_run, _ = med(gap(h, "waking", "switch") for h in g)
        row.append(fmt(w_run))
        if have_user:
            row.append(fmt(med(gap(h, "waking", "user") for h in g)[0]))
        rows.append(row)
    hdr = ["size", "hand-offs", "IPI", "idle exit"] + [lab for _, lab, _, _ in phases] + ["waking->running"]
    if have_user:
        hdr.append("waking->user")
    ts.table(hdr, rows, "A. median time per phase, in ns (per size)")
    print("\n  'IPI' / 'idle exit' = share of hand-offs that had that checkpoint: without an IPI the target CPU was\n"
          "  not sleeping in idle. 'waking->running' = waking to the context switch."
          + ("\n  'waking->user' = to the woken task returning from its syscall (for messages above the 64 KB pipe\n"
             "  buffer that includes copying)." if have_user else
             "\n  (No syscall events in this capture, so there is no 'switch -> syscall returns' phase.)"))

    end = "user" if have_user else "switch"
    rows = []
    for sz in sizes:
        g = [gap(h, "waking", end) for h in by.get(sz, [])]
        g = sorted(v for v in g if v is not None)
        if len(g) >= 5:
            rows.append([ts.human_size(sz), len(g), fmt(g[len(g) // 10]), fmt(st.median(g)), fmt(g[(9 * len(g)) // 10]), fmt(g[-1])])
    if rows:
        ts.table(["size", "n", "p10 ns", "median ns", "p90 ns", "max ns"], rows,
                 f"\nB. spread of the whole wakeup (waking -> {'user' if have_user else 'running'}); sizes with fewer than 5 hand-offs are left out")
    few = [ts.human_size(sz) for sz in sizes if 0 < len(by.get(sz, [])) < 30]
    if few:
        print("\n  !! fewer than 30 hand-offs, treat with care: " + ", ".join(few))

    print("\nC. one representative hand-off per profiled size (the one closest to that size's median), ns from 'waking'")
    for sz in [int(x) for x in p.get("cycles_sizes", "4 65536 524288").split()]:
        g = [h for h in by.get(sz, []) if end in h["cp"]]
        if not g:
            continue
        target = st.median(gap(h, "waking", end) for h in g)
        h = min(g, key=lambda x: abs(gap(x, "waking", end) - target))
        print(f"\n  {ts.human_size(sz)}: waker on CPU {h['waker_cpu']}, woken task on CPU {h['target_cpu']}")
        prev = None
        for name in ORDER:
            if name in h["cp"]:
                t = (h["cp"][name] - h["cp"]["waking"]) * 1e9
                step = "" if prev is None else f"   (+{t - prev:,.0f})"
                print(f"    {t:9,.0f} ns  {name}{step}")
                prev = t


def write_csv(path, hs):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["size_bytes", "t_waking_s", "pid", "waker_cpu", "target_cpu"] + [c for c, *_ in PHASES]
                   + ["waking_to_running_ns", "waking_to_user_ns"])
        for h in hs:
            row = [h["size"], f"{h['cp']['waking']:.9f}", h["pid"], h["waker_cpu"], h["target_cpu"]]
            for _, _, a, b in PHASES:
                g = gap(h, a, b)
                row.append("" if g is None else f"{g:.0f}")
            for a, b in (("waking", "switch"), ("waking", "user")):
                g = gap(h, a, b)
                row.append("" if g is None else f"{g:.0f}")
            w.writerow(row)


def chart(path_base, hs, sizes):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("!! matplotlib is not importable (as root it lives only in the user's site-packages): "
              "wakeup chart NOT written", file=sys.stderr)
        return None
    by = collections.defaultdict(list)
    for h in hs:
        by[h["size"]].append(h)
    sizes = [s for s in sizes if by.get(s)]
    if not sizes:
        return None
    colors = ["#e67e22", "#c0392b", "#8e44ad", "#1f5fbf", "#16a085"]
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 11})
    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    phases = [(ph, c) for ph, c in zip(PHASES, colors) if any(gap(h, ph[2], ph[3]) is not None for h in hs)]
    meds = {s: [med(gap(h, a, b) for h in by[s])[0] or 0.0 for (_, _, a, b), _ in phases] for s in sizes}
    bottom = [0.0] * len(sizes)
    for k, ((col, lab, a, b), c) in enumerate(phases):
        vals = [100 * meds[s][k] / (sum(meds[s]) or 1) for s in sizes]
        ax.bar(range(len(sizes)), vals, bottom=bottom, color=c, edgecolor="white", width=0.65, label=lab)
        bottom = [x + v for x, v in zip(bottom, vals)]
    for i, s in enumerate(sizes):  # the total of the plotted phases, so the shares have a scale
        ax.text(i, 101, f"{sum(meds[s]) / 1e3:,.1f} µs", ha="center", va="bottom", fontsize=8, rotation=90)
    ax.set_xticks(range(len(sizes)))
    ax.set_xticklabels([ts.human_size(s) for s in sizes], rotation=45)
    ax.set_xlabel("Message size (timings taken under tracing)")
    ax.set_ylabel("Share of one wakeup (%)")
    ax.set_ylim(0, 118)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8, frameon=False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{path_base}.{ext}", dpi=200)
    plt.close(fig)
    return path_base + ".png"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--out", help="output directory (default: <run_dir>/wakeup)")
    a = ap.parse_args()
    d = a.run_dir.rstrip("/")
    p = ts.read_params(d)
    if p.get("name"):
        ts.BENCH_COMM = p["name"]  # "latency" or "throughput": the benchmark's process name in the captures
    sizes = [int(x) for x in p.get("sizes", "").split()]
    ded = {int(m.group(1)): f for f in glob.glob(f"{d}/perf_wakeup_*.data")
           for m in [re.search(r"perf_wakeup_(\d+)\.data$", f)] if m}
    path = f"{d}/perf_trace.data"
    if not ded and not os.path.exists(path):
        sys.exit(f"neither perf_wakeup_<size>.data nor {path} found (is this a trace run directory?)")
    out = a.out or f"{d}/wakeup"
    os.makedirs(out, exist_ok=True)
    hs, incomplete = [], 0
    if ded:
        source = (f"dedicated wakeup captures, {len(ded)} files (perf_wakeup_<size>.data): scheduler, IPI and idle "
                  "events only, no syscall events")
        for size in sorted(ded):
            ev = ts.load_events(ded[size])
            if ev:
                h, inc = handoffs(ev, [(size, min(e["t"] for e in ev), max(e["t"] for e in ev) + 1e-9)])
                hs += h
                incomplete += inc
    else:
        source = "perf_trace.data (general tracepoint capture, syscall events included)"
        ev = ts.load_events(path)
        wins = ts.size_windows(ev, set(sizes))
        if not wins:
            sys.exit("could not find the benchmark's pipe writes in the capture")
        hs, incomplete = handoffs(ev, wins)
    if not hs:
        sys.exit("no wakeups of the benchmark found in the captures")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(hs, incomplete, sizes, p, source)
    text = buf.getvalue()
    open(f"{out}/wakeup_timeline.txt", "w").write(text)
    write_csv(f"{out}/wakeup_phases.csv", hs)
    fig = chart(f"{out}/wakeup_phases", hs, sizes)
    print(f"wakeup timeline: {len(hs)} hand-offs -> {out}/wakeup_timeline.txt, wakeup_phases.csv"
          + (f", {os.path.basename(fig)} (+ .pdf)" if fig else ""))


if __name__ == "__main__":
    main()
