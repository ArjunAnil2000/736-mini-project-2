#!/usr/bin/env python3
"""One figure with latency (left axis) and throughput (right axis) on a shared size axis (for the paper).

    python3 -B scripts/plot_combined.py [--out figures] [--latency 'results/latency_run*.csv']
                                        [--throughput 'results/throughput_run*.csv']

Reads the same runner CSVs as analyze_latency.py / analyze_throughput.py and reuses their load()/summarize(), so the
numbers are identical to those scripts. Writes <out>/latency_throughput.png (300 dpi) and .pdf.
"""
import argparse
import glob
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze_latency as al       # noqa: E402
import analyze_throughput as at    # noqa: E402

SIZES = [4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 524288]
TICK = {4: "4", 16: "16", 64: "64", 256: "256", 1024: "1K", 4096: "4K", 16384: "16K", 65536: "64K",
        262144: "256K", 524288: "512K"}
PIPE_BUF, PIPE_LEN = 4096, 65536
BLUE, GREEN, ORANGE, RED = "#1f5fbf", "#1e8449", "#e67e22", "#c0392b"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="figures")
    ap.add_argument("--latency", default="results/latency_run*.csv")
    ap.add_argument("--throughput", default="results/throughput_run*.csv")
    a = ap.parse_args()

    lat_files = sorted(glob.glob(a.latency))
    tp_files = sorted(glob.glob(a.throughput))
    if not lat_files or not tp_files:
        sys.exit(f"no CSVs found ({a.latency}: {len(lat_files)}, {a.throughput}: {len(tp_files)})")
    lat = {r["size"]: r for r in al.summarize([al.load(p) for p in lat_files])}
    tp = {r["size"]: r for r in at.summarize([at.load(p) for p in tp_files])}
    missing = [s for s in SIZES if s not in lat or s not in tp]
    if missing:
        sys.exit(f"sizes missing from the data: {missing}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.labelsize": 8.5, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
                         "ytick.labelsize": 7.5, "pdf.fonttype": 42, "axes.linewidth": 0.7,
                         "font.family": "DejaVu Sans"})
    fig, axl = plt.subplots(figsize=(3.6, 2.9))
    axt = axl.twinx()
    x = range(len(SIZES))

    # latency (left axis): rtt values are ns; one-way = rtt / 2, shown in microseconds
    (ln,) = axl.plot(x, [lat[s]["oneway"] / 1e3 for s in SIZES], "o-", color=BLUE, lw=1.5, ms=3.5,
                     label="One-way latency, minimum (left axis)")
    # throughput (right axis): best run, MiB/s
    (tn,) = axt.plot(x, [tp[s]["best"] for s in SIZES], "s-", color=GREEN, lw=1.5, ms=3.5,
                     label="Throughput, best (right axis)")
    axl.set_yscale("log")
    axt.set_yscale("log")
    axl.set_ylim(0.6, 400)
    axt.set_ylim(8, 300000)
    axl.set_ylabel("One-way latency ($\\mu$s)", color=BLUE)
    axt.set_ylabel("Throughput (MiB/s)", color=GREEN)
    axl.tick_params(axis="y", colors=BLUE)
    axt.tick_params(axis="y", colors=GREEN)

    axl.set_xticks(x)
    axl.set_xticklabels([TICK[s] for s in SIZES], rotation=45, ha="right")
    axl.set_xlabel("Size (bytes)")
    axl.grid(True, axis="y", alpha=0.3, lw=0.5)
    ib, il = SIZES.index(PIPE_BUF), SIZES.index(PIPE_LEN)
    axl.axvline(ib, color=ORANGE, ls=":", lw=1.1)
    axl.axvline(il, color=RED, ls=":", lw=1.1)
    tr = axl.get_xaxis_transform()
    axl.text(ib - 0.12, 0.97, "PIPE_BUF", transform=tr, rotation=90, ha="right", va="top", fontsize=6.5,
             color=ORANGE)
    axl.text(il - 0.12, 0.97, "pipe capacity", transform=tr, rotation=90, ha="right", va="top", fontsize=6.5,
             color=RED)
    axl.legend(handles=[ln, tn], loc="upper center", bbox_to_anchor=(0.5, -0.30), frameon=False, ncol=1)

    fig.tight_layout(pad=0.4)
    os.makedirs(a.out, exist_ok=True)
    png = os.path.join(a.out, "latency_throughput.png")
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(a.out, "latency_throughput.pdf"), bbox_inches="tight")
    print(f"wrote {png} (+ .pdf) from {len(lat_files)} latency and {len(tp_files)} throughput runs")


if __name__ == "__main__":
    main()
