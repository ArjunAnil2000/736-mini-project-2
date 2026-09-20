/**
 * round-trip-pipes: fork + two pipes, round-trip a buffer of each of the assignment's message
 * sizes between parent and child, and verify the bytes come back unchanged.
 *
 * Two unidirectional pipes are used (a2b: parent -> child, b2a: child -> parent), since pipe()
 * itself only supports one direction. The parent writes a buffer, the child reads the exact
 * same number of bytes back and echoes it back unchanged; the parent reads the echo and checks
 * it matches what it sent.
 *
 * Remember pipes are a classic example of the producer-consumer model. This means that we cannot
 * expect a single write()/read() to fully write/read a pipe. Therefore, wrap them in loops --
 * full_write(), full_read().
 * Also remember that the pipe kernel buffer size is 65536 bytes in this lap (see lap-specs.md).
 *
 * The parent and the child processes are pinned to different CPUs using pin_to_cpu(). It's called
 * from inside this program rather than using taskset since the child is spawned only here.
 * Additionally, the child will copy the parent's affinity, so there is no other choice but to set
 * it from inside this program itself. The parent pins itself immediately after starting while the
 * child pins itself immediately after a successful fork.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sched.h>
#include <sys/wait.h>

static const size_t SIZES[] = {4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 524288};
#define NUM_SIZES (sizeof(SIZES) / sizeof(SIZES[0]))

#define PARENT_CPU 0
#define CHILD_CPU 1

static void pin_to_cpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        perror("sched_setaffinity");
        exit(1);
    }
}

static void full_write(int fd, const char *buf, size_t n) {
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

static void full_read(int fd, char *buf, size_t n) {
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

static void child_main(int a2b_read, int b2a_write, size_t max_size) {
    pin_to_cpu(CHILD_CPU);

    char *buf = malloc(max_size);
    if (!buf) {
        perror("malloc");
        exit(1);
    }

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];
        full_read(a2b_read, buf, sz);
        full_write(b2a_write, buf, sz);
    }

    free(buf);
    close(a2b_read);
    close(b2a_write);
    exit(0);
}

static void parent_main(int a2b_write, int b2a_read, size_t max_size, pid_t child_pid) {
    char *send_buf = malloc(max_size);
    char *recv_buf = malloc(max_size);
    if (!send_buf || !recv_buf) {
        perror("malloc");
        exit(1);
    }
    memset(send_buf, 0xAB, max_size);

    for (size_t i = 0; i < NUM_SIZES; i++) {
        size_t sz = SIZES[i];

        full_write(a2b_write, send_buf, sz);
        full_read(b2a_read, recv_buf, sz);

        if (memcmp(send_buf, recv_buf, sz) != 0) {
            fprintf(stderr, "MISMATCH at size %zu bytes\n", sz);
            exit(1);
        }
        printf("size %7zu bytes: round trip OK\n", sz);
    }

    close(a2b_write);
    close(b2a_read);
    free(send_buf);
    free(recv_buf);

    waitpid(child_pid, NULL, 0);
}

int main(void) {
    pin_to_cpu(PARENT_CPU);

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
        /* child: reads from a2b, writes to b2a */
        close(a2b[1]);
        close(b2a[0]);
        child_main(a2b[0], b2a[1], max_size);
        /* never reached, child_main() exits */
    }

    /* parent: writes to a2b, reads from b2a */
    close(a2b[0]);
    close(b2a[1]);
    parent_main(a2b[1], b2a[0], max_size, pid);

    return 0;
}
