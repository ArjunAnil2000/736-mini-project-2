#!/usr/bin/env python3
"""Two compact single-column figures for the paper: fig_breakdown.png and fig_wakeup.png (+ .pdf).

    python3 -B make_figures.py            (run from ai-paper/)

fig_breakdown: share of CPU cycles per component (latency: 4 B, 64 KB, 512 KB; throughput: 4 B, 4 KB, 64 KB, 512 KB), from the
    script-made section 3a tables of the newest results/trace/{latency,throughput}/*/summary.txt
fig_wakeup: the four phases of a wakeup (waking -> running, median per message size) from wakeup/wakeup_phases.csv of the
    latency run
Reads existing results only (../results, helper code in ../scripts); nothing here is typed in by hand.
"""
import csv
import glob
import os
import statistics as st
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import make_paper_assets as mpa  # noqa: E402  (read_tables / breakdown_shares / palette, read-only)


SHORT = {"Copy": "copying", "PipeLogic": "pipe logic", "PageAlloc": "page alloc/memcg", "Locking": "locking",
         "Sched": "scheduler/wakeup", "Interrupts": "interrupts/IPIs", "Syscall": "syscall/VFS/security",
         "OtherKernel": "other kernel", "User": "user space"}


def newest(pattern):
    d = sorted(glob.glob(os.path.join(ROOT, pattern)))
    if not d:
        sys.exit(f"nothing matches {pattern}")
    return d[-1].rstrip("/")


