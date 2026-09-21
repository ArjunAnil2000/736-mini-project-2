#!/usr/bin/env python3
"""ftrace_flamegraph.py <run_dir> [--out DIR]

Flame graphs of the KERNEL call tree under read() and write(), from the ftrace function_graph capture
(trace.dat) of a latency-trace.sh run.

How to read one: every box is a kernel function; the box above it is something it called; the WIDTH is
its share of the time (own time plus everything it called). Left-to-right order is alphabetical and has
no meaning. Look for wide boxes near the top: that is where the time is spent.

Here the "time" is the function_graph SELF time (the function's own time, callees excluded), summed over
every call, so a box's width is exactly the sum of its own and its callees' self time.

Two things to keep in mind:
  * function_graph hooks every function entry and exit, which slows the traced code several times over.
    Compare boxes with each other; do not read the microseconds as real costs.
  * A read() that has to wait sits inside schedule() until the other process wakes it. That off-CPU
    waiting is drawn as a dark 'blocked' box ("schedule"). The "_running" variants drop it, leaving only
    time the task actually ran.

The capture covers all ten message sizes in one file; each call tree is attributed to the size of the
message being sent when it started (from the sys_enter_write events in the same file). Graphs are made
for every size in params.txt cycles_sizes plus 'all' sizes together.

Writes, into <run_dir>/flamegraphs/ (or --out), per graph <name> in {all, 4B, 64KB, 512KB}:
    ftrace_<name>.svg / .pdf       blocked time included
    ftrace_<name>_running.svg      blocked (off-CPU) time removed
    ftrace_<name>.folded           the folded stacks (input for Brendan Gregg's flamegraph.pl if wanted)
and ftrace_top_frames.txt (the widest boxes as a table).
"""
import argparse
import bisect
import collections
import contextlib
import io
import os
import re
import sys
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True  # often run as root; do not leave a root-owned __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trace_summary as ts  # noqa: E402  (read-only reuse of its parsing helpers)

SYS_WRITE = re.compile(r"\s*(\S+)-(\d+)\s+\[(\d+)\]\s+\S+\s+([\d.]+): sys_enter_write:\s*(.*)$")
BLOCKED = {"schedule"}  # frames whose SELF time is off-CPU waiting
BLOCKED_COLOR = "#5d6d7e"
# same palette/order as the cycle breakdown chart (trace_summary.chart_breakdown)
PALETTE = ["#1f5fbf", "#e67e22", "#8e44ad", "#c0392b", "#16a085", "#7f8c8d", "#f1c40f", "#bdc3c7"]
CAT_COLOR = dict(zip([c for c, _ in ts.CATEGORIES] + [ts.OTHER_CAT], PALETTE))
FRAME_H = 18
SVG_W = 1300


def color(name):
    if name == "all":
        return "#e5e8e8"
    if name in BLOCKED:
        return BLOCKED_COLOR
    return CAT_COLOR.get(ts.categorize("k", name), "#bdc3c7")


# ------------------------------------------------------------------ parsing
def parse(report_text, sizes):
    """-> ({window label: Counter{stack tuple: self_ns}}, n_roots). Windows: each size, in order."""
    starts, seen = [], set()
    trees = []                                      # (t_root, stack tuple, self_ns)
    stacks = collections.defaultdict(list)          # pid -> open frames [indent, name, child_us]
    t_root = {}
    for line in report_text.splitlines():
        m = SYS_WRITE.match(line)
        if m:
            comm, _, _, t, args = m.groups()
            fd, cnt = ts.field(args, "fd"), ts.field(args, "count")
            if comm == ts.BENCH_COMM and fd is not None and fd >= 3 and cnt in sizes and cnt not in seen:
                seen.add(cnt)
                starts.append((float(t), cnt))
            continue
        m = ts.FG.match(line)
        if not m:
            continue
        _, pid, _, t, kind, val, unit, indent, rest = m.groups()
        ind = len(indent)
        dur = float(val) * ts.UNIT[unit] if val else None
        st_ = stacks[pid]
        if kind == "entry":
            name = rest.split("(")[0].strip()
            if rest.rstrip().endswith("{"):
                if not st_:
                    t_root[pid] = float(t)
                st_.append([ind, name, 0.0])
            elif dur is not None:                    # a leaf call carries its own duration
                if not st_:
                    t_root[pid] = float(t)
                trees.append((t_root[pid], tuple(f[1] for f in st_) + (name,), dur))
                if st_:
                    st_[-1][2] += dur
        elif rest.strip().startswith("}") and dur is not None:
            while st_ and st_[-1][0] > ind:          # calls whose exit was never seen
                st_.pop()
            if st_ and st_[-1][0] == ind:
                names = tuple(f[1] for f in st_)
                kids = st_[-1][2]
                st_.pop()
                trees.append((t_root[pid], names, max(dur - kids, 0.0)))
                if st_:
                    st_[-1][2] += dur
    starts.sort()
    times = [t for t, _ in starts]
    folded = collections.defaultdict(collections.Counter)
    for t, names, self_us in trees:
        i = bisect.bisect_right(times, t) - 1
        label = starts[i][1] if i >= 0 else None
        ns = int(round(self_us * 1000))
        folded["all"][names] += ns
        if label is not None:
            folded[label][names] += ns
    return folded, len(trees), [s for _, s in starts]


