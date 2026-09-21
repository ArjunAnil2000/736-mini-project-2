#!/usr/bin/env python3
"""trace_summary.py <run_dir> - turn one tracing run (see trace-lib.sh) into readable tables.

Everything is filtered down to the benchmark's own processes and reported PER ROUND TRIP and PER
MESSAGE SIZE, so the numbers mean something about the pipe and not about the rest of the machine.
Missing input files are skipped, so a partial run still summarises.

Sections: 1 counters (perf stat)  2 scheduling/syscalls/idle/IPI (perf record tracepoints)
          3 where the cycles go (perf record cycles)  4 syscalls (strace)  5 kernel call graph (ftrace)
"""
import collections
import csv
import glob
import os
import re
import statistics as st
import subprocess
import sys

sys.dont_write_bytecode = True  # this often runs as root; do not leave a root-owned __pycache__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for analyze_latency

BENCH_COMM = os.environ.get("BENCH_COMM", "latency")  # comm of the benchmark's processes
STATE = {"wakeups": {}}  # size -> benchmark wakeups per round trip, filled by section_tracepoints


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return r.stdout


def table(headers, rows, title=None):
    if title:
        print(f"\n{title}")
    cells = [[str(c) for c in r] for r in rows]
    w = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]
    print("  " + "  ".join(h.rjust(w[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w[i] for i in range(len(headers))))
    for r in cells:
        print("  " + "  ".join(c.rjust(w[i]) for i, c in enumerate(r)))


def num(x, d=1):
    return "n/a" if x is None else f"{x:,.{d}f}"


def read_params(d):
    p = {}
    try:
        for line in open(os.path.join(d, "params.txt")):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                p[k] = v
    except OSError:
        pass
    return p


# ---------------------------------------------------------------- 1. perf stat
def read_stat(path):
    """event -> value for one perf stat -x, file. cpu_atom events and uncounted events are dropped."""
    out = {}
    try:
        for r in csv.reader(l for l in open(path) if l.strip() and not l.startswith("#")):
            if len(r) < 3 or "cpu_atom" in r[2]:
                continue
            try:
                out[r[2].replace("cpu_core/", "").rstrip("/")] = float(r[0])
            except ValueError:
                pass  # <not counted> / <not supported>
    except OSError:
        pass
    return out


def baseline_stats(d, gi):
    """event -> MEDIAN over the baseline runs of group gi (one run's own noise would hit every size)."""
    runs = [read_stat(f) for f in sorted(glob.glob(f"{d}/stat_{gi}_baseline*.csv"))]
    return {ev: st.median(r[ev] for r in runs if ev in r) for ev in set().union(*runs)} if runs else {}


def counters_per_rtt(d, p):
    """-> ({size: {event: per-round-trip value or None}}, rounds per run); startup cost subtracted."""
    sizes = [int(x) for x in p.get("sizes", "").split()]
    n = int(p.get("stat_warmup", 0)) + int(p.get("stat_iterations", 0))
    ngroups = int(p.get("groups", 0))
    per = {}
    if not sizes or not n or not ngroups:
        return per, n
    base = [baseline_stats(d, gi) for gi in range(ngroups)]
    for sz in sizes:
        per[sz] = {}
        for gi in range(ngroups):
            for ev, v in read_stat(f"{d}/stat_{gi}_{sz}.csv").items():
                if ev in base[gi]:
                    per[sz][ev] = (v - base[gi][ev]) / n if v >= base[gi][ev] else None  # below baseline: no signal
    return per, n


def section_counters(d, p):
    sizes = [int(x) for x in p.get("sizes", "").split()]
    per, n = counters_per_rtt(d, p)
    if not any(per.values()):
        return
    print("\n" + "=" * 100)
    print("1. COUNTERS (perf stat) - per round trip, the benchmark's parent + child together")
    print(f"   {n} round trips per size ({p.get('stat_warmup')} warmup + {p.get('stat_iterations')} timed), "
          f"startup cost = median of {p.get('stat_baselines', '?')} baseline runs, subtracted; each counter group "
          "ran alone (not multiplexed); no per-iteration printing (-q).")

    def g(sz, ev):
        return per[sz].get(ev)

    def ratio(a, b):
        return None if a is None or not b else a / b

    rows = []
    for sz in sizes:
        cy, ins = g(sz, "cycles"), g(sz, "instructions")
        rows.append([sz, num(g(sz, "context-switches"), 2), num(g(sz, "cpu-migrations"), 3),
                     num(g(sz, "page-faults"), 2), num(None if cy is None else cy / 1e3, 1),
                     num(None if ins is None else ins / 1e3, 1), num(ratio(ins, cy), 2)])
    table(["size", "ctx-sw", "migr", "pg-faults", "kcycles", "kinstr", "IPC"], rows,
          "1a. scheduling and CPU work (kcycles = thousands of cycles)")

    rows = []
    for sz in sizes:
        cr, cm = g(sz, "cache-references"), g(sz, "cache-misses")
        rows.append([sz, num(cr, 0), num(cm, 0), num(None if ratio(cm, cr) is None else 100 * ratio(cm, cr), 0) + "%",
                     num(g(sz, "L1-dcache-load-misses"), 0), num(g(sz, "mem_load_retired.l2_miss"), 0),
                     num(g(sz, "mem_load_l3_hit_retired.xsnp_hitm"), 1),
                     num(g(sz, "mem_load_l3_hit_retired.xsnp_fwd"), 1),
                     num(g(sz, "dTLB-load-misses"), 0), num(g(sz, "iTLB-load-misses"), 0)])
    table(["size", "cache-ref", "cache-miss", "miss%", "L1d-miss", "L2-miss", "xsnp-hitm", "xsnp-fwd",
           "dTLB-miss", "iTLB-miss"], rows,
          "1b. memory system. xsnp-hitm/fwd = loads served from the OTHER core's cache, i.e. data\n"
          "    moving between the parent's core and the child's core")

    rows = []
    for sz in sizes:
        slots = g(sz, "slots")
        if not slots:
            continue
        pct = [None if g(sz, f"topdown-{k}") is None else 100 * g(sz, f"topdown-{k}") / slots
               for k in ("retiring", "fe-bound", "be-bound", "bad-spec")]
        rows.append([sz] + [num(x, 1) + "%" for x in pct])
    if rows:
        table(["size", "retiring", "front-end", "back-end", "bad-spec"], rows,
              "1c. top-down: where the CPU's pipeline slots went (P-core)")


# ---------------------------------------------------------------- 2. perf record: tracepoints
LINE = re.compile(r"\s*(.+?)\s+(\d+)\s+\[(\d+)\]\s+([\d.]+):\s+([\w:.\-/]+):\s?(.*)$")


def intval(s):
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def load_events(path):
    ev = []
    if os.environ.get("TRACE_SUMMARY_SCRIPT_TXT"):  # test hook: pre-rendered `perf script` output
        text = open(os.environ["TRACE_SUMMARY_SCRIPT_TXT"], errors="replace").read()
    else:
        # --ns: timestamps default to whole microseconds, which quantised every wake->run latency
        text = run(["perf", "script", "-i", path, "--ns", "-F", "comm,tid,cpu,time,event,trace",
                    "--hide-call-graph"])
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            ev.append(dict(comm=m.group(1), tid=int(m.group(2)), cpu=int(m.group(3)),
                           t=float(m.group(4)), ev=m.group(5), f=m.group(6)))
    return ev


def field(f, name):
    m = re.search(rf"\b{name}[=:]\s*(0x[0-9a-fA-F]+|-?\d+)", f)
    return intval(m.group(1)) if m else None


def size_windows(ev, sizes):
    """[(size, t_start, t_end)] from the benchmark's pipe writes (fd>=3, count in sizes)."""
    starts, seen = [], set()
    for e in ev:
        if e["comm"] == BENCH_COMM and e["ev"] == "syscalls:sys_enter_write":
            fd, cnt = field(e["f"], "fd"), field(e["f"], "count")
            if fd is not None and fd >= 3 and cnt in sizes and cnt not in seen:
                seen.add(cnt)
                starts.append((cnt, e["t"]))
    last = max((e["t"] for e in ev if e["comm"] == BENCH_COMM), default=0)
    return [(s, t, starts[i + 1][1] if i + 1 < len(starts) else last + 1e-9) for i, (s, t) in enumerate(starts)]


def section_tracepoints(d, p):
    path = f"{d}/perf_trace.data"
    if not os.path.exists(path):
        return
    sizes = [int(s) for s in p.get("sizes", "").split()]
    n = int(p.get("trace_warmup", 0)) + int(p.get("trace_iterations", 0))
    ev = load_events(path)
    if not ev or not n:
        return
    bench = {e["tid"] for e in ev if e["comm"] == BENCH_COMM}
    wins = size_windows(ev, set(sizes))
    print("\n" + "=" * 100)
    print("2. SCHEDULING, SYSCALLS, IDLE, IPIs (perf record tracepoints - EVERY occurrence, benchmark tasks only)")
    print(f"   {len(ev)} events on CPUs 0-1; benchmark tasks (comm '{BENCH_COMM}'): tids {sorted(bench)}; "
          f"{n} round trips per size ({p.get('trace_warmup')} warmup + {p.get('trace_iterations')} timed).")
    if not wins:
        print("   !! could not find the benchmark's pipe writes - is the comm name right (BENCH_COMM)?")
        return
    rows = []
    for size, t0, t1 in wins:
        W = [e for e in ev if t0 <= e["t"] < t1]
        sw = [e for e in W if e["ev"] == "sched:sched_switch" and e["tid"] in bench]
        wake = [e for e in W if e["ev"] == "sched:sched_waking" and (field(e["f"], "pid") in bench)]
        lat, cross = [], 0
        for i, w in enumerate(wake):
            pid = field(w["f"], "pid")
            for q in W:
                if q["t"] >= w["t"] and q["ev"] == "sched:sched_switch":
                    m = re.search(r"==> .*?:(\d+) \[", q["f"])
                    if m and int(m.group(1)) == pid:
                        lat.append((q["t"] - w["t"]) * 1e6)
                        break
            cross += w["cpu"] != field(w["f"], "target_cpu")
        STATE["wakeups"][size] = len(wake) / n
        rd = [e for e in W if e["ev"] == "syscalls:sys_enter_read" and e["tid"] in bench]
        wr = [e for e in W if e["ev"] == "syscalls:sys_enter_write" and e["tid"] in bench
              and (field(e["f"], "fd") or 0) >= 3]
        ipis = [e for e in W if e["ev"] == "ipi:ipi_send_cpu" and field(e["f"], "cpu") in (0, 1)]
        idle = [e for e in W if e["ev"] == "power:cpu_idle" and e["cpu"] in (0, 1)
                and field(e["f"], "state") != 4294967295]
        pages = [e for e in W if e["ev"] == "kmem:mm_page_alloc" and e["tid"] in bench]
        lock = [e for e in W if e["ev"] == "lock:contention_begin" and e["tid"] in bench]
        rows.append([size, num(len(sw) / n, 2), num(len(wake) / n, 2),
                     num(st.median(lat), 1) if lat else "n/a",
                     num(100 * cross / len(wake), 0) + "%" if wake else "n/a",
                     num(len(rd) / n, 1), num(len(wr) / n, 1), num(len(ipis) / n, 2), num(len(idle) / n, 2),
                     num(len(pages) / n, 1), num(len(lock) / n, 2), num((t1 - t0) / n * 1e6, 0)])
    table(["size", "ctx-sw", "wakeups", "wake->run us", "cross-cpu", "reads", "writes", "IPIs", "idle-ent",
           "pg-alloc", "lock-cont", "us/RTT*"], rows,
          "2a. per round trip. ctx-sw = times a benchmark task was switched out; wake->run = median time from\n"
          "    sched_waking to the task actually running (scheduler + idle-exit latency); cross-cpu = the waker\n"
          "    ran on a different CPU than the task it woke; IPIs / idle-ent = interrupts sent to and idle\n"
          "    entries on CPUs 0-1. *us/RTT is wall time UNDER TRACING, not the real latency.")
    # time inside the pipe read()/write() syscalls
    rows = []
    for size, t0, t1 in wins:
        d = {}
        for name in ("read", "write"):
            cur, vals = {}, []
            for e in ev:
                if not (t0 <= e["t"] < t1) or e["tid"] not in bench:
                    continue
                if e["ev"] == f"syscalls:sys_enter_{name}" and (field(e["f"], "fd") or 0) >= 3:
                    cur[e["tid"]] = e["t"]
                elif e["ev"] == f"syscalls:sys_exit_{name}" and e["tid"] in cur:
                    vals.append((e["t"] - cur.pop(e["tid"])) * 1e6)
            d[name] = vals
        rows.append([size] + [num(st.median(d[k]), 1) if d[k] else "n/a" for k in ("read", "write")]
                    + [num(max(d[k]), 0) if d[k] else "n/a" for k in ("read", "write")])
    table(["size", "read med us", "write med us", "read max us", "write max us"], rows,
          "2b. time INSIDE the read()/write() syscalls on the pipes (a read includes waiting for the other side)")
    idle_states(ev, wins, n)
    bad = run(["perf", "report", "-i", path, "--stat"])
    flagged = [l.strip() for l in bad.splitlines() if re.search(r"THROTTLE|LOST", l) and not re.search(r" 0 ", l)]
    print("\n   completeness: " + ("!! " + "; ".join(flagged) if flagged else "no throttled or lost samples"))


def idle_states(ev, wins, n):
    """Table 2c: which idle (C-)state each idle entry on CPUs 0-1 went to, and how long the CPU stayed."""
    EXIT = 4294967295
    rows = []
    for size, t0, t1 in wins:
        cnt, res, open_ = collections.Counter(), collections.defaultdict(list), {}
        for e in ev:
            if not (t0 <= e["t"] < t1) or e["ev"] != "power:cpu_idle" or e["cpu"] not in (0, 1):
                continue
            state = field(e["f"], "state")
            if state != EXIT:
                open_[e["cpu"]] = (e["t"], state)
                cnt[state] += 1
            elif e["cpu"] in open_:
                t_in, s_in = open_.pop(e["cpu"])
                res[s_in].append((e["t"] - t_in) * 1e6)
        rows.append([size, num(sum(cnt.values()) / n, 2),
                     ", ".join(f"C{k}:{v}" for k, v in sorted(cnt.items())) or "-",
                     ", ".join(f"C{k}={num(st.median(v), 1)}" for k, v in sorted(res.items()) if v) or "-"])
    table(["size", "entries/RTT", "states entered (count)", "median stay us (under tracing)"], rows,
          "2c. idle states entered on CPUs 0-1 (C0 = polling, higher = deeper sleep)")


# ---------------------------------------------------------------- 3. perf record: cycles
# Heuristic, NAME-BASED grouping of kernel symbols (first match wins). It answers "roughly which kind of
# work", not "exactly which function"; the raw top-function list is printed too.
CATEGORIES = [
    ("copying data (to/from pipe)", r"^(_copy_to_iter|_copy_from_iter|copy_page_to_iter|copy_page_from_iter|"
                                    r"copy_user|rep_movs|copy_mc|__copy|memcpy|__memcpy|memmove|__memmove)"),
    ("pipe read/write logic", r"^(anon_pipe|pipe_|__pipe|splice)"),
    ("page alloc/free + memcg", r"(alloc_page|__alloc|free_page|__free|free_unref|get_page_from_freelist|rmqueue|"
                                r"prep_new_page|kernel_init_pages|clear_page|page_counter|memcg|mem_cgroup|"
                                r"obj_cgroup|uncharge|charge|folio|put_page|zone_)"),
    ("locking", r"(mutex|_raw_spin|spin_|rwsem|osq_lock|__lock)"),
    ("scheduler, wakeup, idle", r"(schedule|try_to_wake_up|ttwu|wake_up|__wake|enqueue|dequeue|pick_next|"
                                r"select_task|select_idle|context_switch|__switch_to|switch_mm|finish_task_switch|"
                                r"update_curr|set_next|put_prev|resched|sched_|do_idle|cpuidle|default_idle|mwait|"
                                r"intel_idle|poll_idle|arch_cpu_idle|newidle|cpupri|_rt_|pull_rt|push_rt|rt_mutex)"),
    ("interrupts, IPIs, timers", r"(smp_|call_function|sysvec|irq|apic|ipi|hrtimer|tick_|softirq|rcu_|native_write_msr|wrmsr|x2apic)"),
    ("syscall entry/exit, VFS, security", r"(entry_SYSCALL|do_syscall|syscall_|__x64_sys|x64_sys_call|ksys_|vfs_|"
                                          r"fdget|fput|__fget|rw_verify|security_|selinux|avc_|bpf_lsm|apparmor|"
                                          r"fsnotify|file_)"),
]
USER_CAT, OTHER_CAT, UNRES_CAT = "user space (benchmark + libc)", "other kernel", "unresolved symbols"
CAT_ORDER = [c for c, _ in CATEGORIES] + [OTHER_CAT, USER_CAT, UNRES_CAT]
REPORT_LINE = re.compile(r"^\s*([\d.]+)%\s+\[(.)\]\s+(.*\S)\s*$")


def categorize(kind, sym):
    if re.match(r"^0x[0-9a-f]+$", sym):
        return UNRES_CAT
    if kind == ".":
        return USER_CAT
    for name, pat in CATEGORIES:
        if re.search(pat, sym):
            return name
    return OTHER_CAT


def cycle_profile(path):
    """-> [(percent, kind, symbol)] for the cycles event of one perf.data (self time, no callees)."""
    out = run(["perf", "report", "-i", path, "--stdio", "--no-children", "-g", "none",
               "--sort", "sym", "--percent-limit", "0.01"])
    rows, in_cycles = [], False
    for line in out.splitlines():
        if line.startswith("# Samples"):
            in_cycles = "cycles" in line  # perf prints one block per event; keep only cycles
        elif in_cycles:
            m = REPORT_LINE.match(line)
            if m:
                rows.append((float(m.group(1)), m.group(2), m.group(3)))
    return rows


def untraced_min_rtt_us(d):
    """{size: (min RTT us, median RTT us)} over results/latency_run*.csv, the clean un-traced runs of latency-runner.sh.
    Deliberately NOT results/latency.csv: that is the old unpinned smoke test."""
    files = sorted(glob.glob(os.path.join(d, "..", "..", "..", "latency_run*.csv")))
    try:
        import analyze_latency as al
        runs = []
        for f in files:
            try:
                runs.append(al.load(f))
            except SystemExit:
                pass  # a traced/rtt_cycles or empty file
        if runs:
            return {r["size"]: (r["rtt_min"] / 1e3, r["median"] / 1e3) for r in al.summarize(runs)}, files
    except ImportError:
        pass
    return {}, files


def section_cycles(d, p):
    sizes = [int(x) for x in p.get("cycles_sizes", "").split()]
    paths = {sz: f"{d}/perf_cycles_{sz}.data" for sz in sizes if os.path.exists(f"{d}/perf_cycles_{sz}.data")}
    if not paths:
        return
    print("\n" + "=" * 100)
    print("3. WHERE THE CPU CYCLES GO (perf record cycles + call graphs, one profile per message size)")
    print("   The benchmark's processes only; no printing in the loop (-q). Categories are a heuristic,")
    print("   NAME-based grouping of kernel symbols; kernel symbols only resolve when this runs as root.")
    prof, tops, others = {}, {}, {}
    for sz, path in paths.items():
        rows = cycle_profile(path)
        cat = collections.defaultdict(float)
        for pc, kind, sym in rows:
            cat[categorize(kind, sym)] += pc
        tot = sum(cat.values()) or 1.0
        prof[sz] = {c: 100 * v / tot for c, v in cat.items()}
        tops[sz] = rows[:8]
        others[sz] = [r for r in rows if categorize(r[1], r[2]) == OTHER_CAT][:6]
    unres = max((prof[sz].get(UNRES_CAT, 0) for sz in prof), default=0)
    if unres > 30:
        print(f"   !! {unres:.0f}% of samples are unresolved kernel addresses - re-run this script with sudo")

    per, _ = counters_per_rtt(d, p)
    freq_ghz = int(p.get("freq_khz", 0)) / 1e6
    kcyc = {sz: (None if per.get(sz, {}).get("cycles") is None else per[sz]["cycles"] / 1e3) for sz in prof}
    szs = sorted(prof)
    label = {sz: human_size(sz) for sz in szs}

    rows = []
    for c in CAT_ORDER:
        if any(prof[sz].get(c, 0) >= 0.05 for sz in szs):
            row = [c]
            for sz in szs:
                sh = prof[sz].get(c, 0.0)
                row += [f"{sh:.1f}%", num(None if kcyc[sz] is None else kcyc[sz] * sh / 100, 2)]
            rows.append(row)
    hdr = ["component"]
    for sz in szs:
        hdr += [f"{label[sz]} %", f"{label[sz]} kcyc"]
    table(hdr, rows, "3a. cycles per round trip by component (both tasks; kcyc = thousands of cycles)")
    cpu_vs_rtt(d, p, szs, hdr, kcyc, label, freq_ghz)
    for sz in szs:
        print(f"\n3c. top functions, {label[sz]} messages (% of cycles)")
        for pc, kind, sym in tops[sz]:
            print(f"    {pc:6.2f}%  [{kind}] {sym}   <{categorize(kind, sym)}>")
        if others[sz] and prof[sz].get(OTHER_CAT, 0) > 5:
            print(f"    largest symbols left in 'other kernel' ({prof[sz][OTHER_CAT]:.0f}%): " +
                  ", ".join(f"{sym} {pc:.1f}%" for pc, _, sym in others[sz]))
    chart_breakdown(d, szs, prof, kcyc, label)


def cpu_vs_rtt(d, p, szs, hdr, kcyc, label, freq_ghz):
    """Table 3b. perf stat counts the AVERAGE round trip, so the fair wall-clock comparison is the untraced
    MEDIAN; the minimum (a best case, sometimes a single lucky iteration) is shown for reference."""
    def cells(f):
        return sum(([f(sz), ""] for sz in szs), [])
    cpu_us = {sz: None if kcyc[sz] is None else kcyc[sz] / freq_ghz for sz in szs}
    rows = [["CPU work, total (perf stat)"] + cells(lambda sz: num(kcyc[sz], 1)),
            [f"  = us at {freq_ghz:.1f} GHz"] + cells(lambda sz: num(cpu_us[sz], 2))]
    rtt, files = untraced_min_rtt_us(d)
    if rtt:
        rows.append(["untraced RTT, median (us)"] + cells(lambda sz: num(rtt[sz][1] if sz in rtt else None, 2)))
        rows.append(["  => not on a CPU (us)"] + cells(
            lambda sz: num(None if cpu_us[sz] is None or sz not in rtt else rtt[sz][1] - cpu_us[sz], 2)))
        rows.append(["  untraced RTT, min (us)"] + cells(lambda sz: num(rtt[sz][0] if sz in rtt else None, 2)))
    table(hdr, rows, "3b. CPU work against the measured round trip")
    if rtt:
        import time
        f = files[-1]
        print(f"   untraced RTT: over {len(files)} file(s) results/latency_run*.csv (newest modified "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(f)))}).")
        print("   'not on a CPU' = time when NEITHER task was running (idle exits, IPI flight, wakeup latency),")
        print("   median RTT minus CPU work. Meaningful when the two tasks alternate. For messages above the")
        print("   64 KB pipe buffer both cores work at once, so CPU work can exceed the round trip and the value")
        print("   goes negative: overlap, not an error. At 4 B the two tasks also overlap a little (the parent")
        print("   is still returning from write() while the child wakes), so it understates the idle gap.")
    else:
        print("   (no results/latency_run*.csv found, so no comparison with the measured round trip)")


