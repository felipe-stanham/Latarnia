"""Facts of the system map, read from the repository.

PROJECT FILE. The `system-map` skill rewrites this skeleton for the project's stack.

collect(root) returns (F, problems):
  F         facts the figures and sections draw from: names, lists, values, counts.
  problems  one line per thing the map does not cover yet, or per claim the code now contradicts,
            each naming the file and constant to edit. build.py --check fails while there are any.

Three kinds of fact, three patterns (examples below):
  1. Sets the map draws one by one (modules, tables, threads, jobs, sources, events): keep the set
     the drawing knows in a KNOWN_* constant and report every difference, both ways.
  2. Values the text quotes (versions, defaults, allowed values, schedules): read them from the code
     into F. Never type them into figures.py or sections.py.
  3. Hand-written claims the code could make false ("X is switched off", "Y runs in its own
     thread", "only Z writes table T"): a guard that searches for the contradiction.

Only files git tracks or would track count, so ignored and local files never change the map.
Standard library only.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------- helpers


def tracked_files(root: Path, sub: str = "", suffix: str = "", recursive: bool = True) -> list[Path]:
    """Files under `sub` (repo-relative) that git tracks or would track, sorted."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                              "--exclude-standard", "--", sub or "."],
                             capture_output=True, check=True).stdout
        paths = [root / n for n in out.decode("utf-8").split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in (root / sub).rglob("*") if ".git" not in p.parts]
    base = root / sub
    return sorted(p for p in paths if p.is_file() and p.name.endswith(suffix)
                  and (recursive or p.parent == base))


def code(path: Path, comment: str = "#") -> str:
    """Source without full-line comments, so commented-out code never counts as running."""
    return "\n".join(ln for ln in path.read_text(encoding="utf-8", errors="replace").split("\n")
                     if not ln.strip().startswith(comment))


# ---------------------------------------------------------------------------- pattern 1: a drawn set
# Top-level areas of the repository, as an example. Replace with what the map really draws:
# modules per group, DB tables, threads, scheduled jobs, external services, event types...
KNOWN_AREAS = {
    # "<dir>": "<label in the map>",
}
# Top-level entries that are not part of the system (docs, tooling).
NOT_DRAWN = {"docs", ".claude", ".githooks", ".github", "tests"}


def _areas(root: Path) -> list[str]:
    tops = {p.relative_to(root).parts[0] for p in tracked_files(root)}
    return sorted(t for t in tops if (root / t).is_dir() and t not in NOT_DRAWN)


# ---------------------------------------------------------------------------- collect


def collect(root: Path) -> tuple[dict, list[str]]:
    problems: list[str] = []

    areas = _areas(root)
    for a in areas:
        if a not in KNOWN_AREAS:
            problems.append(f"New area {a}/: add it to KNOWN_AREAS in docs/System/map/facts.py and place it "
                            f"in POSITIONS in docs/System/map/figures.py (or add it to NOT_DRAWN).")
    for a in sorted(set(KNOWN_AREAS) - set(areas)):
        problems.append(f"Area {a}/ no longer exists: remove it from KNOWN_AREAS (facts.py) and POSITIONS "
                        f"(figures.py).")

    # Pattern 2 — a value the text quotes, read from the code. Example:
    #   m = re.search(r'^VERSION\s*=\s*"([^"]+)"', (root / "app/version.py").read_text(), re.M)
    #   version = m.group(1) if m else "?"

    # Pattern 3 — a guard for a hand-written claim. Example: the map shows the reference resolver
    # as switched off; fail as soon as something imports it again.
    #   callers = [p for p in tracked_files(root, "app", ".py") if p.stem != "reference_resolver"
    #              and re.search(r"\breference_resolver\b", code(p))]
    #   if callers:
    #       problems.append("Reference Resolution is called again in " + ", ".join(...) + ": the map "
    #                       "shows it switched off. Draw it in fig_overview and remove it from the "
    #                       "'Switched off' section (sections.py).")

    F = {
        "areas": [(a, KNOWN_AREAS[a]) for a in areas if a in KNOWN_AREAS],
    }
    return F, problems
