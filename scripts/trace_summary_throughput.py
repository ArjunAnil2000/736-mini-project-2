#!/usr/bin/env python3
"""trace_summary_throughput.py <run_dir> - turn one throughput tracing run (see throughput-trace.sh) into
readable tables. The throughput twin of trace_summary.py, whose helpers it imports.

Throughput streams data one way, in chunks of one size, with a single 1-byte ack at the end, so there is no
"round trip". Everything is filtered to the benchmark's own processes and normalised PER CHUNK (one write()
by the parent, one read() by the child) and PER MiB moved (MiB = 2^20 bytes), per chunk size.
Missing input files are skipped, so a partial run still summarises; a broken section never hides the others.

Sections: 1 counters (perf stat)  2 scheduling/syscalls/idle/IPI (perf record tracepoints)
          3 where the cycles go (perf record cycles)  4 syscalls (strace)  5 kernel call graph (ftrace)
          6 wall clock vs CPU work (measured throughput of results/throughput_run*.csv)

Every number is printed by this script from the run's own files; nothing is written by hand.
"""
import glob
import os
import re
import statistics as st
import sys

sys.dont_write_bytecode = True  # this often runs as root; do not leave a root-owned __pycache__
os.environ.setdefault("BENCH_COMM", "throughput")  # comm of the benchmark's processes; read at import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trace_summary as t  # noqa: E402  (helpers shared with the latency summary; not modified)
import analyze_throughput as at  # noqa: E402

MIB = 1024 * 1024
EXIT = 4294967295  # power:cpu_idle state value that means "leaving idle"


# ---------------------------------------------------------------- run parameters
def chunk_map(s):
    """'4:1000 16:500' -> {4: 1000, 16: 500}"""
    return {int(a): int(b) for a, b in (x.split(":") for x in s.split())}


