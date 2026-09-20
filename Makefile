CC = gcc
CFLAGS = -O2 -Wall -Wextra -std=gnu11
BIN = clockres round-trip-pipes

all: $(addprefix out/,$(BIN))

out:
	mkdir -p out

out/common.o: src/common.c src/common.h | out
	$(CC) $(CFLAGS) -c -o $@ src/common.c

out/%: src/%.c out/common.o src/common.h | out
	$(CC) $(CFLAGS) -o $@ $< out/common.o

clockres.run: out/clockres
	./scripts/clockres-runner.sh

round-trip-pipes.run: out/round-trip-pipes
	./scripts/round-trip-pipes-runner.sh

clean:
	rm -rf out

.PHONY: all clean clockres.run round-trip-pipes.run
