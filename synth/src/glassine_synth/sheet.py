"""SVG review sheets: glyph strokes per font, and generated lines coloured by time."""

import numpy as np


def _colour(f: float) -> str:
    # early = blue, late = red
    r, b = int(40 + 200 * f), int(220 - 180 * f)
    return f"rgb({r},60,{b})"


def _path(pts, sx, sy, ox, oy):
    return " ".join(f"{'M' if i == 0 else 'L'}{ox + x * sx:.1f},{oy + y * sy:.1f}"
                    for i, (x, y) in enumerate(pts[:, :2]))


def glyph_sheet(fonts, chars: str, scale: float = 60) -> str:
    """fonts: list of (font id, glyph table). One row per font, stroke order numbered."""
    cell = scale * 1.4
    w = int(160 + cell * len(chars))
    h = int(cell * len(fonts) + 20)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'font-family="sans-serif" font-size="10"><rect width="100%" height="100%" fill="#fff"/>']
    for r, (fid, table) in enumerate(fonts):
        oy = 10 + r * cell + scale * 1.05
        out.append(f'<text x="4" y="{oy:.0f}">{fid}</text>')
        for c, ch in enumerate(chars):
            g = table.get(ch)
            if not g:
                continue
            ox = 160 + c * cell
            n = len(g.strokes)
            for k, s in enumerate(g.strokes):
                col = _colour(k / max(1, n - 1))
                dash = ' stroke-dasharray="3 2"' if g.delayable[k] else ""
                out.append(f'<path d="{_path(s, scale, scale, ox, oy)}" fill="none" '
                           f'stroke="{col}" stroke-width="1.6"{dash}/>')
                x, y = s[0]
                out.append(f'<circle cx="{ox + x * scale:.1f}" cy="{oy + y * scale:.1f}" r="2" fill="{col}"/>')
                out.append(f'<text x="{ox + x * scale + 2:.1f}" y="{oy + y * scale - 2:.1f}" '
                           f'fill="{col}" font-size="8">{k + 1}</text>')
    out.append("</svg>")
    return "\n".join(out)


def line_sheet(samples, scale: float = 40) -> str:
    """Each sample on its own row; colour runs from the first to the last pen sample."""
    rows = []
    width = 0
    for smp in samples:
        allp = np.concatenate(smp["strokes"]) if smp["strokes"] else np.zeros((1, 3))
        width = max(width, float(allp[:, 0].max()) * scale)
        rows.append(allp)
    h = int(len(samples) * scale * 2.2 + 20)
    w = int(width + 60)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'font-family="sans-serif" font-size="11"><rect width="100%" height="100%" fill="#fff"/>']
    for r, smp in enumerate(samples):
        oy = 20 + r * scale * 2.2 + scale * 1.3
        tmax = max(float(s[-1, 2]) for s in smp["strokes"]) if smp["strokes"] else 1
        out.append(f'<text x="20" y="{oy + scale * 0.75:.0f}" fill="#888">{smp["text"]} '
                   f'({tmax:.1f} s, {len(smp["strokes"])} strokes)</text>')
        for s in smp["strokes"]:
            col = _colour(float(s[0, 2]) / tmax)
            out.append(f'<path d="{_path(s, scale, scale, 20, oy)}" fill="none" '
                       f'stroke="{col}" stroke-width="1.4" stroke-linecap="round"/>')
    out.append("</svg>")
    return "\n".join(out)
