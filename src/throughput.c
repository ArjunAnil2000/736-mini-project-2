/**
 * throughput: fork + two pipes, one-way bulk transfer per size with a single ack at the end.
 *
 * Unlike latency.c's per-message round trip, the assignment's throughput protocol is
 * deliberately one-way: the parent streams `iters` chunks of a given size to the child with
 * no per-chunk reply, then waits for a single 1-byte ack once the child has consumed the
 * whole transfer. `iters` is picked per size so the total transferred is large enough that the
 * ack's cost is negligible relative to the whole transfer, while keeping runtime bounded at
 * small chunk sizes.
 *
 * rdtsc (only in the parent, same reasoning as latency.c) brackets the whole transfer + ack,
 * repeated `repeats` times per size; each repeat is printed as its own CSV row
 * (size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec; mb_per_sec is bytes / 2^20 per second).
 *
 * Usage: throughput [-s size_bytes] [-n] [-q] [-b total_bytes | -i chunks] [-r repeats]
 *   With no arguments: every size in SIZES, 64 MB per repeat (chunks clamped to [MIN_ITERS,
 *   MAX_ITERS]), DEFAULT_REPEATS repeats. This is what throughput-runner.sh runs.
 *   -s size_bytes   run only this chunk size (must be one of SIZES). Used by the tracing scripts
 *                   to measure one size at a time.
 *   -n              skip the ~1 s TSC calibration (10 x 100 ms sleeps). elapsed is then raw TSC
 *                   cycles and the CSV columns are elapsed_cycles,bytes_per_cycle instead of
 *                   elapsed_ns,mb_per_sec. For perf/strace/ftrace runs, where the calibration would
 *                   otherwise dominate the run and drown the benchmark in the measurements.
 *   -q              quiet: no CSV rows on stdout, one line per size on stderr (the best repeat). In a
 *                   traced run the printf() per row would otherwise be counted as if it were pipe cost.
 *   -b total_bytes  move this many bytes per repeat at every size: chunks = max(1, total_bytes / size),
 *                   with NO min/max clamp (traced runs want small, explicit volumes).
 *   -i chunks       move exactly this many chunks per repeat (so total bytes = size * chunks).
 *                   Mutually exclusive with -b.
 *   -r repeats      number of repeats per size (each ends with one ack).
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

#define TARGET_BYTES (64ULL * 1024 * 1024)
#define MIN_ITERS 64
#define MAX_ITERS 2000000
#define DEFAULT_REPEATS 3

struct config {
    size_t sizes[NUM_SIZES];       /* chunk sizes to run, in order */
    size_t nsizes;
    size_t max_size;               /* buffer size needed: the largest entry of sizes */
    unsigned long long total_bytes; /* -b: bytes per repeat (0 = not given) */
    size_t chunks;                 /* -i: chunks per repeat (0 = not given) */
    int repeats;
    int calibrate;                 /* 0: skip TSC calibration, report elapsed in raw cycles */
    int quiet;                     /* 1: no CSV rows (see -q) */
};

static unsigned long long parse_ull(const char *s, const char *name, unsigned long long min,
                                    unsigned long long max)
{
    char *end;
    unsigned long long v = strtoull(s, &end, 10);
    if (*s == '\0' || *s == '-' || *end != '\0' || v < min || v > max) {
        fprintf(stderr, "invalid %s '%s' (must be an integer in [%llu, %llu])\n", name, s, min, max);
        exit(1);
    }
    return v;
}

static void usage(const char *prog)
{
    fprintf(stderr, "usage: %s [-s size_bytes] [-n] [-q] [-b total_bytes | -i chunks] [-r repeats]\n", prog);
    exit(1);
}

static size_t iters_for_size(const struct config *cfg, size_t sz)
{
    if (cfg->chunks)
        return cfg->chunks;
    if (cfg->total_bytes) {
        size_t iters = cfg->total_bytes / sz;
        return iters < 1 ? 1 : iters;
    }
    size_t iters = TARGET_BYTES / sz;
    if (iters < MIN_ITERS) iters = MIN_ITERS;
    if (iters > MAX_ITERS) iters = MAX_ITERS;
    return iters;
}

