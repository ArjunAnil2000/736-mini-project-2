#!/usr/bin/env python3
"""Compact figures for the paper, from the finished latency/throughput trace runs.

    python3 -B scripts/plot_paper_figures.py [--out figures]
        [--lat-run results/trace/latency/<ts>] [--tp-run results/trace/throughput/<ts>]

Writes (300 dpi PNG + vector PDF each), in <out>:
  paper_breakdown   cycle share per component: latency 4 B / 64 KB / 512 KB and throughput 4 B / 4 KB / 64 KB / 512 KB
                    (single column; the numbers are the script-made section 3a tables of each run's summary.txt)
  paper_wakeup      the four phases of a wakeup (waking -> running), median per message size, latency run
                    (single column; from wakeup/wakeup_phases.csv)
  paper_flame       ftrace function_graph flame graphs of the kernel time under read()/write(), 4 B and 512 KB
                    (full width; from flamegraphs/ftrace_<size>.folded)
Reads existing outputs only: nothing here is hand-typed. The traced timings are inflated by tracing, so the figures are
for proportions.
"""
import argparse
import csv
import glob
import os
import statistics as st
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_paper_assets as mpa      # noqa: E402  (read_tables / breakdown_shares / palette, read-only)
import ftrace_flamegraph as fg       # noqa: E402  (tree + colours, read-only)

TICK = mpa.TICK


def newest(pattern):
    d = sorted(glob.glob(pattern))
    if not d:
        sys.exit(f"nothing matches {pattern}")
    return d[-1].rstrip("/")


def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.labelsize": 8.5, "legend.fontsize": 7, "xtick.labelsize": 7.5,
                         "ytick.labelsize": 7.5, "pdf.fonttype": 42, "axes.linewidth": 0.7,
                         "font.family": "DejaVu Sans"})
    return plt


def save(fig, out, name):
    fig.savefig(os.path.join(out, name + ".png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(out, name + ".pdf"), bbox_inches="tight")
    print("wrote", os.path.join(out, name + ".png"))


# ------------------------------------------------------------------ cycle breakdown
def fig_breakdown(out, lat_run, tp_run):
    plt = style()
    lat = mpa.breakdown_shares(mpa.read_tables(os.path.join(lat_run, "summary.txt"))["3a"])
    tp = mpa.breakdown_shares(mpa.read_tables(os.path.join(tp_run, "summary.txt"))["3a"])
    bars = [("L", 4, lat), ("L", 65536, lat), ("L", 524288, lat),
            ("T", 4, tp), ("T", 4096, tp), ("T", 65536, tp), ("T", 524288, tp)]
    xs = [0, 1, 2, 3.6, 4.6, 5.6, 6.6]
    fig, ax = plt.subplots(figsize=(3.4, 3.1))
    bottom = [0.0] * len(bars)
    for comp in mpa.COMPONENTS:
        vals = [(src[s].get(comp) or 0.0) for _, s, src in bars]
        ax.bar(xs, vals, bottom=bottom, width=0.78, color=mpa.COMP_COLOR[comp], edgecolor="white", lw=0.5,
               label=mpa.COMP_LABEL[comp])
        for x, v, b in zip(xs, vals, bottom):
            if v >= 8:
                ax.text(x, b + v / 2, f"{v:.0f}", ha="center", va="center", fontsize=6.3,
                        color="white" if comp in ("Copy", "Locking", "PageAlloc", "User") else "black")
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(xs)
    ax.set_xticklabels([TICK[s] for _, s, _ in bars], rotation=45, ha="right")
    ax.set_xlabel("Size (bytes)")
    ax.set_ylabel("Share of CPU cycles (%)")
    ax.set_ylim(0, 100)
    ax.set_xlim(-0.6, 7.2)
    ax.text(1, 102, "Latency", ha="center", va="bottom", fontsize=8)
    ax.text(5.1, 102, "Throughput", ha="center", va="bottom", fontsize=8)
    ax.axvline(2.8, color="#999999", lw=0.6, ls="-")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=2, frameon=False, columnspacing=1.0,
              handlelength=1.2)
    save(fig, out, "paper_breakdown")
    plt.close(fig)


# ------------------------------------------------------------------ wakeup phases
def fig_wakeup(out, lat_run):
    plt = style()
    path = os.path.join(lat_run, "wakeup", "wakeup_phases.csv")
    cols = ["waking_to_ipi", "ipi_to_idle_exit", "idle_exit_to_wakeup", "wakeup_to_switch"]
    labels = ["waker: waking to IPI sent", "IPI flight + idle exit", "wakeup handled (scheduler)",
              "wakeup to context switch"]
    colors = ["#e67e22", "#c0392b", "#8e44ad", "#1f5fbf"]
    by = {}
    for r in csv.DictReader(open(path)):
        by.setdefault(int(r["size_bytes"]), []).append(r)
    # a size whose wakeups mostly did NOT find the target CPU idle has phases over different subsets, so the phase
    # medians do not add up to its waking->running time: leave it out (and say so)
    sizes = []
    for s in mpa.SIZES:
        if s not in by:
            continue
        share = sum(1 for r in by[s] if r["ipi_to_idle_exit"] != "") / len(by[s])
        if share >= 0.9:
            sizes.append(s)
        else:
            print(f"paper_wakeup: size {s} omitted (only {share:.0%} of its wakeups had an idle exit)")
    med = {s: [st.median([float(r[c]) for r in by[s] if r[c] != ""]) / 1e3 for c in cols] for s in sizes}
    fig, ax = plt.subplots(figsize=(3.4, 2.9))
    bottom = [0.0] * len(sizes)
    for i, (lab, col) in enumerate(zip(labels, colors)):
        vals = [med[s][i] for s in sizes]
        ax.bar(range(len(sizes)), vals, bottom=bottom, color=col, edgecolor="white", lw=0.5, width=0.7, label=lab)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, t in enumerate(bottom):
        ax.text(i, t + 0.03, f"{t:.1f}", ha="center", va="bottom", fontsize=6.5)
    ax.set_xticks(range(len(sizes)))
    ax.set_xticklabels([TICK[s] for s in sizes], rotation=45, ha="right")
    ax.set_xlabel("Size (bytes)")
    ax.set_ylabel("Waking to running, traced ($\\mu$s)")
    ax.set_ylim(0, max(bottom) * 1.18)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=1, frameon=False, handlelength=1.2)
    save(fig, out, "paper_wakeup")
    plt.close(fig)


