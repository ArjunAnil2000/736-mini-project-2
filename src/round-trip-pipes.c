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
 * Parent and child are pinned to different CPUs via pin_to_cpu() (common.h), called from inside
 * this program since fork() copies affinity - taskset alone can't give them different cores.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
#include "common.h"

#define PARENT_CPU 0
#define CHILD_CPU 1

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
        full_read(a2b_read, buf, sz);
        full_write(b2a_write, buf, sz);
    }

    free(buf);
    close(a2b_read);
    close(b2a_write);
    exit(0);
}

static void parent_main(int a2b_write, int b2a_read, size_t max_size, pid_t child_pid)
{
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
