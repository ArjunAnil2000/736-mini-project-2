#!/usr/bin/env python3
"""analyze_throughput.py <throughput_run1.csv> [more.csv ...] [--out figures]

Turns the raw per-repeat CSV(s) written by `throughput` (size_bytes,total_bytes,repeat,elapsed_ns,
mb_per_sec) into the numbers the paper reports, and a figure.

  * UNIT: throughput is bytes / 2^20 per second, i.e. MiB/s (the benchmark's own "mb_per_sec" column
    divides by 1024*1024). The decimal GB/s (10^9 bytes) is printed next to it for convenience.
  * The assignment prefers extremal statistics over averages for elapsed time: noise can only make a
    transfer slower, so the SHORTEST elapsed time, i.e. the HIGHEST throughput, is the closest
    observation to the true capability. That is the headline number ("best"). Median is printed too.
  * Given several files (repeated runs), the best is taken over ALL rows of ALL files, and the run-to-run
    spread of the per-run bests is shown, which is the evidence that the best is reproducible.

Refused with a clear message: traced files (header elapsed_cycles, from `throughput -n`), files whose
first line is not the CSV header (e.g. the echo of a make command that ended up in the file), and rows
that do not parse.
"""
import argparse
import csv
import os
import statistics as st
import sys
from collections import defaultdict

PIPE_BUFFER = 65536  # default Linux pipe capacity; see lap-specs.md
HEADER = ["size_bytes", "total_bytes", "repeat", "elapsed_ns", "mb_per_sec"]
TRACED_HEADER = ["size_bytes", "total_bytes", "repeat", "elapsed_cycles", "bytes_per_cycle"]
MIB = 1024 * 1024


def load(path):
    """-> [(size, total_bytes, elapsed_ns, mibps)], mibps recomputed from bytes and time."""
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if header == TRACED_HEADER:
            sys.exit(f"{path}: this is a traced run (throughput -n): its elapsed time is in raw cycles, "
                     "not a throughput measurement")
        if header != HEADER:
            hint = ""
            if header is not None and len(header) == 1:
                hint = (f" Line 1 is {header[0]!r}: that is not CSV. If it is the echo of a make command, "
                        "the Makefile recipe needs a leading @; delete that line and re-check.")
            sys.exit(f"{path}: header is {header}, expected {','.join(HEADER)}.{hint}")
        rows, bad_unit = [], 0
        for n, r in enumerate(rd, start=2):
            try:
                size, total, elapsed, mibps = int(r[0]), int(r[1]), float(r[3]), float(r[4])
            except (ValueError, IndexError):
                sys.exit(f"{path}: line {n} does not parse: {r}")
            calc = total / (elapsed / 1e9) / MIB
            if abs(calc - mibps) > 0.005 * max(calc, 1e-9) + 0.002:  # .3f rounding of the file's own column
                bad_unit += 1
            rows.append((size, total, elapsed, calc))
    if not rows:
        sys.exit(f"{path}: no data rows")
    if bad_unit:
        print(f"warning: {path}: {bad_unit} row(s) whose mb_per_sec column disagrees with total_bytes/elapsed_ns "
              "(unit changed?)", file=sys.stderr)
    return rows


def human(sz):
    return f"{sz // 1024}K" if sz >= 1024 else str(sz)


def summarize(runs):
    """runs: list of load() results. -> per-size dicts, sorted by size."""
    by = defaultdict(list)       # size -> all MiB/s
    per_run = defaultdict(list)  # size -> [best MiB/s of each run]
    total = {}
    for run in runs:
        best = {}
        for size, tot, elapsed, mibps in run:
            by[size].append(mibps)
            total[size] = tot
            best[size] = max(best.get(size, 0.0), mibps)
        for size, b in best.items():
            per_run[size].append(b)
    out = []
    for size in sorted(by):
        v, pr = by[size], per_run[size]
        out.append(dict(size=size, n=len(v), total=total[size], best=max(v), median=st.median(v), worst=min(v),
                        spread=(max(pr) - min(pr)) / max(pr) * 100 if len(pr) > 1 else None,
                        lucky=len(pr) > 1 and max(pr) > 1.15 * st.median(pr)))
    return out


def best_median_by_size(files):
    """{size: (best MiB/s, median MiB/s)} over the given files, skipping any that are not valid."""
    runs = []
    for f in files:
        try:
            runs.append(load(f))
        except SystemExit:
            pass
    return {r["size"]: (r["best"], r["median"]) for r in summarize(runs)} if runs else {}


def print_table(rows, nruns):
    print(f"\nThroughput (pipe, one-way bulk transfer + a single 1-byte ack; parent on CPU 0, child on CPU 1); "
          f"{nruns} run(s)")
    print("unit: MiB/s = bytes / 2^20 per second; GB/s = 10^9 bytes per second. best = shortest elapsed time.\n")
    hdr = ["chunk", "rows", "MiB moved", "BEST MiB/s", "best GB/s", "median MiB/s", "worst MiB/s"]
    if nruns > 1:
        hdr.append("best spread")
    body = []
    for r in rows:
        line = [human(r["size"]), f"{r['n']:,}", f"{r['total'] / MIB:,.1f}",
                f"{r['best']:,.1f}" + ("*" if r["lucky"] else ""), f"{r['best'] * MIB / 1e9:,.3f}",
                f"{r['median']:,.1f}", f"{r['worst']:,.1f}"]
        if nruns > 1:
            line.append(f"{r['spread']:.1f}%")
        body.append(line)
    w = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(hdr)]
    print("  " + "  ".join(h.rjust(w[i]) for i, h in enumerate(hdr)))
    print("  " + "  ".join("-" * w[i] for i in range(len(hdr))))
    for b in body:
        print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(b)))
    print("\n  chunk = bytes per write()/read() call; MiB moved = data per repeat.")
    if any(r["lucky"] for r in rows):
        print("  * one run's best is >15% above the median of the per-run bests: a lucky run, not the typical best.")
    if nruns > 1:
        print("  best spread = (highest - lowest per-run best) / highest: how reproducible the best is")


def plot(rows, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 11, "axes.labelsize": 12, "legend.fontsize": 10})
    sz = [r["size"] for r in rows]
    fig, ax = plt.subplots(figsize=(6.2, 3.8))
    ax.plot(sz, [r["best"] for r in rows], "o-", color="#1f5fbf", lw=2, ms=6, label="best (reported)")
    ax.plot(sz, [r["median"] for r in rows], "s--", color="#888888", lw=1.3, ms=4, label="median")
    ax.axvline(PIPE_BUFFER, color="#c0392b", ls=":", lw=1.4)
    # x in data coordinates, y in axes fraction (a data y on a log axis would need a positive value)
    ax.text(PIPE_BUFFER * 0.92, 0.04, "64 KB pipe buffer", transform=ax.get_xaxis_transform(),
            color="#c0392b", fontsize=9, ha="right", va="bottom")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(sz)
    ax.set_xticklabels([human(s) for s in sz], rotation=45)
    ax.minorticks_off()
    ax.set_xlabel("Chunk size per write()/read() (bytes)")
    ax.set_ylabel("Throughput (MiB/s)")
    ax.grid(True, which="major", alpha=0.3)
    ax.legend(loc="upper left")  # small chunks are slow, so the top-left of the plot is empty
    fig.tight_layout()
    os.makedirs(out_dir, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, f"throughput.{ext}"), dpi=200)
    plt.close(fig)
    return os.path.join(out_dir, "throughput.png")


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
        print(f"\nfigure: {plot(rows, a.out)} (+ .pdf)")


if __name__ == "__main__":
    main()
