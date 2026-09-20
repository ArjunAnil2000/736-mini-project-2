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
 * repeated REPEATS times per size; each repeat is printed as its own CSV row.
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

#define TARGET_BYTES (64ULL * 1024 * 1024)
#define MIN_ITERS 64
#define MAX_ITERS 2000000
#define REPEATS 3

static size_t iters_for_size(size_t sz)
{
    size_t iters = TARGET_BYTES / sz;
    if (iters < MIN_ITERS) iters = MIN_ITERS;
    if (iters > MAX_ITERS) iters = MAX_ITERS;
    return iters;
}

static void child_main(int a2b_read, int b2a_write, size_t max_size)
{
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }
    char ack = 1;

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];
        size_t iters = iters_for_size(sz);
        for (int rep = 0; rep < REPEATS; rep++) {
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
                         size_t max_size,
                         pid_t child_pid,
                         double tsc_hz)
{
    char *buf = malloc(max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }
    memset(buf, 0xAB, max_size);
    char ack;

    printf("size_bytes,total_bytes,repeat,elapsed_ns,mb_per_sec\n");

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];
        size_t iters = iters_for_size(sz);
        size_t total = sz * iters;

        for (int rep = 0; rep < REPEATS; rep++) {
            unsigned long long c0 = __rdtsc();
            for (size_t j = 0; j < iters; j++)
                full_write(a2b_write, buf, sz);
            full_read(b2a_read, &ack, 1);
            unsigned long long c1 = __rdtsc();

            double elapsed_ns = (double)(c1 - c0) / tsc_hz * 1e9;
            double mb_per_sec = (double)total / (elapsed_ns / 1e9) / (1024.0 * 1024.0);
            printf("%zu,%zu,%d,%.1f,%.3f\n", sz, total, rep, elapsed_ns, mb_per_sec);
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

int main(void)
{
    pin_to_cpu(PARENT_CPU);
    double tsc_hz = calibrate_tsc_hz();

    size_t max_size = SIZES[NUM_SIZES - 1];

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
        child_main(a2b[0], b2a[1], max_size);
        /* never reached, child_main() exits */
    }

    close(a2b[0]);
    close(b2a[1]);
    parent_main(a2b[1], b2a[0], max_size, pid, tsc_hz);

    return 0;
}
