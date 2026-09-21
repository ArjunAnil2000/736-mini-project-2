#!/usr/bin/env python3
"""analyze_latency.py <latency.csv> [more.csv ...] [--out figures]

Turns the raw per-iteration CSV(s) written by `latency` (size_bytes,iteration,rtt_ns) into the
numbers the paper reports, and a figure.

  * The assignment says to use the MINIMUM, not the mean: noise can only make a round trip slower,
    so the minimum is the closest observation to the true cost. Mean/median/p99 are printed too, to
    show how much noise there is.
  * One-way latency = round-trip time / 2 (the assignment's method: only one clock, no cross-core
    timestamp comparison).
  * Given several files (repeated runs), the minimum is taken over ALL of them, and the run-to-run
    spread of the per-run minima is shown, which is the evidence that the minimum is reproducible.

Files whose header is rtt_cycles (traced runs, no calibration) are refused: they are not latencies.
"""
import argparse
import csv
import os
import statistics as st
import sys
from collections import defaultdict

PIPE_BUFFER = 65536  # default Linux pipe capacity; see lap-specs.md


def load(path):
    """-> {size: [rtt_ns, ...]}"""
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if header != ["size_bytes", "iteration", "rtt_ns"]:
            sys.exit(f"{path}: header is {header}, expected size_bytes,iteration,rtt_ns "
                     "(a traced 'rtt_cycles' file is not a latency measurement)")
        data = defaultdict(list)
        for r in rd:
            if len(r) == 3:
                data[int(r[0])].append(float(r[2]))
    if not data:
        sys.exit(f"{path}: no data rows")
    return data


def pct(vals, p):
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def human(sz):
    return f"{sz // 1024}K" if sz >= 1024 else str(sz)


def summarize(runs):
    sizes = sorted(set().union(*(r.keys() for r in runs)))
    rows = []
    for sz in sizes:
        allv = [v for r in runs for v in r.get(sz, [])]
        mins = [min(r[sz]) for r in runs if sz in r]
        rows.append(dict(size=sz, n=len(allv), rtt_min=min(allv), oneway=min(allv) / 2,
                         p1=pct(allv, 1), median=st.median(allv), p99=pct(allv, 99), rtt_max=max(allv),
                         spread=(max(mins) - min(mins)) / min(mins) * 100 if len(mins) > 1 else None))
    return rows


def print_table(rows, nruns):
    print(f"\nLatency (pipe, fork + two pipes; parent on CPU 0, child on CPU 1); {nruns} run(s)")
    print("min = best of all iterations (the reported number); one-way = min / 2\n")
    hdr = ["size", "n", "min RTT us", "ONE-WAY us", "p1 RTT us", "median us", "p99 us", "max us"]
    if nruns > 1:
        hdr.append("min spread")
    body = []
    for r in rows:
        lucky = r["p1"] > 1.15 * r["rtt_min"]  # the minimum is well below where the fastest 1% start
        line = [human(r["size"]), f"{r['n']:,}", f"{r['rtt_min'] / 1e3:,.2f}" + ("*" if lucky else ""),
                f"{r['oneway'] / 1e3:,.2f}", f"{r['p1'] / 1e3:,.2f}", f"{r['median'] / 1e3:,.2f}",
                f"{r['p99'] / 1e3:,.2f}", f"{r['rtt_max'] / 1e3:,.1f}"]
        if nruns > 1:
            line.append(f"{r['spread']:.1f}%")
        body.append(line)
    w = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(hdr)]
    print("  " + "  ".join(h.rjust(w[i]) for i, h in enumerate(hdr)))
    print("  " + "  ".join("-" * w[i] for i in range(len(hdr))))
    for b in body:
        print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(b)))
    print("\n  p1 = the 1st percentile: where the fastest 1% of round trips start, a more robust 'typical best'.")
    if any(r["p1"] > 1.15 * r["rtt_min"] for r in rows):
        print("  * the minimum is >15% below p1: it is a handful of lucky iterations, not the typical best case.")
        print("    The assignment says report the minimum, so it stays the headline number, but say so in the paper.")
    if nruns > 1:
        print("  min spread = (largest - smallest per-run minimum) / smallest: how reproducible the minimum is")


def plot(rows, out_dir, nruns):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 11, "axes.labelsize": 12, "legend.fontsize": 10})
    sz = [r["size"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    ax.plot(sz, [r["oneway"] / 1e3 for r in rows], "o-", color="#1f5fbf", lw=2, ms=6,
            label="minimum (reported)")
    ax.plot(sz, [r["p1"] / 2e3 for r in rows], "^:", color="#e67e22", lw=1.3, ms=4,
            label="1st percentile")
    ax.plot(sz, [r["median"] / 2e3 for r in rows], "s--", color="#888888", lw=1.3, ms=4,
            label="median")
    ax.axvline(PIPE_BUFFER, color="#c0392b", ls=":", lw=1.4)
    # x in data coordinates, y in axes fraction (a data y on a log axis would need a positive value)
    ax.text(PIPE_BUFFER * 0.92, 0.04, "64 KB pipe buffer", transform=ax.get_xaxis_transform(),
            color="#c0392b", fontsize=9, ha="right", va="bottom")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(sz)
    ax.set_xticklabels([human(s) for s in sz], rotation=45)
    ax.minorticks_off()
    ax.set_xlabel("Message size (bytes)")
    ax.set_ylabel("One-way latency (µs)")
    ax.grid(True, which="major", alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    os.makedirs(out_dir, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, f"latency.{ext}"), dpi=200)
    plt.close(fig)
    return os.path.join(out_dir, "latency.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--out", default="figures", help="directory for the figure (default: figures)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()
    runs = [load(p) for p in a.csv]
    rows = summarize(runs)
    print_table(rows, len(runs))
    if not a.no_plot:
        print(f"\nfigure: {plot(rows, a.out, len(runs))} (+ .pdf)")


if __name__ == "__main__":
    main()
