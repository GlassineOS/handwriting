# SPDX-License-Identifier: GPL-2.0-or-later
"""glassine_handwriting: the pen interface for Python apps, on libglassine-handwriting
(glassine-handwriting.h, docs/pen-interface.md).

    pen = glassine_handwriting.Pen.for_gdk_window(gdk_window, listener)   # GTK 3 on Wayland
    pen = glassine_handwriting.Pen(wl_display_ptr, wl_surface_ptr, listener)

Pen() returns None (for_gdk_window too) when there is no pen interface: not Wayland, an
older compositor, or the library isn't installed; the app then draws from its own tablet
events. The listener is any object with some of these methods (missing ones are skipped):

    stroke(k, points)              points: [(x, y, time, pressure, tilt_x, tilt_y)]
    neat(k, how, points)           how: "kept", "line" or "pulled"
    reading(text, strokes)
    candidate(serial, rank, text, origin, fit, chars)   origin: "ink", "beam", "words", "guided"
    candidates_done(serial, count)
    report_done(serial, result)    "stored", "not_kept", "not_allowed", "stale", "unavailable"
    capabilities(caps)             a set of "strokes", "neaten", "text", "letters", "regions"

Writing regions (spec 005): pen.region(name, listener) gives a Region (None without them),
whose listener has some of

    stroke(k, points), neat(k, how, points)       region coordinates
    value(serial, text, words, gone, state)       words: dicts in the text's order, an ink word
                                                  {"text", "strokes", "dx", "dy"}, a set word
                                                  {"text", "box": (x, y, w, h), "path": [op, …]}
                                                  with op ("M", x, y), ("L", x, y), ("Q", …),
                                                  ("C", …), ("Z",); state: bytes to keep
    candidate(serial, rank, text, origin, fit), candidates_done(serial, count)
    gesture(serial, kind, strokes)                kind: "strike", "scratch", "caret"
    report_done(serial, result)

Events arrive while the app's toolkit dispatches Wayland events, on its thread."""

import ctypes
import ctypes.util
import os

__all__ = ["Pen", "Region", "PICKED", "ACCEPTED", "CORRECTED", "DISCARDED", "PURPOSE"]

PICKED, ACCEPTED, CORRECTED, DISCARDED = range(4)
HOW = ("kept", "line", "pulled")
ORIGIN = ("ink", "beam", "words", "guided")
RESULT = ("stored", "not_kept", "not_allowed", "stale", "unavailable")
CAPS = ((1, "strokes"), (2, "neaten"), (4, "text"), (8, "letters"), (16, "regions"))
GESTURE = ("strike", "scratch", "caret")
PATH = (("M", 2), ("L", 2), ("Q", 4), ("C", 6), ("Z", 0))
MODE = {"ink": 0, "text": 1}
PURPOSE = {"normal": 0, "alpha": 1, "digits": 2, "number": 3, "phone": 4, "url": 5, "email": 6, "name": 7,
           "password": 8, "pin": 9, "date": 10, "time": 11, "datetime": 12, "terminal": 13}


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("time", ctypes.c_uint32),
                ("pressure", ctypes.c_double), ("tilt_x", ctypes.c_double), ("tilt_y", ctypes.c_double)]


_P = ctypes.POINTER(_Point)
_STROKE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, _P, ctypes.c_size_t)
_NEAT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, _P, ctypes.c_size_t)
_READING = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t)
_CAND = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_int, ctypes.c_char_p)
_DONE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32)
_REPORT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int)
_CAPS = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)


class _Listener(ctypes.Structure):
    _fields_ = [("stroke", _STROKE), ("neat", _NEAT), ("reading", _READING), ("candidate", _CAND),
                ("candidates_done", _DONE), ("report_done", _REPORT), ("capabilities", _CAPS)]


class _Word(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint32), ("length", ctypes.c_uint32), ("set", ctypes.c_int),
                ("strokes", ctypes.POINTER(ctypes.c_uint32)), ("nstrokes", ctypes.c_size_t),
                ("dx", ctypes.c_double), ("dy", ctypes.c_double), ("x", ctypes.c_double), ("y", ctypes.c_double),
                ("width", ctypes.c_double), ("height", ctypes.c_double),
                ("path", ctypes.POINTER(ctypes.c_double)), ("npath", ctypes.c_size_t)]


class _Stored(ctypes.Structure):
    _fields_ = [("stroke", ctypes.c_uint32), ("points", _P), ("n", ctypes.c_size_t)]


