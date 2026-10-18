#!/usr/bin/env python3
"""Compact figures for the 2-page paper (each one column wide, 3.15 in, drawn at final size so fonts stay legible).

    python3 -B scripts/plot_twopage.py [--out figures]

Reads the script-made section 3a tables ("where the CPU cycles go") of the newest latency and throughput trace runs
and folds the nine components into seven so that the categorical palette stays within the validated series count:
  scheduler = scheduler/wakeup/idle + interrupts/IPIs/timers,  other = other kernel + user space.
Writes <out>/twopage_breakdown.png and <out>/twopage_lat_thr.png (300 dpi) and .pdf. Nothing is typed by hand.
"""
import argparse
import glob
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_paper_assets as mpa  # noqa: E402  (read_tables / breakdown_shares, read-only)
import analyze_latency as al       # noqa: E402
import analyze_throughput as at    # noqa: E402

# validated default categorical order (light): blue, orange, aqua, yellow, magenta, green, violet
GROUPS = [
    ("copying data",                   ["Copy"],                      "#2a78d6", "white"),
    ("pipe read/write logic",          ["PipeLogic"],                 "#eb6834", "white"),
    ("page alloc/free + memcg",        ["PageAlloc"],                 "#1baf7a", "black"),
    ("locking",                        ["Locking"],                   "#eda100", "black"),
    ("scheduler, wakeup, IPIs",        ["Sched", "Interrupts"],       "#e87ba4", "black"),
    ("syscall entry, VFS, security",   ["Syscall"],                   "#008300", "white"),
    ("other kernel + user",            ["OtherKernel", "User"],       "#4a3aa7", "white"),
]
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e1e0d9"
TICK = mpa.TICK


def newest(pattern):
    d = sorted(glob.glob(pattern))
    if not d:
        sys.exit(f"nothing matches {pattern}")
    return d[-1].rstrip("/")


