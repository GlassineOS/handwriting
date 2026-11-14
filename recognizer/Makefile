# The handwriting recognizer's engine: features, the model, CTC decoding, samples
# Build: a C11 compiler, pkg-config, json-c (json-c-dev). make check also needs python3
# and numpy (py3-numpy); it reads the trainer in ../train.
OUT ?= .
CC ?= cc
CFLAGS ?= -O2 -g
PREFIX ?= /usr
# -ffp-contract=fast: fused multiply-add in the engine (-std=c11 would turn it off)
XCFLAGS = -std=c11 -ffp-contract=fast -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE $(shell pkg-config --cflags json-c)
LIBS = $(shell pkg-config --libs json-c) -lm
HW = model ctc sample
OBJ = $(OUT)/featurize.o $(HW:%=$(OUT)/%.o)
HDR = $(wildcard src/hw/*.h)
PY ?= python3

all: $(OUT)/test-engine

# the features must round exactly like features.py: no fused multiply-add there
$(OUT)/featurize.o: src/hw/featurize.c $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -ffp-contract=off -c -o $@ $<

$(OUT)/%.o: src/hw/%.c $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -c -o $@ $<

$(OUT)/test-engine: tests/test-engine.c $(OBJ) $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -o $@ tests/test-engine.c $(OBJ) $(LIBS)

# unit tests, the trainer's features and characters as they are now
check: $(OUT)/test-engine
	$(OUT)/test-engine tests/data
	$(PY) tests/test_train_sync.py $(OUT)/test-engine

clean:
	rm -f $(OUT)/test-engine $(OBJ)

.PHONY: all check clean