def human_size(sz):
    return f"{sz // 1024} KB" if sz >= 1024 else f"{sz} B"


def chart_breakdown(d, szs, prof, kcyc, label):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    colors = ["#1f5fbf", "#e67e22", "#8e44ad", "#c0392b", "#16a085", "#7f8c8d", "#f1c40f", "#bdc3c7", "#2c3e50", "#95a5a6"]
    plt.rcParams.update({"font.size": 10, "axes.labelsize": 11})
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    bottom = [0.0] * len(szs)
    for c, col in zip(CAT_ORDER, colors):
        vals = [prof[sz].get(c, 0.0) for sz in szs]
        if max(vals) < 0.5:
            continue
        ax.bar(range(len(szs)), vals, bottom=bottom, color=col, edgecolor="white", width=0.6, label=c)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, sz in enumerate(szs):
        if kcyc[sz] is not None:
            ax.text(i, 101, f"{kcyc[sz]:,.1f}k cycles", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(range(len(szs)))
    ax.set_xticklabels([label[sz] for sz in szs])
    ax.set_xlabel("Message size")
    ax.set_ylabel("Share of CPU cycles per round trip (%)")
    ax.set_ylim(0, 108)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8, frameon=False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"{d}/breakdown.{ext}", dpi=200)
    plt.close(fig)
    print(f"\n   chart: {d}/breakdown.png (+ .pdf)")


