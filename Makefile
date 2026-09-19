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

clean:
	rm -rf out

.PHONY: all clean clockres.run
