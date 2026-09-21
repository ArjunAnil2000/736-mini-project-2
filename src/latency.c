/**
 * latency: fork + two pipes, round-trip a buffer of each SIZES entry, timed with rdtsc.
 *
 * Same parent/child/pipe structure as round-trip-pipes.c. For each size: `warmup` untimed
 * rounds (also used to verify correctness via memcmp, since that check has no business being
 * in the timed path), then `iterations` timed rounds. Only the parent reads rdtsc, bracketing
 * the full round trip - safe regardless of which core the child runs on, since latency is a
 * time difference between two reads made by the same process. One-way latency = RTT / 2.
 *
 * Prints raw per-iteration CSV (size_bytes,iteration,rtt_ns) to stdout; summary stats (min,
 * etc.) are computed from that afterward, not baked in here.
 *
 * Usage: latency [-s size_bytes] [-n] [-q] [warmup iterations]
 *   warmup iterations  defaults: DEFAULT_WARMUP / DEFAULT_ITERATIONS
 *   -s size_bytes      run only this message size (must be one of SIZES); default is all of them.
 *                      Used by the tracing scripts to measure one size at a time.
 *   -n                 skip the ~1 s TSC calibration (10 x 100 ms sleeps). rtt values are then raw
 *                      TSC cycles and the CSV column is named rtt_cycles instead of rtt_ns. For
 *                      perf/strace/ftrace runs, where the calibration would otherwise be ~99% of
 *                      the run and drown the benchmark in the measurements.
 *   -q                 quiet: no per-iteration CSV rows on stdout, only one line per size on stderr
 *                      (the minimum rtt). In a traced run the printf() per iteration would otherwise
 *                      show up in the counters and the cycle profile as if it were pipe cost.
 */
#include <stdio.h>
#include <stdlib.h>
#include <limits.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <x86intrin.h>
#include "common.h"

#define PARENT_CPU 0
#define CHILD_CPU 1

#define DEFAULT_WARMUP 20
#define DEFAULT_ITERATIONS 2000

struct config {
    size_t sizes[NUM_SIZES]; /* message sizes to run, in order */
    size_t nsizes;
    size_t max_size;         /* buffer size needed: the largest entry of sizes */
    int warmup;
    int iterations;
    int calibrate;           /* 0: skip TSC calibration, report rtt in raw cycles */
    int quiet;               /* 1: no per-iteration output (see -q) */
};

static int parse_count(const char *s, const char *name, long min)
{
    char *end;
    long v = strtol(s, &end, 10);
    if (*s == '\0' || *end != '\0' || v < min || v > INT_MAX) {
        fprintf(stderr, "invalid %s '%s' (must be an integer >= %ld)\n", name, s, min);
        exit(1);
    }
    return (int)v;
}

static void usage(const char *prog)
{
    fprintf(stderr, "usage: %s [-s size_bytes] [-n] [-q] [warmup iterations]\n", prog);
    exit(1);
}

static void child_main(int a2b_read, int b2a_write, const struct config *cfg)
{
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(cfg->max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }

    for (size_t i = 0; i < cfg->nsizes; i++) {
        size_t sz = cfg->sizes[i];
        for (int r = 0; r < cfg->warmup + cfg->iterations; r++) {
            full_read(a2b_read, buf, sz);
            full_write(b2a_write, buf, sz);
        }
    }

    free(buf);
    close(a2b_read);
    close(b2a_write);
    exit(0);
}