def latency_throughput(plt):
    """Minimum one-way latency (left axis) and best throughput (right axis): same data and design as
    scripts/plot_combined.py, drawn at column size so the text stays readable at 100% width."""
    import analyze_latency as al
    import analyze_throughput as at
    lat = {r["size"]: r for r in al.summarize([al.load(f) for f in sorted(glob.glob(os.path.join(ROOT, "results/latency_run*.csv")))])}
    tp = {r["size"]: r for r in at.summarize([at.load(f) for f in sorted(glob.glob(os.path.join(ROOT, "results/throughput_run*.csv")))])}
    sizes = mpa.SIZES
    blue, green, orange, red = "#1f5fbf", "#1e8449", "#e67e22", "#c0392b"
    fig, axl = plt.subplots(figsize=(3.35, 1.85))
    axt = axl.twinx()
    x = range(len(sizes))
    (ln,) = axl.plot(x, [lat[z]["oneway"] / 1e3 for z in sizes], "o-", color=blue, lw=1.3, ms=3,
                     label="One-way latency, minimum (left axis)")
    (tn,) = axt.plot(x, [tp[z]["best"] for z in sizes], "s-", color=green, lw=1.3, ms=3,
                     label="Throughput, best (right axis)")
    axl.set_yscale("log")
    axt.set_yscale("log")
    axl.set_ylim(0.6, 400)
    axt.set_ylim(8, 300000)
    axl.set_ylabel("One-way latency ($\\mu$s)", color=blue)
    axt.set_ylabel("Throughput (MiB/s)", color=green)
    axl.tick_params(axis="y", colors=blue)
    axt.tick_params(axis="y", colors=green)
    axl.set_xticks(x)
    axl.set_xticklabels([mpa.TICK[z] for z in sizes])
    axl.set_xlabel("Size (bytes)")
    axl.grid(True, axis="y", alpha=0.3, lw=0.5)
    ib, il = sizes.index(4096), sizes.index(65536)
    axl.axvline(ib, color=orange, ls=":", lw=1.0)
    axl.axvline(il, color=red, ls=":", lw=1.0)
    tr = axl.get_xaxis_transform()
    axl.text(ib - 0.12, 0.97, "PIPE_BUF", transform=tr, rotation=90, ha="right", va="top", fontsize=5.8, color=orange)
    axl.text(il - 0.12, 0.97, "pipe capacity", transform=tr, rotation=90, ha="right", va="top", fontsize=5.8,
             color=red)
    axl.legend(handles=[ln, tn], loc="upper center", bbox_to_anchor=(0.5, -0.27), frameon=False, ncol=1,
               fontsize=6.4, labelspacing=0.25)
    return fig


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 7.5, "axes.labelsize": 8, "legend.fontsize": 6.6, "xtick.labelsize": 7,
                         "ytick.labelsize": 7, "pdf.fonttype": 42, "axes.linewidth": 0.6,
                         "font.family": "DejaVu Sans"})
    lat_run = newest("results/trace/latency/*")
    tp_run = newest("results/trace/throughput/*")
    lat = mpa.breakdown_shares(mpa.read_tables(os.path.join(lat_run, "summary.txt"))["3a"])
    tp = mpa.breakdown_shares(mpa.read_tables(os.path.join(tp_run, "summary.txt"))["3a"])

    fig_a, a = plt.subplots(figsize=(3.35, 1.75))
    fig_b, b = plt.subplots(figsize=(3.35, 1.4))

    # ---- (a) cycle breakdown
    bars = [(4, lat), (65536, lat), (524288, lat), (4, tp), (4096, tp), (65536, tp), (524288, tp)]
    xs = [0, 1, 2, 3.5, 4.5, 5.5, 6.5]
    bottom = [0.0] * len(bars)
    for comp in mpa.COMPONENTS:
        vals = [(src[s].get(comp) or 0.0) for s, src in bars]
        a.bar(xs, vals, bottom=bottom, width=0.78, color=mpa.COMP_COLOR[comp], edgecolor="white", lw=0.4,
              label=SHORT[comp])
        for x, v, bt in zip(xs, vals, bottom):
            if v >= 9:
                a.text(x, bt + v / 2, f"{v:.0f}", ha="center", va="center", fontsize=6,
                       color="white" if comp in ("Copy", "Locking", "PageAlloc", "User") else "black")
        bottom = [bt + v for bt, v in zip(bottom, vals)]
    a.set_xticks(xs)
    a.set_xticklabels([mpa.TICK[s] for s, _ in bars])
    a.set_xlabel("Size (bytes)")
    a.set_ylabel("Share of CPU cycles (%)")
    a.set_ylim(0, 100)
    a.set_xlim(-0.6, 7.1)
    a.axvline(2.75, color="#999999", lw=0.5)
    a.text(1, 101.5, "Latency", ha="center", va="bottom", fontsize=7)
    a.text(5, 101.5, "Throughput", ha="center", va="bottom", fontsize=7)
    a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.27), ncol=3, frameon=False, columnspacing=0.6,
             handlelength=0.9, handletextpad=0.3, fontsize=6)

    # ---- (b) wakeup phases (latency)
    cols = ["waking_to_ipi", "ipi_to_idle_exit", "idle_exit_to_wakeup", "wakeup_to_switch"]
    labels = ["waker: waking to IPI sent", "IPI flight + idle exit", "wakeup handled (scheduler)",
              "wakeup to context switch"]
    colors = ["#e67e22", "#c0392b", "#8e44ad", "#1f5fbf"]
    by = {}
    for r in csv.DictReader(open(os.path.join(lat_run, "wakeup", "wakeup_phases.csv"))):
        by.setdefault(int(r["size_bytes"]), []).append(r)
    sizes = []
    for s in mpa.SIZES:
        if s not in by:
            continue
        share = sum(1 for r in by[s] if r["ipi_to_idle_exit"] != "") / len(by[s])
        if share >= 0.9:
            sizes.append(s)
        else:
            print(f"wakeup panel: size {s} omitted (only {share:.0%} of its wakeups had an idle exit)")
    med = {s: [st.median([float(r[c]) for r in by[s] if r[c] != ""]) / 1e3 for c in cols] for s in sizes}
    bottom = [0.0] * len(sizes)
    for i, (lab, col) in enumerate(zip(labels, colors)):
        vals = [med[s][i] for s in sizes]
        b.bar(range(len(sizes)), vals, bottom=bottom, color=col, edgecolor="white", lw=0.4, width=0.72, label=lab)
        bottom = [bt + v for bt, v in zip(bottom, vals)]
    for i, t in enumerate(bottom):
        b.text(i, t + 0.03, f"{t:.1f}", ha="center", va="bottom", fontsize=6)
    b.set_xticks(range(len(sizes)))
    b.set_xticklabels([mpa.TICK[s] for s in sizes])
    b.set_xlabel("Size (bytes)")
    b.set_ylabel("Wake to run ($\\mu$s)")
    b.set_ylim(0, max(bottom) * 2.0)
    b.tick_params(axis="x", labelsize=6)
    b.legend(loc="upper center", ncol=1, frameon=False, handlelength=1.0, handletextpad=0.4, fontsize=5.8,
             labelspacing=0.2)

    fig_c = latency_throughput(plt)
    for fig, name in ((fig_a, "fig_breakdown"), (fig_b, "fig_wakeup"), (fig_c, "latency_throughput")):
        fig.savefig(os.path.join(HERE, name + ".png"), dpi=300, bbox_inches="tight", pad_inches=0.02)
        fig.savefig(os.path.join(HERE, name + ".pdf"), bbox_inches="tight", pad_inches=0.02)
        print("wrote", os.path.join(HERE, name + ".png"))


if __name__ == "__main__":
    main()