# ---------------------------------------------------------------- 4. strace
STRACE = re.compile(r"(\d+\.\d+) \(\+\s*([\d.]+)\) \[\s*\d+\] \[[0-9a-f]+\] (\w+)\((.*)\)\s+=\s+(-?\d+|\?)(.*?)(?:<([\d.]+)>)?\s*$")


def parse_strace(path):
    out = []
    for line in open(path, errors="replace"):
        if line.startswith(" >"):
            continue
        m = STRACE.match(line)
        if m:
            out.append(dict(t=float(m.group(1)), name=m.group(3), args=m.group(4), dur=float(m.group(7) or 0)))
    return out


def count_of(x):
    m = re.search(r"count=(\d+)", x["args"])
    return int(m.group(1)) if m else None


def section_strace(d, p):
    files = sorted(glob.glob(f"{d}/strace_*.*"))
    if not files:
        return
    sizes = [int(s) for s in p.get("sizes", "").split()]
    n = int(p.get("trace_warmup", 0)) + int(p.get("trace_iterations", 0))
    procs = {f: parse_strace(f) for f in files}
    # the benchmark's processes are the ones writing benchmark-sized messages to pipes; the parent is
    # the one that forks and waits for the child
    procs = {f: s for f, s in procs.items()
             if sum(x["name"] == "write" and "pipe:" in x["args"] and count_of(x) in sizes for x in s) >= 20}
    parent = next((f for f, s in procs.items() if any(x["name"] in ("wait4", "waitid") for x in s)), None)
    print("\n" + "=" * 100)
    print("4. SYSCALLS (strace - every call; strace itself slows the run ~5x, so use COUNTS, not times)")
    for f, s in procs.items():
        role = "parent" if f == parent else "child "
        c = collections.Counter(x["name"] for x in s)
        print(f"   {role} {os.path.basename(f)}: {len(s)} syscalls; top: " +
              ", ".join(f"{k} x{v}" for k, v in c.most_common(5)))
    if not parent or not n:
        return

    def pipe_ops(s, name):
        return [x for x in s if x["name"] == name and "pipe:" in x["args"]]

    par = procs[parent]
    starts, seen = [], set()
    for x in pipe_ops(par, "write"):  # the parent's pipe writes mark the start of each size
        sz = count_of(x)
        if sz in sizes and sz not in seen:
            seen.add(sz)
            starts.append((sz, x["t"]))
    last = max(x["t"] for s in procs.values() for x in s)
    wins = [(sz, t, starts[i + 1][1] if i + 1 < len(starts) else last + 1) for i, (sz, t) in enumerate(starts)]
    rows = []
    child = [f for f in procs if f != parent]
    for sz, t0, t1 in wins:
        row = [sz]
        for f in [parent] + child[:1]:
            s = procs[f]
            rd = [x for x in pipe_ops(s, "read") if t0 <= x["t"] < t1]
            wr = [x for x in pipe_ops(s, "write") if t0 <= x["t"] < t1]
            row += [num(len(wr) / n, 2), num(len(rd) / n, 2)]
        rows.append(row)
    table(["size", "parent wr", "parent rd", "child wr", "child rd"], rows,
          "4a. pipe syscalls per round trip. A write is 1 call (it blocks until the data fits); a read returns\n"
          "    at most what the 64 KB pipe holds, so reads per message = ceil(size / 65536)")


