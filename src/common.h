/* common.h: shared CPU pinning + TSC timing helpers. */
#ifndef COMMON_H
#define COMMON_H

#ifndef _GNU_SOURCE
#error "define _GNU_SOURCE as the first line of your .c file before including common.h"
#endif

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sched.h>
#include <x86intrin.h>

#define CALIBRATION_TRIALS 10

/* pins a particular process to a CPU.
 * NOTE: This does NOT pin the CPU freq.
 */
static inline void pin_to_cpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        perror("sched_setaffinity");
        exit(1);
    }
}

static inline uint64_t timespec_to_ns(struct timespec *ts) {
    return (uint64_t)ts->tv_sec * 1000000000ULL + (uint64_t)ts->tv_nsec;
}

// Calibrates TSC Hz via CLOCK_MONOTONIC; min of CALIBRATION_TRIALS bracketed samples.
static inline double calibrate_tsc_hz(void) {
    double min_hz = __DBL_MAX__;
    for (int trial = 0; trial < CALIBRATION_TRIALS; trial++) {
        struct timespec ts0a, ts0b, ts1a, ts1b;

        clock_gettime(CLOCK_MONOTONIC, &ts0a);
        unsigned long long c0 = __rdtsc();
        clock_gettime(CLOCK_MONOTONIC, &ts0b);

        struct timespec req = {0, 100 * 1000 * 1000}; /* 100 ms */
        nanosleep(&req, NULL);

        clock_gettime(CLOCK_MONOTONIC, &ts1a);
        unsigned long long c1 = __rdtsc();
        clock_gettime(CLOCK_MONOTONIC, &ts1b);

        double ts0_mid_ns = (double)(timespec_to_ns(&ts0a) + timespec_to_ns(&ts0b)) / 2.0;
        double ts1_mid_ns = (double)(timespec_to_ns(&ts1a) + timespec_to_ns(&ts1b)) / 2.0;

        double secs = (ts1_mid_ns - ts0_mid_ns) / 1e9;
        double hz = (double)(c1 - c0) / secs;
        if (hz < min_hz) min_hz = hz;
    }
    return min_hz;
}

#endif
