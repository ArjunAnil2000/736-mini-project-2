# Laptop specs

## Kernel / OS

```
$ uname -a
Linux fedora 7.1.8-200.fc44.x86_64 #1 SMP PREEMPT_DYNAMIC Mon Aug 10 03:35:23 UTC 2026 x86_64 GNU/Linux
```

```
$ cat /etc/os-release | grep -E "^(NAME|VERSION)="
NAME="Fedora Linux"
VERSION="44 (Sway)"
```

## CPU

```
$ lscpu
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           42 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  8
On-line CPU(s) list:                     0-7
Vendor ID:                               GenuineIntel
Model name:                              Intel(R) Core(TM) Ultra 7 258V
CPU family:                              6
Model:                                   189
Thread(s) per core:                      1
Core(s) per socket:                      8
Socket(s):                               1
Stepping:                                1
CPU max MHz:                             4800.0000
CPU min MHz:                             400.0000
BogoMIPS:                                6604.80
L1d cache:                               320 KiB (8 instances)
L1i cache:                               512 KiB (8 instances)
L2 cache:                                14 MiB (5 instances)
L3 cache:                                12 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-7
```


```
$ lscpu -e
CPU NODE SOCKET CORE L1d:L1i:L2:L3 ONLINE    MAXMHZ   MINMHZ       MHZ
  0    0      0    0 0:0:0:0          yes 4800.0000 400.0000 3446.0830
  1    0      0    1 4:4:1:0          yes 4800.0000 400.0000  399.1030
  2    0      0    2 8:8:2:0          yes 4700.0000 400.0000  936.9510
  3    0      0    3 12:12:3:0        yes 4700.0000 400.0000 4412.4570
  4    0      0    4 64:64:8          yes 3700.0000 400.0000 1425.3350
  5    0      0    5 66:66:8          yes 3700.0000 400.0000 3700.6750
  6    0      0    6 68:68:8          yes 3700.0000 400.0000 3701.0420
  7    0      0    7 70:70:8          yes 3700.0000 400.0000 3638.0259
```

This is a hybrid CPU (intel lunar lake, core ultra 7 258V): 0-3 are P-cores, with max 4700-4800 MHz while 4-7 are E-cores with max 3700 MHz. What this means is that I would have to pin the C programs ran as part of this assignment to one of the CPUs. 

This is a hybrid CPU (Intel Lunar Lake, Core Ultra 7 258V): CPUs 0-3 are P-cores (max
4700-4800 MHz), CPUs 4-7 are E-cores (max 3700 MHz). Because the assignment warns that
`rdtsc` values are only comparable **on the same core**, and the scheduler is free to migrate
our processes between P- and E-cores, the benchmark binaries should be (and were) pinned to a
single core with `taskset` during measurement to avoid cross-core migration skewing results.


## Frequency scaling / power state


```
$ for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo -n "$f: "; cat "$f"; done
/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu1/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu2/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu3/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu5/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu6/cpufreq/scaling_governor: powersave
/sys/devices/system/cpu/cpu7/cpufreq/scaling_governor: powersave
```

```
$ cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver
intel_pstate
```

```
$ cat /sys/devices/system/cpu/intel_pstate/no_turbo
0
```
(`0` = turbo boost enabled)

```
$ cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq
400000
$ cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq
4800000
$ cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
400000
```

CPU 0's full allowed range is currently open (400 MHz - 4800 MHz) under `powersave`, and it was sitting idle at 400 MHz at the moment of capture. This is the exact kind of variance `scripts/pin_freq.sh` / `scripts/clockres-runner.sh` exist to eliminate before measuring.

## Page size / pipe buffer limits

```
$ getconf PAGE_SIZE
4096
```

```
$ getconf PIPE_BUF /
TODO: RUN THIS.
```

```
$ cat /proc/sys/fs/pipe-max-size
1048576
```

```
$ python3 -c "
import fcntl, os
r, w = os.pipe()
print(fcntl.fcntl(w, 1032))   # F_GETPIPE_SZ
"
65536
```

The default per-pipe kernel buffer is 65536 bytes (64 KiB), even though the system-wide max (`fs.pipe-max-size`) allows up to 1 MiB. This 64 KiB threshold is directly relevant to the throughput/latency results: message sizes above it require multiple write/read syscalls per "logical" message and can force the writer to block on a full buffer, which shows up as extra context switches in our `getrusage` measurements.

## Toolchain versions used to build/run the benchmarks

```
$ gcc --version | head -1
gcc (GCC) 16.1.1 20260515 (Red Hat 16.1.1-2)
```

```
$ ldd --version | head -1
ldd (GNU libc) 2.43
```

```
$ perf --version
perf version 7.2.5-200.fc44.x86_64
```

```
$ strace --version | head -1
strace -- version 7.2
```

