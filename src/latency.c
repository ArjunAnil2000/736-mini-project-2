/**
 * latency: fork + two pipes, round-trip a buffer of each SIZES entry, timed with rdtsc.
 *
 * Same parent/child/pipe structure as round-trip-pipes.c. For each size: WARMUP untimed
 * rounds (also used to verify correctness via memcmp, since that check has no business being
 * in the timed path), then ITERATIONS timed rounds. Only the parent reads rdtsc, bracketing
 * the full round trip - safe regardless of which core the child runs on, since latency is a
 * time difference between two reads made by the same process. One-way latency = RTT / 2.
 *
 * Prints raw per-iteration CSV (size_bytes,iteration,rtt_ns) to stdout; summary stats (min,
 * etc.) are computed from that afterward, not baked in here.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include <x86intrin.h>
#include "common.h"

#define PARENT_CPU 0
#define CHILD_CPU 1

#define WARMUP 20
#define ITERATIONS 2000

static void child_main(int a2b_read, int b2a_write, size_t max_size)
{
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];
        for (int r = 0; r < WARMUP + ITERATIONS; r++) {
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
                        double tsc_hz)
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

        for (int w = 0; w < WARMUP; w++) {
            full_write(a2b_write, send_buf, sz);
            full_read(b2a_read, recv_buf, sz);
            if (memcmp(send_buf, recv_buf, sz) != 0) {
                fprintf(stderr, "MISMATCH at size %zu bytes (warmup)\n", sz);
                exit(1);
            }
        }

        for (int it = 0; it < ITERATIONS; it++) {
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

int main(void)
{
    pin_to_cpu(PARENT_CPU);
    double tsc_hz = calibrate_tsc_hz();

    size_t max_size = SIZES[NUM_SIZES - 1];

    int a2b[2]; /* parent -> child */
    int b2a[2]; /* child -> parent */
    if (pipe(a2b) < 0 || pipe(b2a) < 0) {
        perror("pipe");
        exit(1);
    }

    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        exit(1);
    }

    if (pid == 0) {
        close(a2b[1]);
        close(b2a[0]);
        child_main(a2b[0], b2a[1], max_size);
        /* never reached, child_main() exits */
    }

    close(a2b[0]);
    close(b2a[1]);
    parent_main(a2b[1], b2a[0], max_size, pid, tsc_hz);

    return 0;
}
