/* common.h: shared CPU pinning + TSC timing helpers. See common.c for implementations. */
#ifndef COMMON_H
#define COMMON_H

#include <stddef.h>
#include <stdint.h>
#include <time.h>

#define NUM_SIZES 10
extern const size_t SIZES[NUM_SIZES];

void pin_to_cpu(int cpu);
uint64_t timespec_to_ns(struct timespec *ts);
double calibrate_tsc_hz(void);
void full_write(int fd, const char *buf, size_t n);
void full_read(int fd, char *buf, size_t n);

#endif