_U32P = ctypes.POINTER(ctypes.c_uint32)
_vp, _u32, _sz = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t
_R_STROKE = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, _P, _sz)
_R_NEAT = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, ctypes.c_int, _P, _sz)
_R_VALUE = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, ctypes.c_char_p, ctypes.POINTER(_Word), _sz, _U32P, _sz, _vp, _sz)
_R_CAND = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, _u32, ctypes.c_char_p, ctypes.c_int, ctypes.c_int)
_R_DONE = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, _u32)
_R_GESTURE = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, ctypes.c_int, _U32P, _sz)
_R_REPORT = ctypes.CFUNCTYPE(None, _vp, _vp, _u32, ctypes.c_int)


class _RListener(ctypes.Structure):
    _fields_ = [("stroke", _R_STROKE), ("neat", _R_NEAT), ("value", _R_VALUE), ("candidate", _R_CAND),
                ("candidates_done", _R_DONE), ("gesture", _R_GESTURE), ("report_done", _R_REPORT)]


_lib = None


def _load():
    global _lib
    if _lib is None:
        name = os.environ.get("GLASSINE_HANDWRITING_LIB") or ctypes.util.find_library("glassine-handwriting") \
            or "libglassine-handwriting.so.1"
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            _lib = False
            return None
        vp, u32, cp, i = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int
        for f, res, args in (
                ("ghw_pen_new", vp, [vp, vp, ctypes.POINTER(_Listener), vp]),
                ("ghw_pen_destroy", None, [vp]),
                ("ghw_pen_capabilities", u32, [vp]),
                ("ghw_pen_languages", ctypes.c_size_t, [vp]),
                ("ghw_pen_language", cp, [vp, ctypes.c_size_t]),
                ("ghw_pen_set_area", None, [vp, i, i, i, i]),
                ("ghw_pen_set_guides", None, [vp, ctypes.c_double, ctypes.c_double]),
                ("ghw_pen_set_neaten", None, [vp, i, ctypes.c_double]),
                ("ghw_pen_set_text", None, [vp, i]),
                ("ghw_pen_set_context", None, [vp, u32, u32, cp, cp, cp]),
                ("ghw_pen_clear", None, [vp]),
                ("ghw_pen_report", None, [vp, u32, i, u32, cp]),
                ("ghw_pen_flush", i, [vp]),
                ("ghw_pen_set_mode", None, [vp, i]),
                ("ghw_region_new", vp, [vp, cp, ctypes.POINTER(_RListener), vp]),
                ("ghw_region_destroy", None, [vp]),
                ("ghw_region_set_rect", None, [vp] + [ctypes.c_double] * 4),
                ("ghw_region_set_clip", None, [vp, i, i, i, i]),
                ("ghw_region_set_guides", None, [vp, ctypes.c_double, ctypes.c_double]),
                ("ghw_region_set_context", None, [vp, u32, u32, cp]),
                ("ghw_region_set_reading", None, [vp, i]),
                ("ghw_region_set_neaten", None, [vp, i, ctypes.c_double]),
                ("ghw_region_set_layer", None, [vp, i]),
                ("ghw_region_set_mode", None, [vp, i]),
                ("ghw_region_edit", None, [vp, cp]),
                ("ghw_region_report", None, [vp, u32, i, u32, cp]),
                ("ghw_region_restore", None, [vp, ctypes.POINTER(_Stored), ctypes.c_size_t, cp, vp, ctypes.c_size_t])):
            fn = getattr(lib, f)
            fn.restype, fn.argtypes = res, args
        _lib = lib
    return _lib or None


def _pts(p, n):
    return [(p[i].x, p[i].y, p[i].time, p[i].pressure, p[i].tilt_x, p[i].tilt_y) for i in range(n)]


def _caps(c):
    return {name for bit, name in CAPS if c & bit}


def _s(b):
    return b.decode("utf-8", "replace") if b else ""


def _gpointer(obj):
    """the C pointer of a PyGObject object"""
    get = ctypes.pythonapi.PyCapsule_GetPointer
    get.restype, get.argtypes = ctypes.c_void_p, [ctypes.py_object, ctypes.c_char_p]
    return get(obj.__gpointer__, None)


