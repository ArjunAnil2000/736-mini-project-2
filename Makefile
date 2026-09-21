CC = gcc

# DO NOT COMPILE WITH -fomit-frame-pointer
# IT WILL BREAK perf record --call-graph
# YOU MUST COMPILE WITH -g OR STRACE FAILS TO STACK TRACE
# -fno-omit-frame-pointer: gcc -O2 omits frame pointers by default on x86-64, which leaves perf's
# frame-pointer call graphs (-g / --call-graph fp) without user-space callers above the leaf
CFLAGS = -O2 -Wall -Wextra -std=gnu11 -g -fno-omit-frame-pointer
BIN = clockres round-trip-pipes latency throughput

all: $(addprefix out/,$(BIN))

get_perf_list:
	sudo perf list --details > perf_events.txt

out:
	mkdir -p out

out/common.o: src/common.c src/common.h | out
	$(CC) $(CFLAGS) -c -o $@ src/common.c

# setcap lets the binary use SCHED_FIFO without root (see pin_to_cpu in src/common.c).
# File capabilities are wiped by every relink, hence setting them right after linking.
out/%: src/%.c out/common.o src/common.h | out
	$(CC) $(CFLAGS) -o $@ $< out/common.o
	sudo setcap cap_sys_nice+ep $@

# The leading @ matters: without it make echoes the command to STDOUT, which the runners use for the
# benchmark's CSV (`sudo make latency.run > results/latency_run1.csv`), so the CSV got a junk first line.
%.run: out/%
	@./scripts/$*-runner.sh

clean:
	rm -rf out

.PHONY: all clean
