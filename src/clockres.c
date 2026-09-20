#define _GNU_SOURCE
/**
 * clockres: measure the resolution of gettimeofday and several clock_gettime() clocks.
 *
 * Accomplish this in 2 ways:
 * - read the clock twice back-to-back and see what the smallest non-zero difference is that we can
 *   observe. If two consecutive reads return the same value, the clock hasn't ticked yet (from our
 *   perspective) between them, so we keep reading until it changes.
 * - sleep() for 2s and wrap the various timers around it.
 *
 * Pinned to CPU via pin_to_cpu() (common.h). See pin_freq/unpin_freq scripts for pinning the
 * CPU's frequency too (required since DVFS can vary it).
 */
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include <sys/time.h>
#include <unistd.h>
#include "common.h"

#define CPU 0
#define TRIALS 2000

static uint64_t timeval_to_ns(struct timeval *tv) {
    return (uint64_t)tv->tv_sec * 1000000000ULL + (uint64_t)tv->tv_usec * 1000ULL;
}

static double clock_gettime_resolution_ns(clockid_t clk) {
    uint64_t min_diff = UINT64_MAX;
    for (int i = 0; i < TRIALS; i++) {
        struct timespec a, b;
        clock_gettime(clk, &a);
        clock_gettime(clk, &b);
        while (a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec)
            clock_gettime(clk, &b);
        uint64_t d = timespec_to_ns(&b) - timespec_to_ns(&a);
        if (d < min_diff) min_diff = d;
    }
    return (double)min_diff;
}

static double gettimeofday_resolution_ns(void) {
    uint64_t min_diff = UINT64_MAX;
    for (int i = 0; i < TRIALS; i++) {
        struct timeval a, b;
        gettimeofday(&a, NULL);
        gettimeofday(&b, NULL);
        while (a.tv_sec == b.tv_sec && a.tv_usec == b.tv_usec)
            gettimeofday(&b, NULL);
        uint64_t d = timeval_to_ns(&b) - timeval_to_ns(&a);
        if (d < min_diff) min_diff = d;
    }
    return (double)min_diff;
}

static unsigned long long rdtsc_resolution_cycles(void) {
    unsigned long long min_diff = UINT64_MAX;
    for (int i = 0; i < TRIALS; i++) {
        unsigned long long a, b;
        a = __rdtsc();
        b = __rdtsc();
        while (a == b)
            b = __rdtsc();
        unsigned long long d = b - a;
        if (d < min_diff) min_diff = d;
    }
    return min_diff;
}

int main(void) {
    pin_to_cpu(CPU);

    printf("\n=== Timer resolution (smallest observed non-zero delta, %d trials) ===\n", TRIALS);

    double cg_ns = clock_gettime_resolution_ns(CLOCK_MONOTONIC);
    printf("clock_gettime(CLOCK_MONOTONIC):             %.1f ns\n", cg_ns);

    double cg_coarse_ns = clock_gettime_resolution_ns(CLOCK_MONOTONIC_COARSE);
    printf("clock_gettime(CLOCK_MONOTONIC_COARSE):      %.1f ns\n", cg_coarse_ns);

    double cg_cputime_ns = clock_gettime_resolution_ns(CLOCK_PROCESS_CPUTIME_ID);
    printf("clock_gettime(CLOCK_PROCESS_CPUTIME_ID):    %.1f ns\n", cg_cputime_ns);

    double gtod_ns = gettimeofday_resolution_ns();
    printf("gettimeofday:                               %.1f ns\n", gtod_ns);

    unsigned long long rdtsc_cycles = rdtsc_resolution_cycles();
    printf("rdtsc_cycles:                               %llu cycles\n", rdtsc_cycles);

    double tsc_hz = calibrate_tsc_hz();
    printf("calibrated TSC frequency:                   %.3f GHz\n", tsc_hz / 1e9);
    printf("rdtsc resolution (calibrated):              %.1f ns\n",
           (double)rdtsc_cycles / tsc_hz * 1e9);

    printf("\n=== Accuracy check: sleep(2) ===\n");
    struct timespec ts0, ts1, ts0_coarse, ts1_coarse, ts0_cpu, ts1_cpu;
    struct timeval tv0, tv1;
    clock_gettime(CLOCK_MONOTONIC, &ts0);
    clock_gettime(CLOCK_MONOTONIC_COARSE, &ts0_coarse);
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts0_cpu);
    gettimeofday(&tv0, NULL);
    unsigned long long c0 = __rdtsc();

    sleep(2);

    unsigned long long c1 = __rdtsc();
    clock_gettime(CLOCK_MONOTONIC, &ts1);
    clock_gettime(CLOCK_MONOTONIC_COARSE, &ts1_coarse);
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts1_cpu);
    gettimeofday(&tv1, NULL);

    uint64_t cg_ns_elapsed = timespec_to_ns(&ts1) - timespec_to_ns(&ts0);
    uint64_t cg_coarse_ns_elapsed = timespec_to_ns(&ts1_coarse) - timespec_to_ns(&ts0_coarse);
    uint64_t cg_cpu_ns_elapsed = timespec_to_ns(&ts1_cpu) - timespec_to_ns(&ts0_cpu);
    uint64_t gtod_ns_elapsed = timeval_to_ns(&tv1) - timeval_to_ns(&tv0);
    double rdtsc_ns_elapsed = (double)(c1 - c0) / tsc_hz * 1e9;

    printf("clock_gettime(CLOCK_MONOTONIC) measured:          %llu ns\n",
           (unsigned long long)cg_ns_elapsed);
    printf("clock_gettime(CLOCK_MONOTONIC_COARSE) measured:   %llu ns\n",
           (unsigned long long)cg_coarse_ns_elapsed);
    printf("gettimeofday measured:                            %llu ns\n",
           (unsigned long long)gtod_ns_elapsed);
    printf("clock_gettime(CLOCK_PROCESS_CPUTIME_ID) measured: %llu ns"
           " (expected ~0: process was blocked in sleep(), not consuming CPU)\n",
           (unsigned long long)cg_cpu_ns_elapsed);
    printf("rdtsc (calibrated) measured:                      %.1f ns\n", rdtsc_ns_elapsed);

    printf("\n=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=\n\n");

    return 0;
}