def without_blocked(counter):
    return collections.Counter({k: v for k, v in counter.items() if k[-1] not in BLOCKED})


# ------------------------------------------------------------------ tree + layout
class Node:
    __slots__ = ("name", "self_v", "kids", "total")

    def __init__(self, name):
        self.name, self.self_v, self.kids, self.total = name, 0, {}, 0


def build(counter):
    root = Node("all")
    for stack, v in counter.items():
        node = root
        for fr in stack:
            node = node.kids.setdefault(fr, Node(fr))
        node.self_v += v
    def tot(n):
        n.total = n.self_v + sum(tot(c) for c in n.kids.values())
        return n.total
    tot(root)
    return root


def layout(root):
    """-> [(x0, width, depth, node)] in value units, plus max depth."""
    out, maxd = [], 0
    def rec(n, x, d):
        nonlocal maxd
        out.append((x, n.total, d, n))
        maxd = max(maxd, d)
        cx = x
        for name in sorted(n.kids):
            c = n.kids[name]
            rec(c, cx, d + 1)
            cx += c.total
    rec(root, 0, 0)
    return out, maxd


def write_folded(path, counter):
    with open(path, "w") as f:
        for stack, v in sorted(counter.items()):
            if v > 0:
                f.write("all;" + ";".join(stack) + f" {v}\n")


# ------------------------------------------------------------------ renderers
def legend_items(rects):
    cats = []
    for _, _, _, n in rects:
        if n.name in BLOCKED:
            c = "blocked / waiting (schedule)"
        elif n.name == "all":
            continue
        else:
            c = ts.categorize("k", n.name)
        if c not in cats:
            cats.append(c)
    return cats


def cat_fill(cat):
    if cat.startswith("blocked"):
        return BLOCKED_COLOR
    return CAT_COLOR.get(cat, "#bdc3c7")


def svg(path, root, title):
    rects, maxd = layout(root)
    total = root.total or 1
    scale = (SVG_W - 20) / total
    top, legend_h = 46, 26
    h = top + (maxd + 1) * FRAME_H + legend_h + 24
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{h}" viewBox="0 0 {SVG_W} {h}">',
         '<style>text{font-family:Verdana,DejaVu Sans,sans-serif} .f{font-size:11px} .f:hover{stroke:#000;stroke-width:1}</style>',
         f'<rect width="{SVG_W}" height="{h}" fill="#ffffff"/>',
         f'<text x="{SVG_W / 2}" y="20" text-anchor="middle" font-size="15">{escape(title)}</text>',
         f'<text x="{SVG_W / 2}" y="37" text-anchor="middle" font-size="11" fill="#555">'
         'width = share of function_graph self time (inflated by tracing: compare boxes, not microseconds)</text>']
    for x, w, d, n in rects:
        px, pw = 10 + x * scale, w * scale
        if pw < 0.3:
            continue
        y = h - legend_h - 24 - (d + 1) * FRAME_H
        label = n.name
        chars = int((pw - 6) / 6.6)
        text = "" if chars < 3 else (label if len(label) <= chars else label[:chars - 1] + "…")
        tip = f"{n.name}: {n.total / 1000:,.1f} us total, {n.self_v / 1000:,.1f} us self ({100 * n.total / total:.1f}%)"
        fill = "#e5e8e8" if n.name == "all" else color(n.name)
        tcol = "#ffffff" if fill in (BLOCKED_COLOR, "#1f5fbf", "#8e44ad", "#c0392b", "#2c3e50") else "#000000"
        o.append(f'<g class="f"><title>{escape(tip)}</title>'
                 f'<rect x="{px:.2f}" y="{y}" width="{pw:.2f}" height="{FRAME_H - 1}" fill="{fill}" '
                 f'stroke="#ffffff" stroke-width="0.5"/>'
                 + (f'<text x="{px + 3:.2f}" y="{y + 12.5}" fill="{tcol}">{escape(text)}</text>' if text else "")
                 + '</g>')
    lx = 10
    for cat in legend_items(rects):
        o.append(f'<rect x="{lx}" y="{h - 34}" width="12" height="12" fill="{cat_fill(cat)}"/>'
                 f'<text x="{lx + 16}" y="{h - 24}" font-size="11">{escape(cat)}</text>')
        lx += 22 + 6.4 * len(cat)
    o.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(o))


