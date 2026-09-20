CC = gcc
CFLAGS = -O2 -Wall -Wextra -std=gnu11
BIN = clockres round-trip-pipes

all: $(addprefix out/,$(BIN))

out:
	mkdir -p out

out/%: src/%.c | out
	$(CC) $(CFLAGS) -o $@ $<

clockres.run: out/clockres
	./scripts/clockres-runner.sh

round-trip-pipes.run: out/round-trip-pipes
	./scripts/round-trip-pipes-runner.sh

clean:
	rm -rf out

.PHONY: all clean clockres.run round-trip-pipes.run
