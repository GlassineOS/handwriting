# glassine-handwriting-service: handwriting for apps (spec 004; docs/service.md). Python 3
# alone; it runs the recognizer (GLASSINE_HANDWRITING_BIN, default glassine-handwriting on PATH).
# make check runs its tests against the recognizer built in ../recognizer (HW_BIN).
PREFIX ?= /usr
LIB = $(PREFIX)/lib/glassine/handwriting-service
PY ?= python3
HW_BIN ?= ../recognizer/glassine-handwriting

all:

check:
	$(PY) -c 'import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' glassine_hwservice/*.py glassine-handwriting-service examples/*.py tests/*.py
	HW_BIN=$(HW_BIN) $(PY) tests/test_service.py
	$(PY) tests/test_regions.py

# a trained model through the pen interface against the keyboard's path (tests/check_sizes.py)
check-model:
	@test -n "$(MODEL_DIR)" || { echo "make check-model MODEL_DIR=dir (with en/recognizer.ghm)"; exit 2; }
	HW_BIN=$(HW_BIN) $(PY) tests/check_sizes.py $(MODEL_DIR)

install:
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(LIB)/glassine_hwservice
	sed 's|/usr/lib/glassine/handwriting-service|$(LIB)|' glassine-handwriting-service > $(DESTDIR)$(PREFIX)/bin/glassine-handwriting-service
	chmod 755 $(DESTDIR)$(PREFIX)/bin/glassine-handwriting-service
	install -m644 glassine_hwservice/*.py $(DESTDIR)$(LIB)/glassine_hwservice/
	install -Dm755 examples/hw-service-example.py $(DESTDIR)$(PREFIX)/share/doc/glassine-handwriting/examples/hw-service-example.py

.PHONY: all check check-model install
