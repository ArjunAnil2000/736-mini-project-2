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
 * Usage: latency [warmup iterations]   (defaults: DEFAULT_WARMUP / DEFAULT_ITERATIONS)
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

static void child_main(int a2b_read, int b2a_write, size_t max_size, int warmup, int iterations)
{
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];
        for (int r = 0; r < warmup + iterations; r++) {
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
                        size_t max_size,
                        pid_t child_pid,
                        double tsc_hz,
                        int warmup,
                        int iterations)
{
    char *send_buf = malloc(max_size);
    char *recv_buf = malloc(max_size);
    if (!send_buf || !recv_buf) {
        perror("malloc");
        exit(1);
    }
    memset(send_buf, 0xAB, max_size);

    printf("size_bytes,iteration,rtt_ns\n");

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];

        for (int w = 0; w < warmup; w++) {
            full_write(a2b_write, send_buf, sz);
            full_read(b2a_read, recv_buf, sz);
            if (memcmp(send_buf, recv_buf, sz) != 0) {
                fprintf(stderr, "MISMATCH at size %zu bytes (warmup)\n", sz);
                exit(1);
            }
        }

        for (int it = 0; it < iterations; it++) {
            unsigned long long c0 = __rdtsc();
            full_write(a2b_write, send_buf, sz);
            full_read(b2a_read, recv_buf, sz);
            unsigned long long c1 = __rdtsc();

            double rtt_ns = (double)(c1 - c0) / tsc_hz * 1e9;
            printf("%zu,%d,%.1f\n", sz, it, rtt_ns);
        }
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

int main(int argc, char **argv)
{
    int warmup = DEFAULT_WARMUP;
    int iterations = DEFAULT_ITERATIONS;
    if (argc == 3) {
        warmup = parse_count(argv[1], "warmup", 0);
        iterations = parse_count(argv[2], "iterations", 1);
    } else if (argc != 1) {
        fprintf(stderr, "usage: %s [warmup iterations]\n", argv[0]);
        return 1;
    }

    pin_to_cpu(PARENT_CPU);
    double tsc_hz = calibrate_tsc_hz();

    size_t max_size = SIZES[NUM_SIZES - 1];

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
        child_main(a2b[0], b2a[1], max_size, warmup, iterations);
        /* never reached, child_main() exits */
    }

    close(a2b[0]);
    close(b2a[1]);
    parent_main(a2b[1], b2a[0], max_size, pid, tsc_hz, warmup, iterations);

    return 0;
}
