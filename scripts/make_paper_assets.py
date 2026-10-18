#!/usr/bin/env python3
"""make_paper_assets.py - generate every number, table and figure the paper uses, from the data files.

Nothing that the paper quotes is typed in by hand: the text refers to LaTeX macros (paper/generated/
numbers.tex), tables (paper/generated/tab_*.tex) and figures (paper/figures/fig_*.pdf), all written here.
If the data changes (a re-run), run this script again and recompile.

Inputs (all produced by the experiment scripts):
  results/latency_run*.csv, results/throughput_run*.csv     clean, frequency-pinned runner data
  results/trace/latency/<newest>/, results/trace/throughput/<newest>/
        summary.txt                       (tables printed by trace_summary*.py)
        wakeup/wakeup_phases.csv          (printed by wakeup_timeline.py)
  results/clockres.txt                      (optional: `sudo make clockres.run > results/clockres.txt`)

Usage:  python3 -B scripts/make_paper_assets.py
"""
import collections
import glob
import os
import re
import statistics as st
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyze_latency as al       # noqa: E402
import analyze_throughput as at    # noqa: E402

ROOT = os.path.dirname(HERE)
PAPER = os.path.join(ROOT, "paper")
GEN = os.path.join(PAPER, "generated")
FIG = os.path.join(PAPER, "figures")
SIZES = [4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 524288]
SIZE_NAME = {4: "Four", 16: "Sixteen", 64: "SixtyFour", 256: "TwoFiftySix", 1024: "OneK", 4096: "FourK",
             16384: "SixteenK", 65536: "SixtyFourK", 262144: "TwoFiftySixK", 524288: "FiveTwelveK"}
LABEL_TO_SIZE = {"4 B": 4, "16 B": 16, "64 B": 64, "256 B": 256, "1 KB": 1024, "4 KB": 4096, "16 KB": 16384,
                 "64 KB": 65536, "256 KB": 262144, "512 KB": 524288}
TICK = {4: "4", 16: "16", 64: "64", 256: "256", 1024: "1K", 4096: "4K", 16384: "16K", 65536: "64K",
        262144: "256K", 524288: "512K"}
PIPE_BUF, PIPE_LEN = 4096, 65536

MACROS = collections.OrderedDict()


def letters(s):
    """A LaTeX-safe macro fragment: letters only, words capitalised."""
    s = re.sub(r"%", " Pct ", s)
    s = re.sub(r"/", " ", s)
    return "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z]+", s))


def mac(name, value):
    assert re.fullmatch(r"[A-Za-z]+", name), name
    MACROS[name] = str(value)


def newest(pattern):
    d = sorted(glob.glob(os.path.join(ROOT, pattern)))
    return d[-1].rstrip("/") if d else None


def to_size(cell):
    """A size cell as printed by the summaries: '4 B', '1 KB' (labels) or '4', '1024' (bytes) -> bytes, else None."""
    if cell in LABEL_TO_SIZE:
        return LABEL_TO_SIZE[cell]
    return int(cell) if cell.isdigit() and int(cell) in SIZE_NAME else None


def group(v):
    """'23415' -> '23{,}415' (LaTeX-safe thousands separator); anything else unchanged."""
    v = str(v).replace(",", "").rstrip("%")
    m = re.fullmatch(r"(\d{4,})(\.\d+)?", v)
    return f"{int(m.group(1)):,}".replace(",", "{,}") + (m.group(2) or "") if m else v


def size_label(size):
    return f"{size // 1024} KB" if size >= 1024 else f"{size} B"


def num(s):
    """'1,981.4' -> 1981.4 ; anything else -> None"""
    try:
        return float(str(s).replace(",", "").rstrip("%"))
    except ValueError:
        return None


# ------------------------------------------------------------------ parsing the script-made summaries
def read_tables(path):
    """{'1a': (header cells, [row cells...])} from a trace_summary*.py output. Cells split on 2+ spaces."""
    lines = open(path).read().splitlines()
    out = {}
    for i, l in enumerate(lines):
        m = re.match(r"^(\d[a-z])\. ", l)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not re.match(r"^\s+-{3,}", lines[j]):
            j += 1
        if j >= len(lines) or j - 1 <= i:
            continue
        header = re.split(r"\s{2,}", lines[j - 1].strip())
        rows = []
        for r in lines[j + 1:]:
            if not r.strip():
                break
            cells = re.split(r"\s{2,}", r.strip())
            first = cells[0]
            if to_size(first) is None and not re.match(r"^[a-zA-Z]", first):
                break
            rows.append(cells)
        out[m.group(1)] = (header, rows)
    return out