static void child_main(int a2b_read, int b2a_write, const struct config *cfg)
{
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(cfg->max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }
    char ack = 1;

    for (size_t i = 0; i < cfg->nsizes; i++) {
        size_t sz = cfg->sizes[i];
        size_t iters = iters_for_size(cfg, sz);
        for (int rep = 0; rep < cfg->repeats; rep++) {
            for (size_t j = 0; j < iters; j++)
                full_read(a2b_read, buf, sz);
            full_write(b2a_write, &ack, 1);
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
    char *buf = malloc(cfg->max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }
    memset(buf, 0xAB, cfg->max_size);
    char ack;

    if (!cfg->quiet)
        printf(cfg->calibrate ? "size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec\n"
                              : "size_bytes,total_bytes,repeat,elapsed_cycles,bytes_per_cycle\n");

    for (size_t i = 0; i < cfg->nsizes; i++) {
        size_t sz = cfg->sizes[i];
        size_t iters = iters_for_size(cfg, sz);
        size_t total = sz * iters;
        unsigned long long best_cycles = ~0ULL;

        for (int rep = 0; rep < cfg->repeats; rep++) {
            unsigned long long c0 = __rdtsc();
            for (size_t j = 0; j < iters; j++)
                full_write(a2b_write, buf, sz);
            full_read(b2a_read, &ack, 1);
            unsigned long long c1 = __rdtsc();

            if (c1 - c0 < best_cycles)
                best_cycles = c1 - c0;
            if (cfg->quiet)
                continue;
            if (cfg->calibrate) {
                double elapsed_ns = (double)(c1 - c0) / tsc_hz * 1e9;
                double mb_per_sec = (double)total / (elapsed_ns / 1e9) / (1024.0 * 1024.0);
                printf("%zu,%zu,%d,%.1f,%.3f\n", sz, total, rep, elapsed_ns, mb_per_sec);
            } else {
                printf("%zu,%zu,%d,%llu,%.4f\n", sz, total, rep, c1 - c0,
                       (double)total / (double)(c1 - c0));
            }
        }
        if (cfg->quiet) {
            if (cfg->calibrate)
                fprintf(stderr, "size %zu: %zu chunks x %d repeats, best %.3f MiB/s\n", sz, iters,
                        cfg->repeats,
                        (double)total / ((double)best_cycles / tsc_hz) / (1024.0 * 1024.0));
            else
                fprintf(stderr, "size %zu: %zu chunks x %d repeats, best %llu cycles (%.4f bytes/cycle)\n",
                        sz, iters, cfg->repeats, best_cycles, (double)total / (double)best_cycles);
        }
    }

    close(a2b_write);
    close(b2a_read);
    free(buf);

    int status;
    waitpid(child_pid, &status, 0);
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        fprintf(stderr, "child exited abnormally (status=%d)\n", status);
        exit(1);
    }
}

static void parse_args(int argc, char **argv, struct config *cfg)
{
    cfg->repeats = DEFAULT_REPEATS;
    cfg->calibrate = 1;
    cfg->quiet = 0;
    cfg->total_bytes = 0;
    cfg->chunks = 0;
    cfg->nsizes = NUM_SIZES;
    memcpy(cfg->sizes, SIZES, sizeof(cfg->sizes));

    int opt;
    while ((opt = getopt(argc, argv, "s:nqb:i:r:")) != -1) {
        switch (opt) {
        case 's': {
            size_t want = (size_t)parse_ull(optarg, "size", 1, ULONG_MAX);
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
        case 'b':
            cfg->total_bytes = parse_ull(optarg, "total_bytes", 1, 1ULL << 40);
            break;
        case 'i':
            cfg->chunks = (size_t)parse_ull(optarg, "chunks", 1, 1ULL << 32);
            break;
        case 'r':
            cfg->repeats = (int)parse_ull(optarg, "repeats", 1, INT_MAX);
            break;
        default:
            usage(argv[0]);
        }
    }
    if (optind != argc)
        usage(argv[0]);
    if (cfg->total_bytes && cfg->chunks) {
        fprintf(stderr, "-b and -i are mutually exclusive\n");
        exit(1);
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

    int a2b[2]; /* parent -> child, bulk data */
    int b2a[2]; /* child -> parent, ack only */
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