# ------------------------------------------------------------------ flame graphs
def load_folded(path):
    import collections
    counter = collections.Counter()
    for line in open(path):
        stack, _, v = line.rstrip("\n").rpartition(" ")
        counter[tuple(stack.split(";")[1:])] += int(v)
    return counter


def draw_flame(ax, root, title, min_frac=0.004):
    from matplotlib.patches import Rectangle
    rects, _ = fg.layout(root)
    total = root.total or 1
    rects = [r for r in rects if r[1] / total >= min_frac]
    maxd = max(r[2] for r in rects)
    for x, w, d, n in rects:
        fill = "#e5e8e8" if n.name == "all" else fg.color(n.name)
        ax.add_patch(Rectangle((x / total, d), w / total, 0.94, facecolor=fill, edgecolor="white", lw=0.4))
        chars = int(w / total * 3.4 * 72 / (0.6 * 5.6)) - 1
        if chars >= 4 and n.name != "all":
            txt = n.name if len(n.name) <= chars else n.name[:chars - 1] + "…"
            ax.text(x / total + 0.004, d + 0.46, txt, fontsize=5.6, va="center", ha="left",
                    color="white" if fill in (fg.BLOCKED_COLOR, "#1f5fbf", "#8e44ad", "#c0392b") else "black")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, maxd + 0.4)
    ax.axis("off")
    ax.set_title(title, fontsize=8, pad=2)
    return rects, maxd


def fig_flame(out, lat_run):
    plt = style()
    from matplotlib.patches import Rectangle
    fdir = os.path.join(lat_run, "flamegraphs")
    panels = [("4 B messages", os.path.join(fdir, "ftrace_4B.folded")),
              ("512 KB messages", os.path.join(fdir, "ftrace_512KB.folded"))]
    roots = [fg.build(load_folded(p)) for _, p in panels]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.35))
    cats, depth = [], 0
    for ax, (title, _), root in zip(axes, panels, roots):
        rects, d = draw_flame(ax, root, title)
        depth = max(depth, d)
        for c in fg.legend_items(rects):
            if c not in cats:
                cats.append(c)
    for ax in axes:
        ax.set_ylim(0, depth + 0.4)
    handles = [Rectangle((0, 0), 1, 1, facecolor=fg.cat_fill(c)) for c in cats]
    fig.legend(handles, cats, loc="lower center", ncol=4, frameon=False, fontsize=6.6, columnspacing=1.2,
               handlelength=1.1, bbox_to_anchor=(0.5, -0.02))
    fig.subplots_adjust(left=0.005, right=0.995, top=0.93, bottom=0.14, wspace=0.03)
    save(fig, out, "paper_flame")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="figures")
    ap.add_argument("--lat-run")
    ap.add_argument("--tp-run")
    a = ap.parse_args()
    root = os.path.dirname(HERE)
    lat_run = a.lat_run or newest(os.path.join(root, "results/trace/latency/*"))
    tp_run = a.tp_run or newest(os.path.join(root, "results/trace/throughput/*"))
    os.makedirs(a.out, exist_ok=True)
    print("latency run:", lat_run, "\nthroughput run:", tp_run)
    fig_breakdown(a.out, lat_run, tp_run)
    fig_wakeup(a.out, lat_run)
    fig_flame(a.out, lat_run)


if __name__ == "__main__":
    main()