def emit_size_table(prefix, table, colmap):
    """One macro per (column, size): \\<prefix><Col><Size>. colmap: header text -> macro fragment."""
    header, rows = table
    for cells in rows:
        size = to_size(cells[0])
        if size is None:
            continue
        for h, val in zip(header[1:], cells[1:]):
            if h in colmap:
                mac(f"{prefix}{colmap[h]}{SIZE_NAME[size]}", group(val))


def emit_breakdown(prefix, table):
    """Component x size table (3a): \\bd<Lat|Tp><Comp><Pct|Kcyc|CycKiB><Size> and the total row."""
    header, rows = table
    cols = []
    for h in header[1:]:
        m = re.match(r"^(\d+ (?:B|KB)) (%|kcyc|cyc/KiB)$", h)
        cols.append((LABEL_TO_SIZE[m.group(1)], {"%": "Pct", "kcyc": "Kcyc", "cyc/KiB": "CycKiB"}[m.group(2)]))
    for cells in rows:
        label = cells[0]
        if label.startswith("TOTAL"):
            metric_cols = [c for c in cols if c[1] == "CycKiB"]
            for (size, metric), val in zip(metric_cols, cells[1:]):
                mac(f"{prefix}Total{metric}{SIZE_NAME[size]}", group(val))
            continue
        comp = {"copying data (to/from pipe)": "Copy", "pipe read/write logic": "PipeLogic",
                "page alloc/free + memcg": "PageAlloc", "locking": "Locking",
                "scheduler, wakeup, idle": "Sched", "interrupts, IPIs, timers": "Interrupts",
                "syscall entry/exit, VFS, security": "Syscall", "other kernel": "OtherKernel",
                "user space (benchmark + libc)": "User"}[label]
        for (size, metric), val in zip(cols, cells[1:]):
            mac(f"{prefix}{comp}{metric}{SIZE_NAME[size]}", group(val))


COMPONENTS = ["Copy", "PipeLogic", "PageAlloc", "Locking", "Sched", "Interrupts", "Syscall", "OtherKernel", "User"]
COMP_LABEL = {"Copy": "copying data", "PipeLogic": "pipe read/write logic", "PageAlloc": "page alloc/free + memcg",
              "Locking": "locking", "Sched": "scheduler, wakeup, idle", "Interrupts": "interrupts, IPIs, timers",
              "Syscall": "syscall entry/exit, VFS, security", "OtherKernel": "other kernel", "User": "user space"}
COMP_COLOR = {"Copy": "#1f5fbf", "PipeLogic": "#e67e22", "PageAlloc": "#8e44ad", "Locking": "#c0392b",
              "Sched": "#16a085", "Interrupts": "#7f8c8d", "Syscall": "#f1c40f", "OtherKernel": "#bdc3c7",
              "User": "#2c3e50"}


def breakdown_shares(table):
    """{size: {comp: percent}} from a parsed 3a table (columns alternate: percent, then cycles, per size)."""
    header, rows = table
    order = []
    for h in header[1:]:
        m = re.match(r"^(\d+ (?:B|KB)) %$", h)
        if m:
            order.append(LABEL_TO_SIZE[m.group(1)])
    lab2comp = {"copying data (to/from pipe)": "Copy", "pipe read/write logic": "PipeLogic",
                "page alloc/free + memcg": "PageAlloc", "locking": "Locking", "scheduler, wakeup, idle": "Sched",
                "interrupts, IPIs, timers": "Interrupts", "syscall entry/exit, VFS, security": "Syscall",
                "other kernel": "OtherKernel", "user space (benchmark + libc)": "User"}
    out = {s: {} for s in order}
    for cells in rows:
        comp = lab2comp.get(cells[0])
        if comp:
            for n, s in enumerate(order):
                out[s][comp] = num(cells[1 + 2 * n])
    return out


