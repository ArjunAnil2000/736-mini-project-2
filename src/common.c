#define _GNU_SOURCE
#include "common.h"

#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <x86intrin.h>

#define CALIBRATION_TRIALS 10

const size_t SIZES[NUM_SIZES] = {4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 524288};

/* Set once the user agrees to run without SCHED_FIFO; survives fork(), so the child won't ask. */
static int rt_declined;

/* Prompts on stderr (stdout carries the CSV). EOF or a non-tty stdin counts as "no". */
static int
confirm_continue(void)
{
    char line[16];
    fprintf(stderr, "Continue without SCHED_FIFO? Results will be noisier. [y/N] ");
    if (!fgets(line, sizeof(line), stdin)) return 0;
    return line[0] == 'y' || line[0] == 'Y';
}

void
pin_to_cpu(int cpu)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        perror("sched_setaffinity");
        exit(1);
    }
    if (rt_declined) return;

    struct sched_param param = { 99 };
    if (sched_setscheduler(0, SCHED_FIFO, &param) != 0) {
        perror("sched_setscheduler");
        if (!confirm_continue()) exit(1);
        rt_declined = 1;
    }
}

void
set_pipe_length(int fd)
{
    if (fcntl(fd, F_SETPIPE_SZ, PIPE_LENGTH) == -1) {
        perror("F_SETPIPE_SZ");
    }
}

uint64_t
timespec_to_ns(struct timespec *ts)
{
    return (uint64_t)ts->tv_sec * 1000000000ULL + (uint64_t)ts->tv_nsec;
}

/* Calibrates TSC Hz via CLOCK_MONOTONIC; min of CALIBRATION_TRIALS bracketed samples. */
double
calibrate_tsc_hz(void)
{
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

void
full_write(int fd, const char *buf, size_t n)
{
    size_t written = 0;
    while (written < n) {
        ssize_t w = write(fd, buf + written, n - written);
        if (w < 0) {
            perror("write");
            exit(1);
        }
        written += (size_t)w;
    }
}

void
full_read(int fd, char *buf, size_t n)
{
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r <= 0) {
            perror("read");
            exit(1);
        }
        got += (size_t)r;
    }
}