static void parent_main(int a2b_write,
                        int b2a_read,
                        pid_t child_pid,
                        double tsc_hz,
                        const struct config *cfg)
{
    char *send_buf = malloc(cfg->max_size);
    char *recv_buf = malloc(cfg->max_size);
    if (!send_buf || !recv_buf) {
        perror("malloc");
        exit(1);
    }
    memset(send_buf, 0xAB, cfg->max_size);

    if (!cfg->quiet)
        printf(cfg->calibrate ? "size_bytes,iteration,rtt_ns\n" : "size_bytes,iteration,rtt_cycles\n");

    for (size_t i = 0; i < cfg->nsizes; i++) {
        size_t sz = cfg->sizes[i];

        for (int w = 0; w < cfg->warmup; w++) {
            full_write(a2b_write, send_buf, sz);
            full_read(b2a_read, recv_buf, sz);
            if (memcmp(send_buf, recv_buf, sz) != 0) {
                fprintf(stderr, "MISMATCH at size %zu bytes (warmup)\n", sz);
                exit(1);
            }
        }

        unsigned long long min_cycles = ~0ULL;
        for (int it = 0; it < cfg->iterations; it++) {
            unsigned long long c0 = __rdtsc();
            full_write(a2b_write, send_buf, sz);
            full_read(b2a_read, recv_buf, sz);
            unsigned long long c1 = __rdtsc();

            if (c1 - c0 < min_cycles)
                min_cycles = c1 - c0;
            if (cfg->quiet)
                continue;
            if (cfg->calibrate)
                printf("%zu,%d,%.1f\n", sz, it, (double)(c1 - c0) / tsc_hz * 1e9);
            else
                printf("%zu,%d,%llu\n", sz, it, c1 - c0);
        }
        if (cfg->quiet)
            fprintf(stderr, "size %zu: min rtt %llu cycles\n", sz, min_cycles);
    }

    close(a2b_write);
    close(b2a_read);
    free(send_buf);
    free(recv_buf);

    int status;
    waitpid(child_pid, &status, 0);
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        fprintf(stderr, "child exited abnormally (status=%d)\n", status);
        exit(1);
    }
}

static void parse_args(int argc, char **argv, struct config *cfg)
{
    cfg->warmup = DEFAULT_WARMUP;
    cfg->iterations = DEFAULT_ITERATIONS;
    cfg->calibrate = 1;
    cfg->quiet = 0;
    cfg->nsizes = NUM_SIZES;
    memcpy(cfg->sizes, SIZES, sizeof(cfg->sizes));

    int opt;
    while ((opt = getopt(argc, argv, "s:nq")) != -1) {
        switch (opt) {
        case 's': {
            size_t want = (size_t)parse_count(optarg, "size", 1);
            int found = 0;
            for (size_t i = 0; i < NUM_SIZES; i++)
                found |= (SIZES[i] == want);
            if (!found) {
                fprintf(stderr, "size %zu is not one of the benchmark sizes\n", want);
                exit(1);
            }
            cfg->sizes[0] = want;
            cfg->nsizes = 1;
            break;
        }
        case 'n':
            cfg->calibrate = 0;
            break;
        case 'q':
            cfg->quiet = 1;
            break;
        default:
            usage(argv[0]);
        }
    }

    int rest = argc - optind;
    if (rest == 2) {
        cfg->warmup = parse_count(argv[optind], "warmup", 0);
        cfg->iterations = parse_count(argv[optind + 1], "iterations", 1);
    } else if (rest != 0) {
        usage(argv[0]);
    }

    cfg->max_size = 0;
    for (size_t i = 0; i < cfg->nsizes; i++)
        if (cfg->sizes[i] > cfg->max_size)
            cfg->max_size = cfg->sizes[i];
}

int main(int argc, char **argv)
{
    struct config cfg;
    parse_args(argc, argv, &cfg);

    pin_to_cpu(PARENT_CPU);
    double tsc_hz = cfg.calibrate ? calibrate_tsc_hz() : 0.0;

    int a2b[2]; /* parent -> child */
    int b2a[2]; /* child -> parent */
    if (pipe(a2b) < 0 || pipe(b2a) < 0) {
        perror("pipe");
        exit(1);
    }

    // Given that a pipe is a uni-directional structure,
    // Setting one end will automatically set the other
    set_pipe_length(a2b[0]);
    set_pipe_length(b2a[0]);

    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        exit(1);
    }

    if (pid == 0) {
        close(a2b[1]);
        close(b2a[0]);
        child_main(a2b[0], b2a[1], &cfg);
        /* never reached, child_main() exits */
    }

    close(a2b[0]);
    close(b2a[1]);
    parent_main(a2b[1], b2a[0], pid, tsc_hz, &cfg);

    return 0;
}