def fig_lat_thr(out, root):
    lat_files = sorted(glob.glob(os.path.join(root, "results/latency_run*.csv")))
    tp_files = sorted(glob.glob(os.path.join(root, "results/throughput_run*.csv")))
    lat = {r["size"]: r for r in al.summarize([al.load(p) for p in lat_files])}
    tp = {r["size"]: r for r in at.summarize([at.load(p) for p in tp_files])}
    sizes = [4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 524288]
    import matplotlib.pyplot as plt
    LAT, THR = "#2a78d6", "#eb6834"
    fig, axl = plt.subplots(figsize=(3.15, 2.2))
    axt = axl.twinx()
    x = range(len(sizes))
    (ln,) = axl.plot(x, [lat[s]["oneway"] / 1e3 for s in sizes], "o-", color=LAT, lw=1.6, ms=3.6, mec="white",
                     mew=0.6, label="minimum one-way latency (left axis)")
    (tn,) = axt.plot(x, [tp[s]["best"] for s in sizes], "s-", color=THR, lw=1.6, ms=3.6, mec="white", mew=0.6,
                     label="best throughput (right axis)")
    for ax in (axl, axt):
        ax.set_yscale("log")
    axl.set_ylim(0.6, 400)
    axt.set_ylim(8, 300000)
    axl.set_ylabel("One-way latency ($\\mu$s)")
    axt.set_ylabel("Throughput (MiB/s)")
    axl.tick_params(axis="y", colors=LAT, which="both")
    axt.tick_params(axis="y", colors=THR, which="both")
    axl.spines["left"].set_color(LAT)
    axt.spines["right"].set_color(THR)
    axl.spines["right"].set_visible(False)
    axt.spines["left"].set_visible(False)
    for ax in (axl, axt):
        ax.spines["top"].set_visible(False)
    axl.set_xticks(list(x))
    axl.set_xticklabels([TICK[s] for s in sizes], rotation=45, ha="right", rotation_mode="anchor", fontsize=7.4)
    axl.set_xlabel("Size (bytes)", labelpad=2)
    axl.grid(True, axis="y", color=GRID, lw=0.5)
    axl.set_axisbelow(True)
    ib, il = sizes.index(4096), sizes.index(65536)
    for xv in (ib, il):
        axl.axvline(xv, color=MUTED, ls=":", lw=1.0)
    tr = axl.get_xaxis_transform()
    axl.text(ib - 0.14, 0.97, "PIPE_BUF", transform=tr, rotation=90, ha="right", va="top", fontsize=6.8, color=MUTED)
    axl.text(il - 0.14, 0.97, "capacity", transform=tr, rotation=90, ha="right", va="top", fontsize=6.8,
             color=MUTED)
    axl.legend(handles=[ln, tn], loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=1, frameon=False,
               fontsize=7.2, handlelength=1.6, labelspacing=0.25)
    os.makedirs(out, exist_ok=True)
    fig.savefig(os.path.join(out, "twopage_lat_thr.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(out, "twopage_lat_thr.pdf"), bbox_inches="tight")
    print("wrote", os.path.join(out, "twopage_lat_thr.png"))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="figures")
    a = ap.parse_args()
    root = os.path.dirname(HERE)
    lat_run = newest(os.path.join(root, "results/trace/latency/*"))
    tp_run = newest(os.path.join(root, "results/trace/throughput/*"))
    lat = mpa.breakdown_shares(mpa.read_tables(os.path.join(lat_run, "summary.txt"))["3a"])
    tp = mpa.breakdown_shares(mpa.read_tables(os.path.join(tp_run, "summary.txt"))["3a"])
    bars = [(4, lat), (65536, lat), (524288, lat), (4, tp), (4096, tp), (65536, tp), (524288, tp)]
    xs = [0, 1, 2, 3.5, 4.5, 5.5, 6.5]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.labelsize": 8.5, "xtick.labelsize": 8, "ytick.labelsize": 8,
                         "legend.fontsize": 7.3, "pdf.fonttype": 42, "font.family": "DejaVu Sans",
                         "text.color": INK, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, ax = plt.subplots(figsize=(3.15, 2.05))
    bottom = [0.0] * len(bars)
    for label, comps, color, txt in GROUPS:
        vals = [sum((src[s].get(c) or 0.0) for c in comps) for s, src in bars]
        ax.bar(xs, vals, bottom=bottom, width=0.8, color=color, edgecolor="white", linewidth=0.8, label=label)
        for x, v, b in zip(xs, vals, bottom):
            if v >= 9:
                ax.text(x, b + v / 2, f"{v:.0f}", ha="center", va="center", fontsize=6.8, color=txt)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(xs)
    ax.set_xticklabels([TICK[s] for s, _ in bars])
    ax.set_xlabel("Size (bytes)", labelpad=2)
    ax.set_ylabel("Share of CPU cycles (%)")
    ax.set_ylim(0, 100)
    ax.set_xlim(-0.6, 7.1)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(True, axis="y", color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.text(1, 101.5, "latency (per round trip)", ha="center", va="bottom", fontsize=7.3, color=MUTED)
    ax.text(5, 101.5, "throughput (per chunk)", ha="center", va="bottom", fontsize=7.3, color=MUTED)
    ax.axvline(2.75, color=GRID, lw=0.8)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=2, frameon=False, columnspacing=1.0,
              handlelength=1.1, handletextpad=0.4, labelspacing=0.3)
    os.makedirs(a.out, exist_ok=True)
    fig.savefig(os.path.join(a.out, "twopage_breakdown.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(a.out, "twopage_breakdown.pdf"), bbox_inches="tight")
    print("wrote", os.path.join(a.out, "twopage_breakdown.png"), "from", lat_run, "and", tp_run)
    plt.close(fig)
    fig_lat_thr(a.out, root)


if __name__ == "__main__":
    main()