# ---------------------------------------------------------------- 5. ftrace
# One function_graph line: entry lines carry a duration only for leaf calls; "{" opens a call that
# a later "}" closes with its duration. Durations over 10 us carry a marker character (+ ! # @ * $) in
# front - missing that once made the parser drop every long (i.e. blocking) call. After the "|" there is one space of padding plus two per level.
FG = re.compile(r"\s*(\S+)-(\d+)\s+\[(\d+)\]\s+\S+\s+([\d.]+): funcgraph_(entry|exit):"
                r"\s*(?:[+!#@*$]\s+)?(?:([\d.]+) (ns|us|ms))?\s*\|( *)(.*)$")
UNIT = {"ns": 1e-3, "us": 1.0, "ms": 1e3}


def section_ftrace(d, p):
    path = f"{d}/trace.dat"
    if not os.path.exists(path):
        return
    text = run(["trace-cmd", "report", "-i", path])
    stacks = collections.defaultdict(list)                  # pid -> open calls [indent, name, child_us]
    fn = collections.defaultdict(lambda: [0, 0.0, 0.0])     # name -> calls, inclusive us, self us
    top = collections.defaultdict(list)                     # top-level call -> durations (us)
    comms, dropped = collections.Counter(), 0
    top_indent = None
    for line in text.splitlines():
        if "EVENTS DROPPED" in line:
            dropped += 1
            continue
        m = FG.match(line)
        if not m:
            continue
        comm, pid, _, _, kind, val, unit, indent, rest = m.groups()
        comms[comm] += 1
        ind = len(indent)
        dur = float(val) * UNIT[unit] if val else None
        st_ = stacks[pid]
        if kind == "entry":
            name = rest.split("(")[0].strip()
            if rest.rstrip().endswith("{"):
                st_.append([ind, name, 0.0])
                top_indent = ind if top_indent is None else min(top_indent, ind)
            elif dur is not None:                            # leaf call
                fn[name][0] += 1; fn[name][1] += dur; fn[name][2] += dur
                if st_:
                    st_[-1][2] += dur
        elif rest.strip().startswith("}") and dur is not None:
            while st_ and st_[-1][0] > ind:                  # calls whose exit we never saw
                st_.pop()
            if st_ and st_[-1][0] == ind:
                _, name, kids = st_.pop()
                fn[name][0] += 1; fn[name][1] += dur; fn[name][2] += max(dur - kids, 0.0)
                if st_:
                    st_[-1][2] += dur
                if ind == top_indent:
                    top[name].append(dur)
    print("\n" + "=" * 100)
    print("5. KERNEL CALL GRAPH under ksys_read / ksys_write (ftrace function_graph, the benchmark only)")
    print(f"   entries by task: {dict(comms.most_common(4))};  'events dropped' notices: {dropped}")
    print("   Durations are inflated by function_graph's own overhead (every function entry/exit is hooked):")
    print("   compare functions with each other, do not read the microseconds as real costs.")
    rows, total = [], sum(sum(v) for v in top.values())
    for name in ("ksys_read", "ksys_write"):
        v = top.get(name)
        if v:
            rows.append([name, len(v), num(st.median(v), 2), num(max(v), 1), num(sum(v) / 1e3, 2)])
    if rows:
        table(["call", "count", "median us", "max us", "total ms"], rows, "5a. top-level calls (pipe read / write)")
    inner = sorted(((k, c, i, sf) for k, (c, i, sf) in fn.items() if c >= 5), key=lambda x: -x[3])[:14]
    if total:
        table(["function", "calls", "self ms", "% of read+write time"],
              [[k, c, num(sf / 1e3, 2), num(100 * sf / total, 1) + "%"] for k, c, i, sf in inner],
              "5b. where the time goes, by SELF time (time in the function itself, not its callees)")