def chunks_for_bytes(total_bytes, size):
    """chunks a `throughput -b total_bytes` run moves at this size (no clamp: at least 1)."""
    return max(1, total_bytes // size)


def freq_ghz(p):
    return int(p.get("freq_khz", 0)) / 1e6


def untraced(d):
    """({size: (best MiB/s, median MiB/s)}, files) from results/throughput_run*.csv, the clean runs of
    throughput-runner.sh. Deliberately no other file."""
    files = sorted(glob.glob(os.path.join(d, "..", "..", "..", "throughput_run*.csv")))
    return at.best_median_by_size(files), files


# ---------------------------------------------------------------- 1. perf stat
def counter_deltas(d, p):
    """-> ({size: {event: count above the baseline, or None}}, {size: chunks}); startup cost subtracted."""
    sizes = [int(x) for x in p.get("sizes", "").split()]
    chunks = chunk_map(p.get("stat_chunks", ""))
    ngroups = int(p.get("groups", 0))
    delta = {}
    if not sizes or not chunks or not ngroups:
        return delta, chunks
    base = [t.baseline_stats(d, gi) for gi in range(ngroups)]
    for sz in sizes:
        delta[sz] = {}
        for gi in range(ngroups):
            for ev, v in t.read_stat(f"{d}/stat_{gi}_{sz}.csv").items():
                if ev in base[gi]:
                    delta[sz][ev] = v - base[gi][ev] if v >= base[gi][ev] else None  # below baseline: no signal
    return delta, chunks


def per_chunk_and_mib(delta, chunks, sz, ev):
    """(per chunk, per MiB) of a counter, or (None, None)."""
    v = delta.get(sz, {}).get(ev)
    if v is None or sz not in chunks:
        return None, None
    return v / chunks[sz], v / (chunks[sz] * sz / MIB)


def section_counters(d, p):
    delta, chunks = counter_deltas(d, p)
    sizes = sorted(delta)
    if not any(delta.values()):
        return
    print("\n" + "=" * 100)
    print("1. COUNTERS (perf stat) - per chunk and per MiB moved, the benchmark's parent + child together")
    print(f"   Each run moved {int(p.get('stat_bytes', 0)) / MIB:,.0f} MiB per size (fewer bytes for tiny chunks: capped at "
          f"{p.get('stat_max_chunks')} chunks); startup cost = median of {p.get('stat_baselines', '?')} baseline runs,")
    print("   subtracted; each counter group ran alone (not multiplexed); no CSV printing (-q).")
    print("   chunk = one write() by the parent + one read() by the child. MiB = 2^20 bytes.")

    def pc(sz, ev):
        return per_chunk_and_mib(delta, chunks, sz, ev)[0]

    def pm(sz, ev):
        return per_chunk_and_mib(delta, chunks, sz, ev)[1]

    rows = []
    for sz in sizes:
        cy, ins = pc(sz, "cycles"), pc(sz, "instructions")
        cyc_kib = None if pm(sz, "cycles") is None else pm(sz, "cycles") / 1024
        rows.append([t.human_size(sz), chunks.get(sz, "n/a"), t.num(pc(sz, "context-switches"), 3),
                     t.num(pc(sz, "cpu-migrations"), 4), t.num(pm(sz, "page-faults"), 2),
                     t.num(None if cy is None else cy / 1e3, 2), t.num(None if ins is None else ins / 1e3, 2),
                     t.num(None if not cy or ins is None else ins / cy, 2), t.num(cyc_kib, 0)])
    t.table(["chunk", "chunks/run", "ctx-sw/chunk", "migr/chunk", "pg-fault/MiB", "kcycles/chunk", "kinstr/chunk",
             "IPC", "cycles/KiB"], rows, "1a. scheduling and CPU work (kcycles = thousands of cycles)")

    rows = []
    for sz in sizes:
        rows.append([t.human_size(sz), t.num(pm(sz, "cache-references"), 0), t.num(pm(sz, "cache-misses"), 0),
                     t.num(pm(sz, "L1-dcache-load-misses"), 0), t.num(pm(sz, "mem_load_retired.l2_miss"), 0),
                     t.num(pm(sz, "mem_load_l3_hit_retired.xsnp_hitm"), 0),
                     t.num(pm(sz, "mem_load_l3_hit_retired.xsnp_fwd"), 0),
                     t.num(pm(sz, "dTLB-load-misses"), 0), t.num(pm(sz, "iTLB-load-misses"), 0)])
    t.table(["chunk", "cache-ref", "cache-miss", "L1d-miss", "L2-miss", "xsnp-hitm", "xsnp-fwd", "dTLB-miss",
             "iTLB-miss"], rows,
            "1b. memory system, per MiB moved. xsnp-hitm/fwd = loads served from the OTHER core's cache, i.e.\n"
            "    data moving between the parent's core and the child's core")

    rows = []
    for sz in sizes:
        slots = delta[sz].get("slots")
        if not slots:
            continue
        pct = [None if delta[sz].get(f"topdown-{k}") is None else 100 * delta[sz][f"topdown-{k}"] / slots
               for k in ("retiring", "fe-bound", "be-bound", "bad-spec")]
        rows.append([t.human_size(sz)] + [t.num(x, 1) + "%" for x in pct])
    if rows:
        t.table(["chunk", "retiring", "front-end", "back-end", "bad-spec"], rows,
                "1c. top-down: where the CPU's pipeline slots went (P-core)")


# ---------------------------------------------------------------- 2. perf record: tracepoints
def tracepoint_rows(ev, wins, bench, chunks):
    """One dict per size window with per-chunk / per-MiB figures. `chunks`: {size: chunks in that window}.
    parent = a benchmark task that issues chunk writes (pipe write, count == size); child = the other."""
    out = []
    for size, t0, t1 in wins:
        n = chunks.get(size)
        if not n:
            continue
        W = [e for e in ev if t0 <= e["t"] < t1]
        parents = {e["tid"] for e in W if e["ev"] == "syscalls:sys_enter_write" and e["tid"] in bench
                   and (t.field(e["f"], "fd") or 0) >= 3 and t.field(e["f"], "count") == size}
        children = bench - parents
        mib = n * size / MIB
        sw = [e for e in W if e["ev"] == "sched:sched_switch" and e["tid"] in bench]
        wake = [e for e in W if e["ev"] == "sched:sched_waking" and t.field(e["f"], "pid") in bench]
        lat, cross = [], 0
        for w in wake:
            pid = t.field(w["f"], "pid")
            for q in W:
                if q["t"] >= w["t"] and q["ev"] == "sched:sched_switch":
                    m = re.search(r"==> .*?:(\d+) \[", q["f"])
                    if m and int(m.group(1)) == pid:
                        lat.append((q["t"] - w["t"]) * 1e6)
                        break
            cross += w["cpu"] != t.field(w["f"], "target_cpu")
        writes = [e for e in W if e["ev"] == "syscalls:sys_enter_write" and e["tid"] in parents
                  and t.field(e["f"], "count") == size]
        reads = [e for e in W if e["ev"] == "syscalls:sys_enter_read" and e["tid"] in children
                 and (t.field(e["f"], "fd") or 0) >= 3]
        ipis = [e for e in W if e["ev"] == "ipi:ipi_send_cpu" and t.field(e["f"], "cpu") in (0, 1)]
        idle = [e for e in W if e["ev"] == "power:cpu_idle" and e["cpu"] in (0, 1)
                and t.field(e["f"], "state") != EXIT]
        pages = [e for e in W if e["ev"] == "kmem:mm_page_alloc" and e["tid"] in bench]
        lock = [e for e in W if e["ev"] == "lock:contention_begin" and e["tid"] in bench]
        out.append(dict(size=size, chunks=n, ctx=len(sw) / n, wakeups=len(wake) / n,
                        wake_run_us=st.median(lat) if lat else None,
                        cross_pct=100 * cross / len(wake) if wake else None,
                        reads=len(reads) / n, writes=len(writes) / n, ipis=len(ipis) / n, idle=len(idle) / n,
                        pages_mib=len(pages) / mib, lock_mib=len(lock) / mib, mibs=mib / (t1 - t0)))
    return out


def idle_state_rows(ev, wins, chunks):
    """Per size window: idle entries per chunk on CPUs 0-1, states entered, median stay (us)."""
    rows = []
    for size, t0, t1 in wins:
        n = chunks.get(size)
        if not n:
            continue
        cnt, res, open_ = {}, {}, {}
        for e in ev:
            if not (t0 <= e["t"] < t1) or e["ev"] != "power:cpu_idle" or e["cpu"] not in (0, 1):
                continue
            state = t.field(e["f"], "state")
            if state != EXIT:
                open_[e["cpu"]] = (e["t"], state)
                cnt[state] = cnt.get(state, 0) + 1
            elif e["cpu"] in open_:
                t_in, s_in = open_.pop(e["cpu"])
                res.setdefault(s_in, []).append((e["t"] - t_in) * 1e6)
        rows.append([t.human_size(size), t.num(sum(cnt.values()) / n, 3),
                     ", ".join(f"C{k}:{v}" for k, v in sorted(cnt.items())) or "-",
                     ", ".join(f"C{k}={t.num(st.median(v), 1)}" for k, v in sorted(res.items()) if v) or "-"])
    return rows


def section_tracepoints(d, p):
    path = f"{d}/perf_trace.data"
    if not os.path.exists(path):
        return
    sizes = [int(x) for x in p.get("sizes", "").split()]
    tb = int(p.get("trace_bytes", 0))
    ev = t.load_events(path)
    if not ev or not tb:
        return
    chunks = {sz: chunks_for_bytes(tb, sz) for sz in sizes}
    bench = {e["tid"] for e in ev if e["comm"] == t.BENCH_COMM}
    wins = t.size_windows(ev, set(sizes))
    print("\n" + "=" * 100)
    print("2. SCHEDULING, SYSCALLS, IDLE, IPIs (perf record tracepoints - EVERY occurrence, benchmark tasks only)")
    print(f"   {len(ev)} events on CPUs 0-1; benchmark tasks (comm '{t.BENCH_COMM}'): tids {sorted(bench)}; "
          f"{tb / 1024:,.0f} KiB per size (chunks = max(1, bytes/size)).")
    if not wins:
        print("   !! could not find the benchmark's pipe writes - is the comm name right (BENCH_COMM)?")
        return
    rows = []
    for r in tracepoint_rows(ev, wins, bench, chunks):
        rows.append([t.human_size(r["size"]), r["chunks"], t.num(r["ctx"], 3), t.num(r["wakeups"], 3),
                     t.num(r["wake_run_us"], 1),
                     t.num(r["cross_pct"], 0) + "%" if r["cross_pct"] is not None else "n/a",
                     t.num(r["reads"], 2), t.num(r["writes"], 2), t.num(r["ipis"], 3), t.num(r["idle"], 3),
                     t.num(r["pages_mib"], 0), t.num(r["lock_mib"], 2), t.num(r["mibs"], 0)])
    t.table(["chunk", "chunks", "ctx-sw", "wakeups", "wake->run us", "cross-cpu", "child rd", "parent wr", "IPIs",
             "idle-ent", "pg-alloc/MiB", "lock/MiB", "MiB/s*"], rows,
            "2a. per chunk unless marked. ctx-sw = times a benchmark task was switched out; wake->run = median time\n"
            "    from sched_waking to the task actually running; cross-cpu = the waker ran on a different CPU than\n"
            "    the task it woke; child rd = read() calls per chunk (above 1 means the reader found the pipe short\n"
            "    and had to read again); IPIs / idle-ent = interrupts sent to and idle entries on CPUs 0-1.\n"
            "    *MiB/s is throughput UNDER TRACING (and over a tiny transfer), not the real throughput.")
    # time inside the pipe read()/write() syscalls
    rows = []
    for size, t0, t1 in wins:
        vals = {}
        for name in ("read", "write"):
            cur, v = {}, []
            for e in ev:
                if not (t0 <= e["t"] < t1) or e["tid"] not in bench:
                    continue
                if e["ev"] == f"syscalls:sys_enter_{name}" and (t.field(e["f"], "fd") or 0) >= 3:
                    cur[e["tid"]] = e["t"]
                elif e["ev"] == f"syscalls:sys_exit_{name}" and e["tid"] in cur:
                    v.append((e["t"] - cur.pop(e["tid"])) * 1e6)
            vals[name] = v
        rows.append([t.human_size(size)] + [t.num(st.median(vals[k]), 1) if vals[k] else "n/a" for k in ("read", "write")]
                    + [t.num(max(vals[k]), 0) if vals[k] else "n/a" for k in ("read", "write")])
    t.table(["chunk", "read med us", "write med us", "read max us", "write max us"], rows,
            "2b. time INSIDE the read()/write() syscalls on the pipes (a read includes waiting for the parent, a\n"
            "    write includes waiting for the child to drain a full pipe)")
    t.table(["chunk", "idle entries/chunk", "states entered (count)", "median stay us (under tracing)"],
            idle_state_rows(ev, wins, chunks), "2c. idle states entered on CPUs 0-1 (C0 = polling, higher = deeper sleep)")
    bad = t.run(["perf", "report", "-i", path, "--stat"])
    flagged = [l.strip() for l in bad.splitlines() if re.search(r"THROTTLE|LOST", l) and not re.search(r" 0 ", l)]
    print("\n   completeness: " + ("!! " + "; ".join(flagged) if flagged else "no throttled or lost samples"))


# ---------------------------------------------------------------- 3. perf record: cycles
def cycle_shares(rows):
    """[(pct, kind, sym)] -> ({category: share %}, total pct). Same name-based categories as the latency summary."""
    cat = {}
    for pc, kind, sym in rows:
        c = t.categorize(kind, sym)
        cat[c] = cat.get(c, 0.0) + pc
    tot = sum(cat.values()) or 1.0
    return {c: 100 * v / tot for c, v in cat.items()}


def section_cycles(d, p):
    sizes = [int(x) for x in p.get("cycles_sizes", "").split()]
    paths = {sz: f"{d}/perf_cycles_{sz}.data" for sz in sizes if os.path.exists(f"{d}/perf_cycles_{sz}.data")}
    if not paths:
        return
    print("\n" + "=" * 100)
    print("3. WHERE THE CPU CYCLES GO (perf record cycles + call graphs, one profile per chunk size)")
    print("   The benchmark's processes only; no CSV printing (-q). Categories are a heuristic, NAME-based grouping")
    print("   of kernel symbols; kernel symbols only resolve when this runs as root.")
    prof, tops, others = {}, {}, {}
    for sz, path in paths.items():
        rows = t.cycle_profile(path)
        prof[sz] = cycle_shares(rows)
        tops[sz] = rows[:8]
        others[sz] = [r for r in rows if t.categorize(r[1], r[2]) == t.OTHER_CAT][:6]
    unres = max((prof[sz].get(t.UNRES_CAT, 0) for sz in prof), default=0)
    if unres > 30:
        print(f"   !! {unres:.0f}% of samples are unresolved kernel addresses - re-run this script with sudo")
    delta, chunks = counter_deltas(d, p)
    cyc_kib = {}
    for sz in prof:
        v = per_chunk_and_mib(delta, chunks, sz, "cycles")[1]
        cyc_kib[sz] = None if v is None else v / 1024
    szs = sorted(prof)
    label = {sz: t.human_size(sz) for sz in szs}
    hdr = ["component"] + sum(([f"{label[sz]} %", f"{label[sz]} cyc/KiB"] for sz in szs), [])
    rows = []
    for c in t.CAT_ORDER:
        if any(prof[sz].get(c, 0) >= 0.05 for sz in szs):
            row = [c]
            for sz in szs:
                sh = prof[sz].get(c, 0.0)
                row += [f"{sh:.1f}%", t.num(None if cyc_kib[sz] is None else cyc_kib[sz] * sh / 100, 1)]
            rows.append(row)
    rows.append(["TOTAL CPU work (perf stat)"] + sum(([ "", t.num(cyc_kib[sz], 1)] for sz in szs), []))
    t.table(hdr, rows, "3a. cycles per KiB moved, by component (both tasks; cyc/KiB = cycles per 1024 bytes of data)")
    for sz in szs:
        print(f"\n3b. top functions, {label[sz]} chunks (% of cycles)")
        for pc, kind, sym in tops[sz]:
            print(f"    {pc:6.2f}%  [{kind}] {sym}   <{t.categorize(kind, sym)}>")
        if others[sz] and prof[sz].get(t.OTHER_CAT, 0) > 5:
            print(f"    largest symbols left in 'other kernel' ({prof[sz][t.OTHER_CAT]:.0f}%): " +
                  ", ".join(f"{sym} {pc:.1f}%" for pc, _, sym in others[sz]))
    chart_breakdown(d, szs, prof, cyc_kib, label)


def chart_breakdown(d, szs, prof, cyc_kib, label):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n   !! matplotlib is not importable here (as root it lives only in the user's site-packages):")
        print("   breakdown chart NOT written. Re-run with PYTHONPATH pointing at the user's site-packages.")
        return
    colors = ["#1f5fbf", "#e67e22", "#8e44ad", "#c0392b", "#16a085", "#7f8c8d", "#f1c40f", "#bdc3c7", "#2c3e50",
              "#95a5a6"]
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 11})
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bottom = [0.0] * len(szs)
    for c, col in zip(t.CAT_ORDER, colors):
        vals = [prof[sz].get(c, 0.0) for sz in szs]
        if max(vals) < 0.5:
            continue
        ax.bar(range(len(szs)), vals, bottom=bottom, color=col, edgecolor="white", width=0.6, label=c)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, sz in enumerate(szs):
        if cyc_kib[sz] is not None:
            v = cyc_kib[sz]  # abbreviated so a huge value (tiny chunks) does not run into its neighbour's label
            ax.text(i, 101, (f"{v / 1000:,.0f}k" if v >= 10000 else f"{v:,.0f}") + " cyc/KiB",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(szs)))
    ax.set_xticklabels([label[sz] for sz in szs])
    ax.set_xlabel("Chunk size")
    ax.set_ylabel("Share of CPU cycles (%)")
    ax.set_ylim(0, 108)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8, frameon=False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{d}/breakdown.{ext}", dpi=200)
    plt.close(fig)
    print(f"\n   chart: {d}/breakdown.png (+ .pdf)")


