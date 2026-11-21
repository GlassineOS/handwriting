# glassine-handwriting: the handwriting recognizer (docs/recognizer.md)
# Build: a C11 compiler, pkg-config, json-c (json-c-dev). make check also needs python3
# and numpy (py3-numpy), and for the font fontTools (py3-fonttools), hb-shape
# (harfbuzz-utils) and fontconfig; it reads the trainer in ../train.
OUT ?= .
CC ?= cc
CFLAGS ?= -O2 -g
PREFIX ?= /usr
# -ffp-contract=fast: fused multiply-add in the engine (-std=c11 would turn it off)
XCFLAGS = -std=c11 -ffp-contract=fast -Wall -Wextra -Wno-unused-parameter -D_GNU_SOURCE $(shell pkg-config --cflags json-c)
LIBS = $(shell pkg-config --libs json-c) -lm
HW = model ctc sample recog store adapt gesture words chars letters spacing ttf base size neaten lookalike calib cut
OBJ = $(OUT)/featurize.o $(HW:%=$(OUT)/%.o)
HDR = $(wildcard src/hw/*.h)
PY ?= python3

all: $(OUT)/glassine-handwriting

# the features must round exactly like features.py: no fused multiply-add there
$(OUT)/featurize.o: src/hw/featurize.c $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -ffp-contract=off -c -o $@ $<

$(OUT)/%.o: src/hw/%.c $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -c -o $@ $<

$(OUT)/glassine-handwriting: src/glassine-handwriting.c $(OBJ) $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -o $@ src/glassine-handwriting.c $(OBJ) $(LIBS)

$(OUT)/test-engine: tests/test-engine.c $(OBJ) $(HDR)
	$(CC) $(CFLAGS) $(XCFLAGS) -o $@ tests/test-engine.c $(OBJ) $(LIBS)

# unit tests, the trainer's features and characters as they are now, the contract
check: $(OUT)/test-engine $(OUT)/glassine-handwriting
	$(OUT)/test-engine tests/data
	$(PY) tests/test_train_sync.py $(OUT)/test-engine
	HW_BIN=$(OUT)/glassine-handwriting $(PY) tests/test_recognizer.py
	HW_BIN=$(OUT)/glassine-handwriting $(PY) tests/test_letters.py
	HW_BIN=$(OUT)/glassine-handwriting $(PY) tests/test_neaten.py
	HW_BIN=$(OUT)/glassine-handwriting $(PY) tests/test_relay.py

# with a trained model: MODEL_DIR holds en/recognizer.ghm (tools/onnx2ghm.py)
check-model: $(OUT)/glassine-handwriting
	@test -n "$(MODEL_DIR)" || { echo "make check-model MODEL_DIR=dir (with en/recognizer.ghm)"; exit 2; }
	HW_BIN=$(OUT)/glassine-handwriting $(PY) tests/check_model.py $(MODEL_DIR)
	HW_BIN=$(OUT)/glassine-handwriting HW_MODEL_DIR=$(MODEL_DIR) $(PY) tests/test_recognizer.py Contract

install: $(OUT)/glassine-handwriting
	install -Dm755 $(OUT)/glassine-handwriting $(DESTDIR)$(PREFIX)/bin/glassine-handwriting

# Autograph's base font (docs/recognizer.md, The base font): the centre lines of an Edu
# Beginner font (BASE_TTF, its licence BASE_OFL), made and checked by tools/base-font.py
# (fontTools, numpy); then installed with its licence and a note of where it comes from
BASE_DIR = $(PREFIX)/share/glassine/handwriting/autograph
$(OUT)/base.jsonl: tools/base-font.py
	@test -n "$(BASE_TTF)" || { echo "make base BASE_TTF=EduSABeginner.ttf"; exit 2; }
	$(PY) tools/base-font.py $(BASE_TTF) $@

base: $(OUT)/base.jsonl

install-base: $(OUT)/base.jsonl
	@test -n "$(BASE_OFL)" || { echo "make install-base BASE_OFL=OFL.txt"; exit 2; }
	install -Dm644 $(OUT)/base.jsonl $(DESTDIR)$(BASE_DIR)/base.jsonl
	install -Dm644 $(BASE_OFL) $(DESTDIR)$(BASE_DIR)/OFL.txt
	$(PY) -c 'import json, sys; h = json.loads(open(sys.argv[1]).readline()); print("Autograph draws the characters a person has not written from the centre lines of %s (%s, %s; sha256 %s), a Modified Version under the SIL Open Font License 1.1 (OFL.txt). %s" % (h["family"], h["source"], h["licence"], h["sha256"], h["copyright"]))' $(OUT)/base.jsonl > $(DESTDIR)$(BASE_DIR)/README

clean:
	rm -f $(OUT)/glassine-handwriting $(OUT)/test-engine $(OBJ)

.PHONY: all check check-model install base install-base clean
