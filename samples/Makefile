# glassine-handwriting-samples: the handwriting samples app (spec 002)
# Runs on python3, PyGObject with GTK 3, pycairo and glassine_ui (py3-glassine-ui).
# make check runs the toolkit-free part against the recognizer (HW_BIN, default the one
# built in ../recognizer) with python3 alone.
PREFIX ?= /usr
LIB = $(PREFIX)/lib/glassine/handwriting-samples
MODELS = $(PREFIX)/share/glassine/handwriting
PY ?= python3
HW_BIN ?= ../recognizer/glassine-handwriting
LANGS = $(notdir $(wildcard prompts/*))

all:

check:
	$(PY) -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' glassine_hwsamples/*.py glassine-handwriting-samples tests/*.py
	HW_BIN=$(HW_BIN) $(PY) tests/test_samples.py

install:
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(LIB)/glassine_hwsamples
	sed 's|/usr/lib/glassine/handwriting-samples|$(LIB)|' glassine-handwriting-samples > $(DESTDIR)$(PREFIX)/bin/glassine-handwriting-samples
	chmod 755 $(DESTDIR)$(PREFIX)/bin/glassine-handwriting-samples
	install -m644 glassine_hwsamples/*.py $(DESTDIR)$(LIB)/glassine_hwsamples/
	for l in $(LANGS); do \
		install -d $(DESTDIR)$(MODELS)/$$l/prompts; \
		install -m644 prompts/$$l/* $(DESTDIR)$(MODELS)/$$l/prompts/; \
	done
	install -Dm644 org.glassine.HandwritingSamples.desktop $(DESTDIR)$(PREFIX)/share/applications/org.glassine.HandwritingSamples.desktop

.PHONY: all check install