# ---------------------------------------------------------------- 4. strace
STRACE_RET = re.compile(t.STRACE.pattern)


def parse_strace_ret(path):
    """Like trace_summary.parse_strace but keeps the return value: [{t, name, args, ret, dur}]."""
    out = []
    for line in open(path, errors="replace"):
        if line.startswith(" >"):
            continue
        m = STRACE_RET.match(line)
        if m:
            ret = int(m.group(5)) if m.group(5) not in ("?", None) else None
            out.append(dict(t=float(m.group(1)), name=m.group(3), args=m.group(4), ret=ret, dur=float(m.group(7) or 0)))
    return out


def strace_rows(parent, child, sizes, chunks):
    """Per size window (from the parent's chunk writes): parent writes/chunk, child reads/chunk, mean bytes
    returned per child read. `parent`/`child`: parse_strace_ret() lists."""
    def pipe_ops(s, name):
        return [x for x in s if x["name"] == name and "pipe:" in x["args"]]

    starts, seen = [], set()
    for x in pipe_ops(parent, "write"):
        sz = t.count_of(x)
        if sz in sizes and sz not in seen:
            seen.add(sz)
            starts.append((sz, x["t"]))
    last = max(x["t"] for s in (parent, child) for x in s)
    wins = [(sz, t0, starts[i + 1][1] if i + 1 < len(starts) else last + 1) for i, (sz, t0) in enumerate(starts)]
    rows = []
    for sz, t0, t1 in wins:
        n = chunks.get(sz)
        if not n:
            continue
        wr = [x for x in pipe_ops(parent, "write") if t0 <= x["t"] < t1 and t.count_of(x) == sz]
        rd = [x for x in pipe_ops(child, "read") if t0 <= x["t"] < t1]
        got = [x["ret"] for x in rd if x["ret"] and x["ret"] > 0]
        rows.append(dict(size=sz, chunks=n, wr=len(wr) / n, rd=len(rd) / n,
                         avg_read=sum(got) / len(got) if got else None))
    return rows