def pdf(path, root, title):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        print("!! matplotlib is not importable (as root it lives only in the user's site-packages): "
              f"{os.path.basename(path)} NOT written (the .svg is)", file=sys.stderr)
        return False
    rects, maxd = layout(root)
    total = root.total or 1
    fig_w = 12.0
    fig, ax = plt.subplots(figsize=(fig_w, 0.24 * (maxd + 1) + 1.6))
    for x, w, d, n in rects:
        if w / total < 0.0004:
            continue
        fill = "#e5e8e8" if n.name == "all" else color(n.name)
        ax.add_patch(Rectangle((x / total, d), w / total, 0.94, facecolor=fill, edgecolor="white", lw=0.4))
        chars = int(w / total * fig_w * 72 / (0.62 * 6.5)) - 1
        if chars >= 3:
            txt = n.name if len(n.name) <= chars else n.name[:chars - 1] + "…"
            ax.text(x / total + 0.002, d + 0.45, txt, fontsize=6.5, va="center", ha="left",
                    color="white" if fill in (BLOCKED_COLOR, "#1f5fbf", "#8e44ad", "#c0392b") else "black")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, maxd + 1.1)
    ax.axis("off")
    ax.set_title(title, fontsize=10)
    handles = [Rectangle((0, 0), 1, 1, facecolor=cat_fill(c)) for c in legend_items(rects)]
    ax.legend(handles, legend_items(rects), loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=4,
              fontsize=7, frameon=False)
    fig.text(0.5, 0.005, "width = share of function_graph self time (inflated by tracing: compare boxes, not microseconds)",
             ha="center", fontsize=7, color="#555555")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return True


# ------------------------------------------------------------------ top frames table
def top_frames(counter, n=12):
    self_t = collections.Counter()
    for stack, v in counter.items():
        self_t[stack[-1]] += v
    total = sum(self_t.values()) or 1
    return [[name, f"{v / 1000:,.1f}", f"{100 * v / total:.1f}%"] for name, v in self_t.most_common(n)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--out", help="output directory (default: <run_dir>/flamegraphs)")
    a = ap.parse_args()
    d = a.run_dir.rstrip("/")
    path = f"{d}/trace.dat"
    if not os.path.exists(path):
        sys.exit(f"{path} not found (is this a latency-trace.sh run directory?)")
    out = a.out or f"{d}/flamegraphs"
    os.makedirs(out, exist_ok=True)
    p = ts.read_params(d)
    sizes = {int(x) for x in p.get("sizes", "").split()}
    wanted = [int(x) for x in p.get("cycles_sizes", "4 65536 524288").split()]
    text = ts.run(["trace-cmd", "report", "-i", path])
    folded, n_calls, found_sizes = parse(text, sizes)
    if not folded.get("all"):
        sys.exit("no function_graph call trees found in trace.dat")
    missing = [s for s in wanted if s not in folded]
    made, tables = [], io.StringIO()
    for label in ["all"] + [s for s in wanted if s in folded]:
        name = "all" if label == "all" else ts.human_size(label).replace(" ", "")
        what = "all message sizes" if label == "all" else f"{ts.human_size(label)} messages"
        for variant, counter in (("", folded[label]), ("_running", without_blocked(folded[label]))):
            if not counter:
                continue
            root = build(counter)
            note = "" if not variant else ", blocked (off-CPU) time removed"
            title = f"ftrace function_graph: kernel time under read() and write(), {what}{note}"
            svg(f"{out}/ftrace_{name}{variant}.svg", root, title)
            if not variant:
                pdf(f"{out}/ftrace_{name}.pdf", root, title)
                write_folded(f"{out}/ftrace_{name}.folded", counter)
        with contextlib.redirect_stdout(tables):
            ts.table(["function", "self us", "% of time"], top_frames(folded[label]),
                     f"{what}: widest boxes by self time (blocked time included)")
            ts.table(["function", "self us", "% of time"], top_frames(without_blocked(folded[label])),
                     f"{what}: widest boxes by self time, blocked (off-CPU) time removed")
            print()
        made.append(name)
    header = ("ftrace flame graph inputs: %d call trees from trace.dat; times are function_graph self time\n"
              "(inflated by tracing: compare rows with each other, not against real microseconds)\n" % n_calls)
    open(f"{out}/ftrace_top_frames.txt", "w").write(header + tables.getvalue())
    print(f"ftrace flame graphs ({', '.join(made)}) -> {out}/"
          + (f"   [no call trees found for sizes: {missing}]" if missing else ""))


if __name__ == "__main__":
    main()
