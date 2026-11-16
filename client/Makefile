# libglassine-handwriting: the pen interface for apps (glassine-handwriting.h,
# docs/pen-interface.md). glassine-handwriting-v1.xml is GlassineOS/protocols' (copied).
PREFIX ?= /usr
LIBDIR ?= $(PREFIX)/lib
CC ?= cc
CFLAGS ?= -O2
CFLAGS += -std=c11 -D_GNU_SOURCE -Wall -Wextra -Wno-unused-parameter -fPIC
O ?= out
SOVER = 1
PY ?= python3
WL_CFLAGS = $(shell pkg-config --cflags wayland-client)
WL_LIBS = $(shell pkg-config --libs wayland-client)

all: $(O)/libglassine-handwriting.so $(O)/ghw-pen-print

$(O)/glassine-handwriting-v1-client-protocol.h: glassine-handwriting-v1.xml
	mkdir -p $(O)
	wayland-scanner client-header $< $@

$(O)/glassine-handwriting-v1-protocol.c: glassine-handwriting-v1.xml
	mkdir -p $(O)
	wayland-scanner private-code $< $@

$(O)/libglassine-handwriting.so: glassine-handwriting.c glassine-handwriting.h $(O)/glassine-handwriting-v1-client-protocol.h $(O)/glassine-handwriting-v1-protocol.c
	$(CC) $(CFLAGS) $(WL_CFLAGS) -I$(O) -I. -shared -Wl,-soname,libglassine-handwriting.so.$(SOVER) -o $@ \
		glassine-handwriting.c $(O)/glassine-handwriting-v1-protocol.c $(WL_LIBS)
	ln -sf libglassine-handwriting.so $(O)/libglassine-handwriting.so.$(SOVER)

$(O)/ghw-pen-print: examples/ghw-pen-print.c $(O)/libglassine-handwriting.so
	$(CC) $(CFLAGS) $(WL_CFLAGS) -I. -o $@ examples/ghw-pen-print.c -L$(O) -lglassine-handwriting -Wl,-rpath,'$$ORIGIN' $(WL_LIBS)

install: all
	install -Dm755 $(O)/libglassine-handwriting.so $(DESTDIR)$(LIBDIR)/libglassine-handwriting.so.$(SOVER)
	ln -sf libglassine-handwriting.so.$(SOVER) $(DESTDIR)$(LIBDIR)/libglassine-handwriting.so
	install -Dm644 glassine-handwriting.h $(DESTDIR)$(PREFIX)/include/glassine-handwriting.h
	install -Dm644 examples/ghw-pen-print.c $(DESTDIR)$(PREFIX)/share/doc/glassine-handwriting/examples/ghw-pen-print.c
	install -Dm644 glassine-handwriting-v1.xml $(DESTDIR)$(PREFIX)/share/glassine/protocols/glassine-handwriting-v1.xml
	install -Dm644 python/glassine_handwriting.py $(DESTDIR)$(PREFIX)/lib/glassine/python/glassine_handwriting.py

# the pen interface end to end needs sway's stand-in compositor (GlassineOS/sway
# test/handwriting-standin.sh): HW_STANDIN=path; without it the tests skip
HW_BIN ?= ../recognizer/glassine-handwriting
check: all
	HW_BIN=$(HW_BIN) GHW_PRINT=$(O)/ghw-pen-print $(PY) tests/test_pen.py
	HW_BIN=$(HW_BIN) GLASSINE_HANDWRITING_LIB=$(abspath $(O))/libglassine-handwriting.so $(PY) tests/test_binding.py

clean:
	rm -rf $(O)

.PHONY: all check install clean
