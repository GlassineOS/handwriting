#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Python binding (python/glassine_handwriting.py) against the stand-in compositor and
the service: a raw stroke, its neat version and candidates arrive as Python calls.

    HW_STANDIN=… GLASSINE_HANDWRITING_LIB=…/libglassine-handwriting.so python3 tests/test_binding.py"""
import ctypes
import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "python"))
import test_pen as TP  # noqa: E402
import glassine_handwriting as G  # noqa: E402


class Wl:
    """a wl_display and one wl_surface, from libwayland-client alone"""

    def __init__(self):
        wl = self.wl = ctypes.CDLL("libwayland-client.so.0")
        wl.wl_display_connect.restype = ctypes.c_void_p
        wl.wl_display_connect.argtypes = [ctypes.c_char_p]
        wl.wl_proxy_marshal_flags.restype = ctypes.c_void_p
        wl.wl_proxy_add_listener.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        for f in ("wl_display_roundtrip", "wl_display_dispatch", "wl_display_flush", "wl_display_disconnect",
                  "wl_display_dispatch_pending"):
            getattr(wl, f).argtypes = [ctypes.c_void_p]
        self.d = wl.wl_display_connect(None)
        assert self.d
        reg = wl.wl_proxy_marshal_flags(ctypes.c_void_p(self.d), 1, ctypes.c_void_p(ctypes.addressof(
            ctypes.c_char.in_dll(wl, "wl_registry_interface"))), 1, 0, None)
        self.names = {}
        G_ = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32)
        R_ = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
        self.cbs = (G_(lambda d, r, n, i, v: self.names.setdefault(i.decode(), n)), R_(lambda d, r, n: None))
        self.lst = (ctypes.c_void_p * 2)(ctypes.cast(self.cbs[0], ctypes.c_void_p), ctypes.cast(self.cbs[1], ctypes.c_void_p))
        wl.wl_proxy_add_listener(reg, self.lst, None)
        wl.wl_display_roundtrip(self.d)
        ci = ctypes.c_void_p(ctypes.addressof(ctypes.c_char.in_dll(wl, "wl_compositor_interface")))
        comp = wl.wl_proxy_marshal_flags(ctypes.c_void_p(reg), 0, ci, 4, 0, ctypes.c_uint32(self.names["wl_compositor"]),
                                         b"wl_compositor", ctypes.c_uint32(4), None)
        si = ctypes.c_void_p(ctypes.addressof(ctypes.c_char.in_dll(wl, "wl_surface_interface")))
        self.s = wl.wl_proxy_marshal_flags(ctypes.c_void_p(comp), 0, si, 4, 0, None)
        wl.wl_display_roundtrip(self.d)

    def pump(self, until, timeout=20):
        end = time.monotonic() + timeout
        while not until():
            self.wl.wl_display_flush(self.d)
            import select
            fd = self.wl.wl_display_get_fd(ctypes.c_void_p(self.d))
            if select.select([fd], [], [], 0.1)[0]:
                self.wl.wl_display_dispatch(self.d)
            if time.monotonic() > end:
                raise AssertionError("timed out")


class Binding(TP.Pen):
    __unittest_skip__ = False             # test_pen's own reason (GHW_PRINT) isn't this one's
    locals().update({n: None for n in dir(TP.Pen) if n.startswith("test_")})     # test_pen's cases

    def test_binding(self):
        if not (TP.STANDIN and os.environ.get("GLASSINE_HANDWRITING_LIB")):
            self.skipTest("needs HW_STANDIN and the library")
        self.start()
        os.environ["XDG_RUNTIME_DIR"] = self.env["XDG_RUNTIME_DIR"]
        os.environ["WAYLAND_DISPLAY"] = self.env["WAYLAND_DISPLAY"]
        w = Wl()

        class L:
            def __init__(s):
                s.ev = []

            def __getattr__(s, name):
                if name in ("stroke", "neat", "reading", "candidate", "candidates_done", "report_done", "capabilities"):
                    return lambda *a: s.ev.append((name, a))
                raise AttributeError(name)
        lst = L()
        pen = G.Pen(w.d, w.s, lst)
        self.assertIsNotNone(pen)
        self.assertEqual(pen.capabilities & {"strokes", "neaten", "text"}, {"strokes", "neaten", "text"})
        self.assertIn("en", pen.languages)
        pen.set_neaten(True, 0.5)
        pen.set_text(True)
        w.wl.wl_display_roundtrip(w.d)
        st = self.ink(2, seed=9)
        for s in st:
            self.pen(s)
        w.pump(lambda: any(e[0] == "candidates_done" for e in lst.ev))
        names = [e[0] for e in lst.ev]
        self.assertEqual(names.count("stroke"), 2)
        self.assertEqual(names.count("neat"), 2)
        k, pts = [e[1] for e in lst.ev if e[0] == "stroke"][0]
        self.assertEqual(k, 0)
        self.assertEqual([p[:3] for p in pts], [(x, y, t) for x, y, t, *_ in st[0]])
        self.assertIn([e[1][1] for e in lst.ev if e[0] == "neat"][0], ("kept", "line", "pulled"))
        c = [e[1] for e in lst.ev if e[0] == "candidate"][0]
        self.assertIn(c[3], ("ink", "beam", "words", "guided"))
        serial = c[0]
        pen.report(serial, G.PICKED, 0, c[2])
        w.pump(lambda: any(e[0] == "report_done" for e in lst.ev))
        self.assertEqual([e[1] for e in lst.ev if e[0] == "report_done"][0], (serial, "not_allowed"))
        pen.close()
        w.wl.wl_display_disconnect(w.d)


    def test_regions(self):
        """a writing region from Python: its strokes, value and words, an edit, a restore"""
        if not (TP.STANDIN and os.environ.get("GLASSINE_HANDWRITING_LIB")):
            self.skipTest("needs HW_STANDIN and the library")
        self.start()
        os.environ["XDG_RUNTIME_DIR"] = self.env["XDG_RUNTIME_DIR"]
        os.environ["WAYLAND_DISPLAY"] = self.env["WAYLAND_DISPLAY"]
        w = Wl()

        class L:
            def __init__(s):
                s.ev = []

            def __getattr__(s, name):
                if name in ("stroke", "neat", "value", "candidate", "candidates_done", "gesture", "report_done"):
                    return lambda *a: s.ev.append((name, a))
                raise AttributeError(name)
        pen = G.Pen(w.d, w.s, None)
        self.assertIn("regions", pen.capabilities)
        lst, lst2 = L(), L()
        r = pen.region("notes", lst)
        self.assertIsNotNone(r)
        r.set_rect(0, 0, 400, 100)
        w.wl.wl_display_roundtrip(w.d)
        for s in self.ink(2, seed=4):
            self.pen(s)
        w.pump(lambda: any(e[0] == "candidates_done" for e in lst.ev))
        strokes = {e[1][0]: e[1][1] for e in lst.ev if e[0] == "stroke"}
        self.assertEqual(sorted(strokes), [0, 1])
        serial, text, words, gone, state = [e[1] for e in lst.ev if e[0] == "value"][-1]
        self.assertTrue(text and words and state)
        self.assertEqual(" ".join(x["text"] for x in words), text)
        # shown set in the person's font: the same words, each with its strokes and its shape
        n = len([e for e in lst.ev if e[0] == "value"])
        r.set_display("set")
        w.pump(lambda: len([e for e in lst.ev if e[0] == "value"]) > n)
        _, t2, w2, g2, s2 = [e[1] for e in lst.ev if e[0] == "value"][-1]
        self.assertEqual((t2, g2, s2), (text, [], state))
        self.assertTrue(all("path" in x and x["strokes"] for x in w2), w2)
        self.assertEqual([x["strokes"] for x in w2], [x["strokes"] for x in words])
        r.set_display("ink")
        w.pump(lambda: len([e for e in lst.ev if e[0] == "value"]) > n + 1)
        self.assertFalse(any("path" in x for x in [e[1] for e in lst.ev if e[0] == "value"][-1][2]))
        r.edit("hello")
        w.pump(lambda: [e for e in lst.ev if e[0] == "value"][-1][1][1] == "hello")
        _, text, words, gone, state = [e[1] for e in lst.ev if e[0] == "value"][-1]
        self.assertEqual(words[0]["text"], "hello")
        self.assertIn("box", words[0])
        self.assertIsInstance(words[0]["path"], list)
        keep = {k: v for k, v in strokes.items() if k not in gone}
        r2 = pen.region("copy", lst2)
        r2.set_rect(0, 200, 400, 100)
        r2.restore(keep, text, state)
        w.pump(lambda: any(e[0] == "value" for e in lst2.ev))
        self.assertEqual([e[1] for e in lst2.ev if e[0] == "value"][-1][1], "hello")
        r.close()
        r2.close()
        pen.close()
        w.wl.wl_display_disconnect(w.d)


if __name__ == "__main__":
    unittest.main()