# ------------------------------------------------------------------ latency / throughput numbers
def runner_numbers():
    lat_files = sorted(glob.glob(os.path.join(ROOT, "results", "latency_run*.csv")))
    tp_files = sorted(glob.glob(os.path.join(ROOT, "results", "throughput_run*.csv")))
    if not lat_files or not tp_files:
        sys.exit("need results/latency_run*.csv and results/throughput_run*.csv")
    lat = al.summarize([al.load(p) for p in lat_files])
    tp = at.summarize([at.load(p) for p in tp_files])
    mac("nLatRuns", len(lat_files))
    mac("nTpRuns", len(tp_files))
    mac("latItersPerRun", lat[0]["n"] // len(lat_files))
    for r in lat:
        n = SIZE_NAME[r["size"]]
        mac(f"latRtt{n}", f"{r['rtt_min'] / 1e3:.2f}")
        mac(f"latOneWay{n}", f"{r['oneway'] / 1e3:.2f}")
        mac(f"latMedRtt{n}", f"{r['median'] / 1e3:.2f}")
        mac(f"latPone{n}", f"{r['p1'] / 1e3:.2f}")
        mac(f"latSpread{n}", f"{r['spread']:.1f}")
    for r in tp:
        n = SIZE_NAME[r["size"]]
        mac(f"tpBest{n}", f"{r['best']:,.0f}".replace(",", "{,}"))
        mac(f"tpBestGB{n}", f"{r['best'] * at.MIB / 1e9:.2f}")
        mac(f"tpMed{n}", f"{r['median']:,.0f}".replace(",", "{,}"))
        mac(f"tpSpread{n}", f"{r['spread']:.1f}")
    best = {r["size"]: r["best"] for r in tp}
    peak_size = max(best, key=best.get)
    plateau = st.mean(best[s] for s in (65536, 262144, 524288))
    mac("tpPeakSize", size_label(peak_size))
    mac("tpPeakBest", f"{best[peak_size]:,.0f}".replace(",", "{,}"))
    mac("tpPeakGB", f"{best[peak_size] * at.MIB / 1e9:.2f}")
    mac("tpPlateauBest", f"{plateau:,.0f}".replace(",", "{,}"))
    mac("tpPlateauGB", f"{plateau * at.MIB / 1e9:.2f}")
    mac("tpDropPct", f"{100 * (1 - plateau / best[peak_size]):.0f}")
    return lat, tp


# ------------------------------------------------------------------ wakeup phases
def wakeup_numbers(d, prefix):
    import csv
    path = os.path.join(d, "wakeup", "wakeup_phases.csv")
    if not os.path.exists(path):
        return {}
    by = collections.defaultdict(list)
    for r in csv.DictReader(open(path)):
        by[int(r["size_bytes"])].append(r)
    cols = ["waking_to_ipi", "ipi_to_idle_exit", "idle_exit_to_wakeup", "wakeup_to_switch", "switch_to_user"]
    med = {}
    for s, rows in by.items():
        med[s] = {}
        for c in cols + ["waking_to_running_ns"]:
            v = [float(r[c]) for r in rows if r.get(c) not in (None, "")]
            med[s][c] = st.median(v) if v else None
    mac(f"{prefix}HandOffs", sum(len(v) for v in by.values()))
    runs = [med[s]["waking_to_running_ns"] for s in sorted(med) if med[s]["waking_to_running_ns"] and len(by[s]) >= 30]
    if runs:
        mac(f"{prefix}RunMin", f"{min(runs) / 1e3:.2f}")
        mac(f"{prefix}RunMax", f"{max(runs) / 1e3:.2f}")
    for s in SIZES:
        if s in med and med[s]["waking_to_running_ns"]:
            mac(f"{prefix}Run{SIZE_NAME[s]}", f"{med[s]['waking_to_running_ns'] / 1e3:.2f}")
            mac(f"{prefix}N{SIZE_NAME[s]}", group(len(by[s])))
    pooled = {c: st.median(float(r[c]) for s, rows in by.items() if len(rows) >= 30 for r in rows
                           if r.get(c) not in (None, "")) for c in cols[:4]}
    for c, name in zip(cols[:4], ["Waker", "Ipi", "Handle", "Switch"]):
        mac(f"{prefix}Phase{name}", f"{pooled[c] / 1e3:.2f}")
    return {"by": by, "med": med, "cols": cols}


def source_constants(ld):
    """Configuration constants, read from the source and from the trace params, never typed in."""
    def grab(path, name):
        m = re.search(rf"#define\s+{name}\s+(.+)", open(os.path.join(ROOT, path)).read())
        return m.group(1).strip()
    mac("cfgLatWarmup", grab("src/latency.c", "DEFAULT_WARMUP"))
    mac("cfgLatIters", group(grab("src/latency.c", "DEFAULT_ITERATIONS")))
    mac("cfgTpRepeats", grab("src/throughput.c", "DEFAULT_REPEATS"))
    mac("cfgTpMinChunks", grab("src/throughput.c", "MIN_ITERS"))
    mac("cfgTpMaxChunks", group(grab("src/throughput.c", "MAX_ITERS")))
    nums = [int(x) for x in re.findall(r"\d+", grab("src/throughput.c", "TARGET_BYTES"))]
    mac("cfgTpTargetMiB", nums[0] * nums[1] * nums[2] // (1024 * 1024) if len(nums) == 3 else nums[0] // 2 ** 20)
    p = {}
    for line in open(os.path.join(ld, "params.txt")):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            p[k] = v
    mac("cfgTraceWarmup", p["trace_warmup"])
    mac("cfgTraceIters", p["trace_iterations"])
    mac("cfgStatIters", group(p["stat_iterations"]))
    mac("cfgFreqGHz", f"{int(p['freq_khz']) / 1e6:.1f}")


def derived_numbers(lat, tp, lt, tt):
    """Ratios and model checks computed from the numbers above."""
    L = {r["size"]: r for r in lat}
    T = {r["size"]: r for r in tp}
    mac("latRatioBigOverSixtyFourK", f"{L[524288]['oneway'] / L[65536]['oneway']:.1f}")
    for s in (4, 4096, 524288):
        mac(f"latNoise{SIZE_NAME[s]}", f"{100 * (L[s]['median'] / L[s]['rtt_min'] - 1):.0f}")
    mac("latStepFourK", f"{L[4096]['oneway'] / L[1024]['oneway']:.1f}")
    for s in (4, 16):
        mac(f"tpMoved{SIZE_NAME[s]}", f"{T[s]['total'] / at.MIB:.1f}")
    # throughput model: cores busy x core clock / cycles per KiB, in MiB/s
    hdr, rows = tt["6a"]
    freq = 4.0e9
    for cells in rows:
        s = to_size(cells[0])
        if s in (16384, 65536):
            cores = float(cells[hdr.index("cores busy")])
            cyc = num(cells[hdr.index("cycles/KiB")])
            mac(f"tpModel{SIZE_NAME[s]}", group(f"{cores * freq / cyc / 1024:.0f}"))
    # the round-trip step at PIPE_BUF: what part is CPU work, what part is time nobody ran
    hdr, rows = lt["6a"]
    by = {to_size(c[0]): c for c in rows if to_size(c[0])}
    cpu = lambda s: num(by[s][hdr.index("CPU work us")])
    idle = lambda s: num(by[s][hdr.index("both idle us")])
    mac("latStepCpu", f"{cpu(4096) - cpu(1024):.2f}")
    mac("latStepIdle", f"{idle(4096) - idle(1024):.2f}")
    mac("latStepCpuShare", f"{100 * (cpu(4096) - cpu(1024)) / ((cpu(4096) - cpu(1024)) + (idle(4096) - idle(1024))):.0f}")


def summary_extras(ld):
    """Single values that only exist in the printed summary (top functions)."""
    t = open(os.path.join(ld, "summary.txt")).read()
    block = re.search(r"3c\. top functions, 4 B messages.*?\n\n", t, re.S)
    m = re.search(r"([\d.]+)%\s+\[k\] cpupri_set", block.group(0)) if block else None
    mac("latCpupri", m.group(1) if m else "0")


def folded_shares(d, prefix):
    """Self-time shares (%) of a few frame families from flamegraphs/ftrace_all.folded."""
    path = os.path.join(d, "flamegraphs", "ftrace_all.folded")
    if not os.path.exists(path):
        return
    tot = sched = sec = spin = 0
    for line in open(path):
        stack, _, v = line.rpartition(" ")
        v = int(v)
        leaf = stack.split(";")[-1]
        tot += v
        if leaf == "schedule":
            sched += v
        elif re.search(r"avc_|selinux|security_|__file_has_perm|inode_has_perm|bpf_lsm", leaf):
            sec += v
        elif leaf == "mutex_spin_on_owner":
            spin += v
    mac(f"{prefix}Sched", f"{100 * sched / tot:.0f}")
    mac(f"{prefix}Security", f"{100 * sec / tot:.0f}")
    mac(f"{prefix}MutexSpin", f"{100 * spin / tot:.0f}")


# ------------------------------------------------------------------ clock resolution (optional file)
def clock_numbers():
    path = os.path.join(ROOT, "results", "clockres.txt")
    keys = ["ClkMono", "ClkCoarse", "ClkCpu", "ClkGtod", "ClkRdtscCycles", "ClkTscGHz", "ClkRdtscNs",
            "ClkSleepMono", "ClkSleepGtod", "ClkSleepRdtsc", "ClkSleepCoarse"]
    pending = r"\textbf{[pending: run clockres]}"
    if not os.path.exists(path):
        for k in keys:
            mac(k, pending)
        mac("clkAvailable", "0")
        return
    t = open(path).read()

    def grab(pat, fmt=lambda x: x):
        m = re.search(pat, t)
        return fmt(m.group(1)) if m else pending

    mac("ClkMono", grab(r"clock_gettime\(CLOCK_MONOTONIC\):\s+([\d.]+) ns", lambda x: f"{float(x):.0f}"))
    mac("ClkCoarse", grab(r"CLOCK_MONOTONIC_COARSE\):\s+([\d.]+) ns", lambda x: f"{float(x) / 1e6:.1f}"))
    mac("ClkCpu", grab(r"CLOCK_PROCESS_CPUTIME_ID\):\s+([\d.]+) ns", lambda x: f"{float(x):.0f}"))
    mac("ClkGtod", grab(r"gettimeofday:\s+([\d.]+) ns", lambda x: f"{float(x):.0f}"))
    mac("ClkRdtscCycles", grab(r"rdtsc_cycles:\s+(\d+) cycles"))
    mac("ClkTscGHz", grab(r"calibrated TSC frequency:\s+([\d.]+) GHz"))
    mac("ClkRdtscNs", grab(r"rdtsc resolution \(calibrated\):\s+([\d.]+) ns", lambda x: f"{float(x):.1f}"))
    mono = re.search(r"CLOCK_MONOTONIC\) measured:\s+(\d+) ns", t)
    for key, pat in (("ClkSleepGtod", r"gettimeofday measured:\s+(\d+) ns"),
                     ("ClkSleepRdtsc", r"rdtsc \(calibrated\) measured:\s+([\d.]+) ns"),
                     ("ClkSleepCoarse", r"CLOCK_MONOTONIC_COARSE\) measured:\s+(\d+) ns")):
        m = re.search(pat, t)
        mac(key, f"{abs(float(m.group(1)) - float(mono.group(1))) / 1e3:.0f}" if m and mono else pending)
    mac("ClkSleepMono", f"{float(mono.group(1)) / 1e9:.6f}" if mono else pending)
    mac("clkAvailable", "1")


# ------------------------------------------------------------------ LaTeX tables
def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


def tex_num(s):
    return s.replace(",", "{,}")


def table_latency(lat):
    rows = []
    for r in lat:
        lucky = r["p1"] > 1.15 * r["rtt_min"]
        rows.append(f"{TICK[r['size']]} & {r['rtt_min'] / 1e3:.2f}{'$^{\\ast}$' if lucky else ''} & "
                    f"{r['oneway'] / 1e3:.2f} & {r['median'] / 1e3:.2f} & {r['spread']:.1f} \\\\")
    write(f"{GEN}/tab_latency.tex", "\\begin{tabular}{@{}rrrrr@{}}\n\\toprule\n"
          "Size (B) & Min RTT ($\\mu$s) & One-way ($\\mu$s) & Median RTT ($\\mu$s) & Spread (\\%) \\\\\n\\midrule\n"
          + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def table_throughput(tp):
    rows = []
    for r in tp:
        star = "$^{\\ast}$" if r["lucky"] else ""
        rows.append(f"{TICK[r['size']]} & {tex_num(f'{r['best']:,.1f}')}{star} & "
                    f"{r['best'] * at.MIB / 1e9:.3f} & {tex_num(f'{r['median']:,.1f}')} & {r['spread']:.1f} \\\\")
    write(f"{GEN}/tab_throughput.tex", "\\begin{tabular}{@{}rrrrr@{}}\n\\toprule\n"
          "Chunk (B) & Best (MiB/s) & Best (GB/s) & Median (MiB/s) & Spread (\\%) \\\\\n\\midrule\n"
          + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def table_lat_where(t1a, t6a):
    ctx = {to_size(c[0]): c for c in t1a[1] if to_size(c[0])}
    ih = t1a[0]
    wh = t6a[0]
    wrow = {to_size(c[0]): c for c in t6a[1] if to_size(c[0])}
    rows = []
    for s in SIZES:
        w = wrow[s]
        rows.append(f"{TICK[s]} & {w[wh.index('median RTT us')]} & {w[wh.index('CPU work us')]} & "
                    f"{w[wh.index('both idle us')]} & {w[wh.index('idle %')].rstrip('%')} & "
                    f"{ctx[s][ih.index('ctx-sw')]} \\\\")
    write(f"{GEN}/tab_lat_where.tex", "\\begin{tabular}{@{}rrrrrr@{}}\n\\toprule\n"
          "Size (B) & RTT ($\\mu$s) & CPU work ($\\mu$s) & Both idle ($\\mu$s) & Idle (\\%) & Ctx.\\ sw.\\ / RTT \\\\\n"
          "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def table_tp_where(t1a, t6a):
    ih, wh = t1a[0], t6a[0]
    c1 = {to_size(c[0]): c for c in t1a[1] if to_size(c[0])}
    c6 = {to_size(c[0]): c for c in t6a[1] if to_size(c[0])}
    rows = []
    for s in SIZES:
        rows.append(f"{TICK[s]} & {c1[s][ih.index('ctx-sw/chunk')]} & {tex_num(c6[s][wh.index('cycles/KiB')])} & "
                    f"{c6[s][wh.index('cores busy')]} \\\\")
    write(f"{GEN}/tab_tp_where.tex", "\\begin{tabular}{@{}rrrr@{}}\n\\toprule\n"
          "Chunk (B) & Ctx.\\ sw.\\ / chunk & Cycles / KiB & Cores busy \\\\\n\\midrule\n"
          + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


# ------------------------------------------------------------------ figures
def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.labelsize": 8.5, "legend.fontsize": 7.5, "xtick.labelsize": 7.5,
                         "ytick.labelsize": 7.5, "pdf.fonttype": 42, "axes.linewidth": 0.7,
                         "font.family": "DejaVu Sans"})
    return plt


def size_axes(ax, ylabel, buf_at=(0.55, "bottom"), cap_at=(0.03, "bottom")):
    """buf_at / cap_at: (axes-fraction y, va) of the PIPE_BUF / pipe-capacity labels, chosen per plot so that
    they sit where there is no curve or legend."""
    ax.set_xticks(range(len(SIZES)))
    ax.set_xticklabels([TICK[s] for s in SIZES], rotation=45, ha="right")
    ax.set_xlabel("Size (bytes)")
    ax.set_ylabel(ylabel)
    ax.grid(True, axis="y", alpha=0.3, lw=0.5)
    ib, il = SIZES.index(PIPE_BUF), SIZES.index(PIPE_LEN)
    ax.axvline(ib, color="#e67e22", ls=":", lw=1.1)
    ax.axvline(il, color="#c0392b", ls=":", lw=1.1)
    ax.text(ib - 0.12, buf_at[0], "PIPE_BUF", transform=ax.get_xaxis_transform(), rotation=90, ha="right",
            va=buf_at[1], fontsize=6.5, color="#e67e22")
    ax.text(il - 0.12, cap_at[0], "pipe capacity", transform=ax.get_xaxis_transform(), rotation=90, ha="right",
            va=cap_at[1], fontsize=6.5, color="#c0392b")


def fig_latency(lat):
    plt = style()
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    x = range(len(SIZES))
    ax.plot(x, [r["oneway"] / 1e3 for r in lat], "o-", color="#1f5fbf", lw=1.5, ms=3.5, label="minimum (reported)")
    ax.plot(x, [r["median"] / 2e3 for r in lat], "s--", color="#7f8c8d", lw=1.0, ms=2.8, label="median")
    ax.set_yscale("log")
    size_axes(ax, "One-way latency ($\\mu$s)", buf_at=(0.58, "bottom"), cap_at=(0.03, "bottom"))
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout(pad=0.3)
    fig.savefig(f"{FIG}/fig_latency.pdf")
    plt.close(fig)


def fig_throughput(tp):
    plt = style()
    fig, ax = plt.subplots(figsize=(3.3, 2.3))
    x = range(len(SIZES))
    ax.plot(x, [r["best"] for r in tp], "o-", color="#1f5fbf", lw=1.5, ms=3.5, label="best (reported)")
    ax.plot(x, [r["median"] for r in tp], "s--", color="#7f8c8d", lw=1.0, ms=2.8, label="median")
    ax.set_yscale("log")
    size_axes(ax, "Throughput (MiB/s)", buf_at=(0.30, "bottom"), cap_at=(0.30, "bottom"))
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout(pad=0.3)
    fig.savefig(f"{FIG}/fig_throughput.pdf")
    plt.close(fig)


def fig_latency_throughput(lat, tp):
    plt = style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.5))
    x = range(len(SIZES))

    ax = axes[0]
    ax.plot(x, [r["oneway"] / 1e3 for r in lat], "o-", color="#1f5fbf", lw=1.5, ms=3.5, label="minimum (reported)")
    ax.plot(x, [r["median"] / 2e3 for r in lat], "s--", color="#7f8c8d", lw=1.0, ms=2.8, label="median")
    ax.set_yscale("log")
    size_axes(ax, "One-way latency ($\\mu$s)", buf_at=(0.58, "bottom"), cap_at=(0.03, "bottom"))
    ax.legend(loc="upper left", frameon=False)
    ax.set_title("Latency", fontsize=8.5)

    ax = axes[1]
    ax.plot(x, [r["best"] for r in tp], "o-", color="#1f5fbf", lw=1.5, ms=3.5, label="best (reported)")
    ax.plot(x, [r["median"] for r in tp], "s--", color="#7f8c8d", lw=1.0, ms=2.8, label="median")
    ax.set_yscale("log")
    size_axes(ax, "Throughput (MiB/s)", buf_at=(0.30, "bottom"), cap_at=(0.30, "bottom"))
    ax.legend(loc="lower right", frameon=False)
    ax.set_title("Throughput", fontsize=8.5)

    fig.tight_layout(pad=0.4)
    fig.savefig(f"{FIG}/fig_latency_throughput.png", dpi=300)
    fig.savefig(f"{FIG}/fig_latency_throughput.pdf")
    plt.close(fig)


def fig_breakdown(lat_sh, tp_sh, lat_total, tp_total):
    plt = style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), gridspec_kw={"width_ratios": [3, 4]}, sharey=True)
    panels = [(axes[0], lat_sh, lat_total, "Latency", "k cycles / round trip"),
              (axes[1], tp_sh, tp_total, "Throughput", "cycles / KiB")]
    for ax, sh, total, title, unit in panels:
        sizes = sorted(sh)
        bottom = [0.0] * len(sizes)
        for comp in COMPONENTS:
            vals = [sh[s].get(comp) or 0.0 for s in sizes]
            ax.bar(range(len(sizes)), vals, bottom=bottom, color=COMP_COLOR[comp], edgecolor="white", lw=0.5,
                   width=0.62, label=COMP_LABEL[comp])
            for i, (v, b) in enumerate(zip(vals, bottom)):
                if v >= 9:
                    ax.text(i, b + v / 2, f"{v:.0f}", ha="center", va="center", fontsize=6.5,
                            color="white" if comp in ("Copy", "Locking", "PageAlloc", "User", "Sched") else "black")
            bottom = [b + v for b, v in zip(bottom, vals)]
        for i, s in enumerate(sizes):
            ax.text(i, 101.5, f"{total[s]:,.1f}" if unit.startswith("k") else f"{total[s]:,.0f}", ha="center",
                    va="bottom", fontsize=6.5)
        ax.set_xticks(range(len(sizes)))
        ax.set_xticklabels([TICK[s] for s in sizes])
        ax.set_title(f"{title}\n(number above bar: {unit})", fontsize=7.5, pad=10)
        ax.set_xlabel("Size (bytes)")
        ax.set_ylim(0, 100)
    axes[0].set_ylabel("Share of CPU cycles (%)")
    axes[1].legend(loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False, fontsize=7)
    fig.tight_layout(pad=0.4)
    fig.savefig(f"{FIG}/fig_breakdown.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_wakeup(wk):
    plt = style()
    by, med, cols = wk["by"], wk["med"], wk["cols"]
    sizes = [s for s in SIZES if s in med and len(by[s]) >= 30]
    labels = ["waking to IPI sent", "IPI to CPU leaves idle", "idle exit to wakeup handled",
              "wakeup to context switch", "context switch to syscall return"]
    colors = ["#e67e22", "#c0392b", "#8e44ad", "#1f5fbf", "#16a085"]
    fig, ax = plt.subplots(figsize=(3.3, 3.5))
    fig.subplots_adjust(left=0.15, right=0.98, top=0.94, bottom=0.42)
    bottom = [0.0] * len(sizes)
    totals = [sum(med[s][c] or 0 for c in cols) for s in sizes]
    for c, lab, col in zip(cols, labels, colors):
        vals = [100 * (med[s][c] or 0) / t for s, t in zip(sizes, totals)]
        ax.bar(range(len(sizes)), vals, bottom=bottom, color=col, edgecolor="white", lw=0.5, width=0.66, label=lab)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, t in enumerate(totals):
        ax.text(i, 101, f"{t / 1e3:.1f}", ha="center", va="bottom", fontsize=6.5)
    ax.set_xticks(range(len(sizes)))
    ax.set_xticklabels([TICK[s] for s in sizes], rotation=45, ha="right")
    ax.set_xlabel("Size (bytes); label above = total ($\\mu$s)")
    ax.set_ylabel("Share of wakeup (%)")
    ax.set_ylim(0, 100)
    handles, labs = ax.get_legend_handles_labels()
    fig.legend(handles, labs, loc="lower center", ncol=1, frameon=False, fontsize=6.8)
    fig.savefig(f"{FIG}/fig_wakeup.pdf")
    plt.close(fig)


# ------------------------------------------------------------------ main
def main():
    os.makedirs(GEN, exist_ok=True)
    os.makedirs(FIG, exist_ok=True)
    lat, tp = runner_numbers()
    ld, td = newest("results/trace/latency/*/"), newest("results/trace/throughput/*/")
    if not ld or not td:
        sys.exit("need a latency and a throughput trace run under results/trace/")
    lt = read_tables(os.path.join(ld, "summary.txt"))
    tt = read_tables(os.path.join(td, "summary.txt"))
    mac("latTraceRun", os.path.basename(ld))
    mac("tpTraceRun", os.path.basename(td))
    emit_size_table("latC", lt["1a"], {"ctx-sw": "CtxSw", "migr": "Migr", "kcycles": "Kcyc", "kinstr": "Kinstr",
                                       "IPC": "Ipc"})
    emit_size_table("latM", lt["1b"], {"cache-ref": "CacheRef", "cache-miss": "CacheMiss", "L1d-miss": "LOneMiss",
                                       "L2-miss": "LTwoMiss", "xsnp-hitm": "XsnpHitm", "xsnp-fwd": "XsnpFwd",
                                       "dTLB-miss": "DtlbMiss", "iTLB-miss": "ItlbMiss"})
    emit_size_table("latW", lt["6a"], {"median RTT us": "MedRtt", "CPU work us": "CpuWork", "both idle us": "Idle",
                                       "idle %": "IdlePct", "wakeups/RTT": "Wakeups",
                                       "idle per wakeup us": "IdlePerWake"})
    emit_size_table("tpC", tt["1a"], {"ctx-sw/chunk": "CtxSw", "kcycles/chunk": "Kcyc", "IPC": "Ipc",
                                      "cycles/KiB": "CycKiB", "chunks/run": "Chunks"})
    emit_size_table("tpW", tt["6a"], {"wall ns/chunk": "WallNs", "CPU ns/chunk": "CpuNs", "cores busy": "Cores",
                                      "cycles/KiB": "CycKiB"})
    emit_size_table("tpM", tt["1b"], {"xsnp-hitm": "XsnpHitm", "cache-miss": "CacheMiss"})
    emit_size_table("latT", lt["2a"], {"us/RTT*": "UsRtt", "wake->run us": "WakeRun"})
    emit_size_table("latS", lt["4a"], {"parent wr": "ParentWr", "parent rd": "ParentRd", "child wr": "ChildWr",
                                       "child rd": "ChildRd"})
    emit_breakdown("bdLat", lt["3a"])
    emit_breakdown("bdTp", tt["3a"])
    lat_sh, tp_sh = breakdown_shares(lt["3a"]), breakdown_shares(tt["3a"])
    rows1a = {to_size(c[0]): c for c in lt["1a"][1] if to_size(c[0])}
    lat_total = {s: num(rows1a[s][lt["1a"][0].index("kcycles")]) for s in lat_sh}
    tp_total = {}
    for cells in tt["3a"][1]:
        if cells[0].startswith("TOTAL"):
            sizes = sorted(tp_sh)
            tp_total = {s: num(v) for s, v in zip(sizes, cells[1:])}
    wl = wakeup_numbers(ld, "wakeLat")
    wt = wakeup_numbers(td, "wakeTp")
    clock_numbers()
    source_constants(ld)
    derived_numbers(lat, tp, lt, tt)
    summary_extras(ld)
    folded_shares(ld, "ftLat")
    folded_shares(td, "ftTp")

    table_latency(lat)
    table_throughput(tp)
    table_lat_where(lt["1a"], lt["6a"])
    table_tp_where(tt["1a"], tt["6a"])
    fig_latency(lat)
    fig_throughput(tp)
    fig_latency_throughput(lat, tp)
    fig_breakdown(lat_sh, tp_sh, lat_total, tp_total)
    if wl:
        fig_wakeup(wl)
    write(f"{GEN}/numbers.tex", "% Generated by scripts/make_paper_assets.py. Do not edit by hand.\n"
          + "".join(f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in MACROS.items()))
    print(f"wrote {len(MACROS)} macros, 4 tables, 4 figures\n  latency trace: {ld}\n  throughput trace: {td}")


if __name__ == "__main__":
    main()
