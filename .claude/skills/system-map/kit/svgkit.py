"""SVG primitives for the system map: boxes, frames, arrows and text that warn when they do not fit.

Engine file, seeded by the `system-map` skill. Layout is by hand (x, y, w, h in figures.py): a
generated layout reads worse than a drawn one. What keeps a drawing honest is not the layout but
the coverage checks in figures.coverage() and facts.collect().
"""
from __future__ import annotations

import html

WARN: list[str] = []
# Average glyph width per font size, for the fallback fonts a standalone SVG gets on GitHub.
MONO_K, SANS_K = 0.6, 0.53

NODE_KINDS = ("proc", "store", "ext", "off", "ok", "warn", "bad")
EDGE_KINDS = ("call", "main", "fail", "off")


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def width(s: str, size: float, mono: bool = False) -> float:
    return len(s) * size * (MONO_K if mono else SANS_K)


class Fig:
    """One figure. `slug` names its SVG file (docs/System/map/svg/<slug>.svg) and must be unique.

    `label` is the accessible description: say what the figure shows, in one or two sentences.
    """

    def __init__(self, slug: str, w: int, h: int, label: str):
        self.slug, self.w, self.h, self.label = slug, w, h, label
        self.fid = "f-" + slug
        self.p: list[str] = []

    # -- primitives
    def raw(self, s: str) -> None:
        self.p.append(s)

    def text(self, x, y, s, cls="t", anchor="start", size=None, mono=False) -> None:
        c = cls + (" m" if mono else "")
        st = f' font-size="{size}"' if size else ""
        self.p.append(f'<text x="{x}" y="{y}" class="{c}" text-anchor="{anchor}"{st}>{esc(s)}</text>')

    def rect(self, x, y, w, h, cls) -> None:
        self.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" class="{cls}"/>')

    # -- shapes
    def frame(self, x, y, w, h, title, kind="sys") -> None:
        """A boundary: the system (`sys`) or a host/platform (`host`). Title top-left."""
        self.rect(x, y, w, h, f"fr-{kind}")
        self.text(x + 14, y + 22, title, cls="fr-t", size=13.5)

    def box(self, x, y, w, h, title, lines=(), kind="proc", size=12, tsize=13.5) -> None:
        """A component. A title or line starting with a backtick is drawn in the monospace face."""
        assert kind in NODE_KINDS, kind
        self.rect(x, y, w, h, f"n-{kind}")
        mono = title.startswith("`")
        t = title.lstrip("`")
        if width(t, tsize, mono) > w - 16:
            WARN.append(f"{self.slug}: title {t!r} does not fit its box (width {w})")
        self.text(x + 10, y + 20, t, cls="ttl", size=tsize, mono=mono)
        yy = y + 37
        for ln in lines:
            m = ln.startswith("`")
            s = ln.lstrip("`")
            sz = size - 0.5 if m else size
            if width(s, sz, m) > w - 16:
                WARN.append(f"{self.slug}: line {s!r} does not fit its box (width {w})")
            self.text(x + 10, yy, s, cls="sub", size=sz, mono=m)
            yy += 16
        if yy - 9 > y + h:
            WARN.append(f"{self.slug}: box {t!r} is too short for its {len(lines)} lines")

    def arrow(self, pts, kind="call", label=None, at=None, anchor="middle", both=False, size=11.5) -> None:
        """A polyline from pts[0] to pts[-1]. `main` is the data path, `fail` an error path,
        `off` something optional or switched off. `at` is the label position."""
        assert kind in EDGE_KINDS, kind
        d = " ".join(f"{x},{y}" for x, y in pts)
        start = f' marker-start="url(#{self.fid}-s-{kind})"' if both else ""
        self.p.append(f'<polyline points="{d}" class="e-{kind}" fill="none" '
                      f'marker-end="url(#{self.fid}-a-{kind})"{start}/>')
        if label:
            if at is None:
                (x1, y1), (x2, y2) = pts[0], pts[-1]
                at = ((x1 + x2) / 2, (y1 + y2) / 2 - 6)
            self.text(at[0], at[1], label, cls=f"lbl lk-{kind}", anchor=anchor, size=size)

    # -- output
    def _defs(self) -> str:
        marks = []
        for k in EDGE_KINDS:
            marks.append(
                f'<marker id="{self.fid}-a-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
                f'markerHeight="7" markerUnits="userSpaceOnUse" orient="auto">'
                f'<path d="M0,0 L10,5 L0,10 z" class="h-{k}"/></marker>'
                f'<marker id="{self.fid}-s-{k}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" '
                f'markerHeight="7" markerUnits="userSpaceOnUse" orient="auto">'
                f'<path d="M10,0 L0,5 L10,10 z" class="h-{k}"/></marker>')
        return f'<defs>{"".join(marks)}</defs>'

    def inline(self, min_w: int = 900) -> str:
        """For the HTML page: styled by the page's own CSS. Scrolls sideways on narrow screens."""
        return (f'<svg viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.label)}" '
                f'style="min-width:{min(min_w, self.w)}px">{self._defs()}{"".join(self.p)}</svg>')

    def standalone(self, css: str) -> str:
        """A file of its own (GitHub, editors): carries its style and background."""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(self.label)}">\n'
                f'<title>{esc(self.label)}</title>\n<style>{css}</style>\n{self._defs()}\n'
                f'<rect x="0" y="0" width="{self.w}" height="{self.h}" class="bg"/>\n'
                + "\n".join(self.p) + "\n</svg>\n")
