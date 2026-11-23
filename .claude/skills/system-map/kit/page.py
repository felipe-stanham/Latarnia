"""Page blocks, styles and renderers of the system map: one block list renders to both HTML and
Markdown, so the two outputs never disagree.

Engine file, seeded by the `system-map` skill. To restyle the map, change the tokens below (a
project with a design system maps its own tokens here); keep every token in both LIGHT and DARK.
"""
from __future__ import annotations

import html
import re

from svgkit import esc

# ---------------------------------------------------------------------------- tokens
LIGHT = {
    "bg": "#F6F7F9", "raised": "#FFFFFF", "frame": "#ECEFF3",
    "ink": "#16202A", "ink-2": "#46525E", "ink-3": "#5F6B77", "rule": "#D3D9DF", "rule-strong": "#8E9AA6",
    "accent": "#2456C7", "accent-soft": "#E3EBFB", "store": "#D8E9E4", "ext": "#F0F1F3",
    "ok": "#1D6B42", "ok-soft": "#DEF0E5", "warn": "#8A5A00", "warn-soft": "#FBEFD5",
    "bad": "#B3261E", "bad-soft": "#FBE4E2",
}
DARK = {
    "bg": "#10161C", "raised": "#18212A", "frame": "#141C24",
    "ink": "#E7ECF0", "ink-2": "#B9C3CC", "ink-3": "#97A3AE", "rule": "#2B3742", "rule-strong": "#56636F",
    "accent": "#86A9FF", "accent-soft": "#1D2A47", "store": "#21403A", "ext": "#1A2128",
    "ok": "#6FD29B", "ok-soft": "#173826", "warn": "#E8B758", "warn-soft": "#3A2C10",
    "bad": "#F28B82", "bad-soft": "#3F1D1B",
}
FONTS = ('--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;'
         '--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;')


def _vars(tokens: dict) -> str:
    return "".join(f"--{k}:{v};" for k, v in tokens.items())


SVG_RULES = """
text{font-family:var(--sans)}
.t{fill:var(--ink)} .sub{fill:var(--ink-2)} .ttl{fill:var(--ink);font-weight:650} .m{font-family:var(--mono)}
.cap{fill:var(--ink-3);font-size:11.5px;font-weight:600;letter-spacing:.05em}
.fr-t{fill:var(--ink);font-weight:680}
.lbl{font-weight:500;paint-order:stroke;stroke:var(--raised);stroke-width:4px;stroke-linejoin:round}
.lk-call{fill:var(--ink-2)} .lk-main{fill:var(--accent)} .lk-fail{fill:var(--bad)} .lk-off{fill:var(--ink-3)}
.bg{fill:var(--raised)}
.fr-sys{fill:var(--frame);stroke:var(--ink);stroke-width:1.5} .fr-host{fill:none;stroke:var(--rule-strong);stroke-dasharray:6 4}
.n-proc{fill:var(--raised);stroke:var(--ink);stroke-width:1.25} .n-store{fill:var(--store);stroke:var(--rule-strong)}
.n-ext{fill:var(--ext);stroke:var(--rule-strong)} .n-off{fill:var(--raised);stroke:var(--ink-3);stroke-dasharray:5 4}
.n-ok{fill:var(--ok-soft);stroke:var(--ok)} .n-warn{fill:var(--warn-soft);stroke:var(--warn)} .n-bad{fill:var(--bad-soft);stroke:var(--bad)}
.e-call{stroke:var(--ink-2);stroke-width:1.4} .e-main{stroke:var(--accent);stroke-width:2.25}
.e-fail{stroke:var(--bad);stroke-width:1.5;stroke-dasharray:5 4} .e-off{stroke:var(--ink-3);stroke-width:1.4;stroke-dasharray:2 4}
.h-call{fill:var(--ink-2)} .h-main{fill:var(--accent)} .h-fail{fill:var(--bad)} .h-off{fill:var(--ink-3)}
""".replace("\n", "")

SVG_CSS = (f":root{{{_vars(LIGHT)}{FONTS}}}"
           f"@media (prefers-color-scheme:dark){{:root{{{_vars(DARK)}}}}}" + SVG_RULES)