class Pen:
    def __new__(cls, display, surface, listener=None):
        lib = _load()
        if lib is None or not display or not surface:
            return None
        self = super().__new__(cls)
        self.listener = listener
        self._lib = lib

        def call(name, *a):
            fn = getattr(self.listener, name, None)
            if fn:
                fn(*a)
        # kept on the object: C holds these pointers as long as the pen lives
        self._cbs = _Listener(
            _STROKE(lambda d, k, p, n: call("stroke", k, _pts(p, n))),
            _NEAT(lambda d, k, h, p, n: call("neat", k, HOW[h] if 0 <= h < 3 else "kept", _pts(p, n))),
            _READING(lambda d, t, s, n: call("reading", _s(t), [s[i] for i in range(n)])),
            _CAND(lambda d, serial, rank, t, o, fit, ch: call("candidate", serial, rank, _s(t),
                                                               ORIGIN[o] if 0 <= o < 4 else "ink", bool(fit), _s(ch))),
            _DONE(lambda d, serial, n: call("candidates_done", serial, n)),
            _REPORT(lambda d, serial, r: call("report_done", serial, RESULT[r] if 0 <= r < 5 else "unavailable")),
            _CAPS(lambda d, c: call("capabilities", _caps(c))))
        self._p = lib.ghw_pen_new(display, surface, ctypes.byref(self._cbs), None)
        if not self._p:
            return None
        return self

    @classmethod
    def for_gdk_window(cls, window, listener=None):
        """the pen interface for a GTK 3 window's Wayland surface (its toplevel's), or None"""
        try:
            gdk = ctypes.CDLL("libgdk-3.so.0")
            gdk.gdk_wayland_window_get_wl_surface.restype = ctypes.c_void_p
            gdk.gdk_wayland_window_get_wl_surface.argtypes = [ctypes.c_void_p]
            gdk.gdk_wayland_display_get_wl_display.restype = ctypes.c_void_p
            gdk.gdk_wayland_display_get_wl_display.argtypes = [ctypes.c_void_p]
            top = window.get_effective_toplevel()
            disp = top.get_display()
            if disp.__gtype__.name != "GdkWaylandDisplay":
                return None
            d = gdk.gdk_wayland_display_get_wl_display(_gpointer(disp))
            s = gdk.gdk_wayland_window_get_wl_surface(_gpointer(top))
        except (OSError, AttributeError, TypeError, ValueError):
            return None
        return cls(d, s, listener)

    def close(self):
        if self._p:
            self._lib.ghw_pen_destroy(self._p)
            self._p = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def capabilities(self):
        return _caps(self._lib.ghw_pen_capabilities(self._p))

    @property
    def languages(self):
        return [_s(self._lib.ghw_pen_language(self._p, i)) for i in range(self._lib.ghw_pen_languages(self._p))]

    def set_area(self, x=0, y=0, w=0, h=0):
        self._lib.ghw_pen_set_area(self._p, int(x), int(y), int(w), int(h))
        self.flush()

    def set_guides(self, spacing=0.0, first=0.0):
        """the area's ruled lines (spacing 0: none); neatening goes line by line"""
        self._lib.ghw_pen_set_guides(self._p, float(spacing), float(first))
        self.flush()

    def set_neaten(self, on, strength=0.5):
        self._lib.ghw_pen_set_neaten(self._p, int(bool(on)), float(strength))
        self.flush()

    def set_text(self, on):
        self._lib.ghw_pen_set_text(self._p, int(bool(on)))
        self.flush()

    def set_context(self, purpose="normal", hints=0, before="", after="", languages=""):
        p = PURPOSE.get(purpose, purpose) if isinstance(purpose, str) else purpose
        self._lib.ghw_pen_set_context(self._p, int(p), int(hints), before.encode(), after.encode(), languages.encode())
        self.flush()

    def clear(self):
        self._lib.ghw_pen_clear(self._p)
        self.flush()

    def report(self, serial, action, rank, text):
        self._lib.ghw_pen_report(self._p, int(serial), int(action), int(rank), text.encode())
        self.flush()

    def flush(self):
        return self._lib.ghw_pen_flush(self._p)

    def set_mode(self, mode):
        """every region into "ink" or "text" mode, and those made later"""
        self._lib.ghw_pen_set_mode(self._p, MODE.get(mode, mode) if isinstance(mode, str) else int(mode))
        self.flush()

    def region(self, name, listener=None):
        """a new writing region on this surface, or None without regions"""
        return Region(self, name, listener)


def _path(p, n):
    out, i = [], 0
    while i < n:
        op = int(p[i])
        if not 0 <= op < len(PATH):
            break
        name, k = PATH[op]
        out.append((name, *[p[i + 1 + j] for j in range(k)]))
        i += 1 + k
    return out


