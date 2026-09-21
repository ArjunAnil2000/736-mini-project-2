#CC = gcc

# DO NOT COMPILE WITH -fomit-frame-pointer
# IT WILL BREAK perf record --call-graph
# YOU MUST COMPILE WITH -g OR STRACE FAILS TO STACK TRACE
CFLAGS = -O2 -Wall -Wextra -std=gnu11 -g
BIN = clockres round-trip-pipes latency throughput

all: $(addprefix out/,$(BIN))

get_perf_list:
	sudo perf list --details > perf_events.txt

out:
	mkdir -p out

out/common.o: src/common.c src/common.h | out
	$(CC) $(CFLAGS) -c -o $@ src/common.c

out/%: src/%.c out/common.o src/common.h | out
	$(CC) $(CFLAGS) -o $@ $< out/common.o

%.run: out/%
	./scripts/$*-runner.sh

clean:
	rm -rf out

.PHONY: all clean