def section_strace(d, p):
    files = sorted(glob.glob(f"{d}/strace_*.*"))
    if not files:
        return
    sizes = [int(x) for x in p.get("sizes", "").split()]
    sb = int(p.get("strace_bytes", 0))
    procs = {f: parse_strace_ret(f) for f in files}
    procs = {f: s for f, s in procs.items()
             if sum(x["name"] == "write" and "pipe:" in x["args"] and t.count_of(x) in sizes for x in s) >= 1
             or sum(x["name"] == "read" and "pipe:" in x["args"] for x in s) >= 1}
    parent = next((f for f, s in procs.items() if any(x["name"] in ("wait4", "waitid") for x in s)), None)
    print("\n" + "=" * 100)
    print("4. SYSCALLS (strace - every call; strace itself slows the run a lot, so use COUNTS, not times)")
    for f, s in procs.items():
        c = {}
        for x in s:
            c[x["name"]] = c.get(x["name"], 0) + 1
        top = sorted(c.items(), key=lambda kv: -kv[1])[:5]
        print(f"   {'parent' if f == parent else 'child '} {os.path.basename(f)}: {len(s)} syscalls; top: " +
              ", ".join(f"{k} x{v}" for k, v in top))
    child = [f for f in procs if f != parent]
    if not parent or not child or not sb:
        return
    chunks = {sz: chunks_for_bytes(sb, sz) for sz in sizes}
    rows = [[t.human_size(r["size"]), r["chunks"], t.num(r["wr"], 3), t.num(r["rd"], 3), t.num(r["avg_read"], 0)]
            for r in strace_rows(procs[parent], procs[child[0]], set(sizes), chunks)]
    t.table(["chunk", "chunks", "parent wr/chunk", "child rd/chunk", "avg bytes/read"], rows,
            "4a. pipe syscalls per chunk. The parent does one write() per chunk. The child asks for exactly one chunk\n"
            "    per read(); it needs more than one read() when the pipe holds less than a chunk (a chunk larger than\n"
            "    the 64 KB pipe buffer always does). avg bytes/read = bytes the child's reads returned on average.")