def _words(text, w, n):
    b = text.encode()
    out = []
    for i in range(n):
        d = {"text": b[w[i].start:w[i].start + w[i].length].decode("utf-8", "replace"), "start": w[i].start,
             "length": w[i].length}
        if w[i].set:
            d.update(box=(w[i].x, w[i].y, w[i].width, w[i].height), path=_path(w[i].path, w[i].npath))
        else:
            d.update(strokes=[w[i].strokes[j] for j in range(w[i].nstrokes)], dx=w[i].dx, dy=w[i].dy)
        out.append(d)
    return out


class Region:
    """a writing region (spec 005) of a Pen; Pen.region() makes one"""

    def __new__(cls, pen, name, listener=None):
        self = super().__new__(cls)
        self.pen, self.listener, self._lib = pen, listener, pen._lib

        def call(fn, *a):
            f = getattr(self.listener, fn, None)
            if f:
                f(*a)

        def value(d, r, serial, t, w, nw, g, ng, st, nst):
            text = _s(t)
            call("value", serial, text, _words(text, w, nw), [g[i] for i in range(ng)],
                 ctypes.string_at(st, nst) if st and nst else b"")
        self._cbs = _RListener(
            _R_STROKE(lambda d, r, k, p, n: call("stroke", k, _pts(p, n))),
            _R_NEAT(lambda d, r, k, h, p, n: call("neat", k, HOW[h] if 0 <= h < 3 else "kept", _pts(p, n))),
            _R_VALUE(value),
            _R_CAND(lambda d, r, serial, rank, t, o, fit: call("candidate", serial, rank, _s(t),
                                                                 ORIGIN[o] if 0 <= o < 4 else "ink", bool(fit))),
            _R_DONE(lambda d, r, serial, n: call("candidates_done", serial, n)),
            _R_GESTURE(lambda d, r, serial, k, s, n: call("gesture", serial, GESTURE[k] if 0 <= k < 3 else "strike",
                                                          [s[i] for i in range(n)])),
            _R_REPORT(lambda d, r, serial, res: call("report_done", serial,
                                                     RESULT[res] if 0 <= res < 5 else "unavailable")))
        self._r = self._lib.ghw_region_new(pen._p, name.encode(), ctypes.byref(self._cbs), None)
        if not self._r:
            return None
        return self

    def close(self):
        if self._r:
            self._lib.ghw_region_destroy(self._r)
            self._r = None
            self.pen.flush()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _do(self, fn, *a):
        getattr(self._lib, fn)(self._r, *a)
        self.pen.flush()

    def set_rect(self, x, y, w, h):
        self._do("ghw_region_set_rect", float(x), float(y), float(w), float(h))

    def set_clip(self, x=0, y=0, w=0, h=0):
        self._do("ghw_region_set_clip", int(x), int(y), int(w), int(h))

    def set_guides(self, spacing=0.0, first=0.0):
        self._do("ghw_region_set_guides", float(spacing), float(first))

    def set_context(self, purpose="normal", hints=0, languages=""):
        p = PURPOSE.get(purpose, purpose) if isinstance(purpose, str) else purpose
        self._do("ghw_region_set_context", int(p), int(hints), languages.encode())

    def set_reading(self, on):
        self._do("ghw_region_set_reading", int(bool(on)))

    def set_neaten(self, on, strength=0.5):
        self._do("ghw_region_set_neaten", int(bool(on)), float(strength))

    def set_layer(self, layer):
        self._do("ghw_region_set_layer", int(layer))

    def set_mode(self, mode):
        self._do("ghw_region_set_mode", MODE.get(mode, mode) if isinstance(mode, str) else int(mode))

    def edit(self, text):
        self._do("ghw_region_edit", text.encode())

    def report(self, serial, action, rank=0, text=""):
        self._do("ghw_region_report", int(serial), int(action), int(rank), text.encode())

    def restore(self, strokes, value=None, state=None):
        """stored ink handed back: strokes {k: points} (points as stroke() gave them), the
        value and saved state kept with them, or neither"""
        keep = []
        arr = (_Stored * max(1, len(strokes)))()
        for i, (k, pts) in enumerate(sorted(strokes.items())):
            p = (_Point * max(1, len(pts)))(*[_Point(*pt) for pt in pts])
            keep.append(p)
            arr[i] = _Stored(int(k), ctypes.cast(p, _P), len(pts))
        st = ctypes.create_string_buffer(state, len(state)) if state else None
        self._do("ghw_region_restore", arr, len(strokes), value.encode() if value is not None else None,
                 ctypes.cast(st, ctypes.c_void_p) if st else None, len(state) if state else 0)