PAGE_CSS = f"""
:root{{{_vars(LIGHT)}{FONTS}color-scheme:light}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{_vars(DARK)}color-scheme:dark}}}}
:root[data-theme="dark"]{{{_vars(DARK)}color-scheme:dark}}
body{{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.5;margin:0}}
.wrap{{max-width:1320px;margin:0 auto}}
.top,main,nav{{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}}
.top{{background:var(--frame);border-bottom:1px solid var(--rule);padding-block:28px 22px}}
h1{{font-size:28px;line-height:1.15;font-weight:700;margin:0 0 10px}}
.lede{{max-width:80ch;margin:0;font-size:16px}}
nav{{position:sticky;top:0;z-index:5;background:var(--raised);border-bottom:1px solid var(--rule)}}
nav .wrap{{display:flex;gap:4px 18px;flex-wrap:wrap;padding-block:7px}}
nav a{{color:var(--ink-2);font-size:13.5px;font-weight:600;text-decoration:none;padding:3px 0;border-bottom:2px solid transparent}}
nav a:hover,nav a:focus-visible{{border-bottom-color:var(--accent);color:var(--ink)}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
main{{padding-block:8px 48px}}
section{{padding-block:28px 8px;scroll-margin-top:44px}}
h2{{font-size:21px;line-height:1.2;font-weight:680;margin:0 0 6px;text-wrap:balance}}
h3{{font-size:15.5px;font-weight:650;margin:22px 0 6px}}
section p{{max-width:82ch;margin:0 0 12px;color:var(--ink-2)}}
code{{font-family:var(--mono);font-size:.88em}}
figure{{margin:12px 0 0}}
.scroll{{overflow-x:auto;background:var(--raised);border:1px solid var(--rule);border-radius:4px;padding:10px}}
figcaption{{font-size:13.5px;color:var(--ink-3);margin-top:7px;max-width:95ch}}
svg{{display:block;width:100%;height:auto}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 22px;margin:14px 0 0;padding:0;list-style:none;font-size:13.5px;color:var(--ink-2)}}
.legend li{{display:flex;align-items:center;gap:7px}}
.legend svg{{width:30px;height:14px;flex:none}}
table{{border-collapse:collapse;width:100%;font-size:14px;background:var(--raised)}}
th{{background:var(--frame);text-align:left;font-weight:600;font-size:12.5px;padding:7px 10px;border-bottom:1px solid var(--rule-strong)}}
td{{padding:7px 10px;border-bottom:1px solid var(--rule);vertical-align:top}}
.tscroll{{overflow-x:auto;border:1px solid var(--rule);border-radius:4px;margin-top:10px}}
.tscroll table{{min-width:640px}}
.items{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:10px 26px;margin:10px 0 0;padding:0;list-style:none}}
.items li{{min-width:0;font-size:14px;color:var(--ink-2)}}
.items b{{display:block;color:var(--ink);font-weight:650}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0;padding:0;list-style:none}}
.chips li{{font-family:var(--mono);font-size:12px;background:var(--raised);border:1px solid var(--rule);border-radius:4px;padding:2px 7px}}
{SVG_RULES}
@media (max-width:640px){{h1{{font-size:24px}}.scroll{{padding:6px}}}}
"""

# ---------------------------------------------------------------------------- blocks
# Text in P, H3, captions, cells and items is HTML (use c() for code). Markdown keeps the inline
# HTML, which GitHub renders.


def c(s) -> str:
    return f"<code>{html.escape(str(s))}</code>"


def P(text):
    return ("p", text)


def H3(text):
    return ("h3", text)


def FIG(fig, caption):
    return ("fig", fig, caption)


def TABLE(head, rows):
    return ("table", head, rows)


def ITEMS(items):
    """A grid of (term, description) pairs: states, entry points, settings."""
    return ("items", items)


def CHIPS(items):
    """A row of short code-like names: tools, flags, event types."""
    return ("chips", items)


# Legend entries: (kind, label). Kinds are the svgkit classes: e-<edge> or n-<node>.
LEGEND_WORDS = {
    "e-main": "accent line", "e-call": "grey line", "e-fail": "red dashed line", "e-off": "dotted line",
    "n-proc": "box with dark border", "n-store": "green-grey box", "n-ext": "light grey box",
    "n-off": "dashed box", "n-ok": "green box", "n-warn": "amber box", "n-bad": "red box",
}


def legend_html(items) -> str:
    out = []
    for kind, label in items:
        if kind.startswith("e-"):
            sw = f'<polyline points="1,7 29,7" class="{kind}" fill="none"/>'
        else:
            sw = f'<rect x="1" y="1" width="28" height="12" rx="2" class="{kind}"/>'
        out.append(f'<li><svg viewBox="0 0 30 14" aria-hidden="true">{sw}</svg>{label}</li>')
    return '<ul class="legend" aria-label="Legend">' + "".join(out) + "</ul>"


def legend_md(items) -> str:
    return "; ".join(f"{LEGEND_WORDS.get(k, k)}: {label}" for k, label in items) + "."


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _table_html(head, rows, wrap=True) -> str:
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    t = f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"
    return f'<div class="tscroll">{t}</div>' if wrap else t


def render_html(sections) -> str:
    out = []
    for anchor, title, blocks in sections:
        out.append(f'<section id="{esc(anchor)}"><div class="wrap"><h2>{title}</h2>')
        for b in blocks:
            kind = b[0]
            if kind == "p":
                out.append(f"<p>{b[1]}</p>")
            elif kind == "h3":
                out.append(f"<h3>{b[1]}</h3>")
            elif kind == "fig":
                out.append(f'<figure><div class="scroll" tabindex="0">{b[1].inline()}</div>'
                           f"<figcaption>{b[2]}</figcaption></figure>")
            elif kind == "table":
                out.append(_table_html(b[1], b[2]))
            elif kind == "items":
                out.append('<ul class="items">' + "".join(f"<li><b>{t}</b>{d}</li>" for t, d in b[1]) + "</ul>")
            elif kind == "chips":
                out.append('<ul class="chips">' + "".join(f"<li>{x}</li>" for x in b[1]) + "</ul>")
            else:
                raise ValueError(f"unknown block {kind!r}")
        out.append("</div></section>")
    return "\n".join(out)


def render_md(sections) -> str:
    out = []
    for _anchor, title, blocks in sections:
        out.append(f"## {_strip_tags(title)}")
        for b in blocks:
            kind = b[0]
            if kind == "p":
                out.append(b[1])
            elif kind == "h3":
                out.append(f"### {_strip_tags(b[1])}")
            elif kind == "fig":
                out.append(f"![{esc(b[1].label)}](map/svg/{b[1].slug}.svg)\n\n*{_strip_tags(b[2])}*")
            elif kind == "table":
                out.append(_table_html(b[1], b[2], wrap=False))
            elif kind == "items":
                out.append("\n".join(f"- **{t}.** {d}" for t, d in b[1]))
            elif kind == "chips":
                out.append(" · ".join(b[1]))
    return "\n\n".join(out) + "\n"