# ---------------------------------------------------------------- 6. wall clock vs CPU work
def wall_rows(delta, chunks, meas, ghz):
    """Per size: measured throughput (best, median MiB/s) against the CPU work perf stat counted per chunk."""
    rows = []
    for sz in sorted(delta):
        cyc = per_chunk_and_mib(delta, chunks, sz, "cycles")[0]
        if cyc is None or sz not in meas or not ghz:
            continue
        best, med = meas[sz]
        wall_ns = sz / (med * MIB) * 1e9          # wall time per chunk at the MEDIAN throughput
        cpu_ns = cyc / ghz                        # CPU time per chunk, both tasks together
        rows.append(dict(size=sz, best=best, median=med, wall_ns=wall_ns, cpu_ns=cpu_ns, cores=cpu_ns / wall_ns,
                         cyc_kib=cyc / (sz / 1024)))
    return rows


def section_wall(d, p):
    delta, chunks = counter_deltas(d, p)
    meas, files = untraced(d)
    ghz = freq_ghz(p)
    rows = wall_rows(delta, chunks, meas, ghz)
    if not rows:
        return
    print("\n" + "=" * 100)
    print("6. WHERE THE WALL-CLOCK TIME GOES: measured throughput vs CPU work")
    print(f"   Measured, un-traced throughput from results/throughput_run*.csv ({len(files)} file(s)) against the CPU")
    print(f"   work perf stat counted per chunk at {ghz:.1f} GHz. Throughput is MiB/s (2^20 bytes per second).")
    print("   perf stat counts the AVERAGE chunk, hence the wall time per chunk uses the MEDIAN throughput.")
    t.table(["chunk", "best MiB/s", "median MiB/s", "wall ns/chunk", "CPU ns/chunk", "cores busy", "cycles/KiB"],
            [[t.human_size(r["size"]), t.num(r["best"], 1), t.num(r["median"], 1), t.num(r["wall_ns"], 0),
              t.num(r["cpu_ns"], 0), t.num(r["cores"], 2), t.num(r["cyc_kib"], 0)] for r in rows],
            "6a. per chunk size")
    print("   cores busy = CPU ns per chunk (parent + child together) / wall ns per chunk. The parent and child run at the")
    print("   same time as a pipeline, so 2.0 would mean both cores were busy the whole time, about 1.0 means one core's")
    print("   worth of work, and less means the tasks spent much of the time blocked or idle.")


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit(f"usage: {sys.argv[0]} <run_dir>")
    d = sys.argv[1].rstrip("/")
    p = t.read_params(d)
    print(f"THROUGHPUT TRACE SUMMARY  {os.path.basename(os.path.dirname(d))}/{os.path.basename(d)}   "
          f"(kernel {p.get('kernel', '?')}, CPUs pinned to {int(p.get('freq_khz', 0)) // 1000} MHz)")
    for f in (section_counters, section_tracepoints, section_cycles, section_strace, t.section_ftrace, section_wall):
        try:
            f(d, p)
        except Exception as e:  # a broken section must not hide the others
            print(f"\n!! {f.__name__} failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