def section_wall_time(d, p):
    """Section 6: for every size, how much of the (untraced, median) round trip the two tasks were on a CPU."""
    per, _ = counters_per_rtt(d, p)
    rtt, files = untraced_min_rtt_us(d)
    freq_ghz = int(p.get("freq_khz", 0)) / 1e6
    if not per or not rtt or not freq_ghz:
        return
    rows = []
    for sz in sorted(per):
        cy = per[sz].get("cycles")
        if cy is None or sz not in rtt:
            continue
        cpu, med = cy / freq_ghz / 1e3, rtt[sz][1]
        wk = STATE["wakeups"].get(sz)
        rows.append([human_size(sz), num(med, 2), num(cpu, 2), num(med - cpu, 2), num(100 * (med - cpu) / med, 0) + "%",
                     num(wk, 1), num((med - cpu) / wk, 2) if wk else "n/a"])
    print("\n" + "=" * 100)
    print("6. WHERE THE WALL-CLOCK TIME GOES: CPU work vs time when neither task was running")
    print(f"   Median untraced round trip (results/latency_run*.csv, {len(files)} file(s)) against the CPU work perf stat")
    print(f"   counted per round trip at {freq_ghz:.1f} GHz. perf stat counts the AVERAGE round trip, hence the median.")
    table(["size", "median RTT us", "CPU work us", "both idle us", "idle %", "wakeups/RTT", "idle per wakeup us"], rows,
          "6a. per message size")
    print("   'both idle' = median RTT - CPU work: idle exits, IPI flight, wakeup latency. Meaningful when the two")
    print("   tasks alternate; above the 64 KB pipe buffer both cores work at once. At 4 B a little overlap")
    print("   (the parent still returning from write() while the child wakes) makes it understate the gap.")
    print("   wakeups/RTT come from the tracepoint capture; 'n/a' if that capture is missing.")


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit(f"usage: {sys.argv[0]} <run_dir>")
    d = sys.argv[1].rstrip("/")
    p = read_params(d)
    print(f"TRACE SUMMARY  {os.path.basename(os.path.dirname(d))}/{os.path.basename(d)}   "
          f"(kernel {p.get('kernel', '?')}, CPUs pinned to {int(p.get('freq_khz', 0)) // 1000} MHz)")
    for f in (section_counters, section_tracepoints, section_cycles, section_strace, section_ftrace,
              section_wall_time):
        try:
            f(d, p)
        except Exception as e:  # a broken section must not hide the others
            print(f"\n!! {f.__name__} failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
