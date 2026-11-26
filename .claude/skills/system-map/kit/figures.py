"""Figures of the system map.

PROJECT FILE. The `system-map` skill rewrites this skeleton for the project.

Each fig_*(F) returns an svgkit.Fig. Layout is drawn by hand; names, counts and values come from F.
When F holds something a figure has no place for, the figure must not guess: coverage(F) reports
it, naming the constant to edit.

Box kinds: proc (the system's own code), store (keeps data), ext (outside the system),
off (switched off or pending), ok / warn / bad (states).
Arrow kinds: main (the data path), call (a call or trigger), fail (an error path), off (optional).
"""
from __future__ import annotations

from svgkit import Fig

# Where each area is drawn in fig_overview: name → (x, y). Hand layout, keyed by the fact's name.
POSITIONS = {
    # "<dir>": (40, 70),
}
BOX_W, BOX_H = 200, 64


def coverage(F: dict) -> list[str]:
    """What F has that the drawings lack. One line per problem, naming the constant to edit."""
    problems = []
    for name, _label in F["areas"]:
        if name not in POSITIONS:
            problems.append(f"Area {name}/ has no place in fig_overview: add it to POSITIONS in "
                            f"docs/System/map/figures.py.")
    return problems


def fig_overview(F: dict) -> Fig:
    placed = [(n, lbl, POSITIONS[n]) for n, lbl in F["areas"] if n in POSITIONS]
    right = max([x + BOX_W for _, _, (x, _) in placed], default=440) + 40
    bottom = max([y + BOX_H for _, _, (_, y) in placed], default=140) + 30
    f = Fig("01-overview", right, bottom,
            "Overview: the parts of the system and how they connect.")
    f.frame(16, 30, right - 32, bottom - 46, "The system")
    if not placed:
        f.text(40, 90, "Nothing drawn yet: run the system-map skill.", cls="sub", size=13)
    for name, label, (x, y) in placed:
        f.box(x, y, BOX_W, BOX_H, label, ["`" + name + "/"], "proc")
    return f
