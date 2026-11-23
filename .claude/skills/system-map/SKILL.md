---
name: system-map
description: Builds the project's system map — an HTML page (plus Markdown and SVGs) generated from the code by docs/System/map/build.py, explaining the whole system to a newcomer — and wires `build.py --check` as an architecture invariant so the map can never silently go stale. Invoke to set the map up for the first time, or to add or redesign a figure.
---

## What This Skill Does

Hand-written docs rot because nothing checks them against the code. The system map is the part of
the docs a machine can check. It is **generated from the repository**, and its check fails both when
the committed map is stale and when the code has something the drawings do not cover yet.

| Path | Owner | What it is |
|---|---|---|
| `docs/System/map/index.html` | generated | The map: one page, light/dark, sticky index. Open it in a browser. |
| `docs/System/map.md` | generated | The same content for GitHub and editors (SVGs + Markdown). |
| `docs/System/map/svg/*.svg` | generated | One standalone SVG per figure. |
| `docs/System/map/build.py`, `page.py`, `svgkit.py` | project (seeded from `kit/`) | Engine: rendering, styles, the `--check`. Rarely edited. |
| `docs/System/map/facts.py` | project (written here) | Reads the facts from the code; reports what the drawings do not cover. |
| `docs/System/map/figures.py` | project (written here) | The drawings (hand layout) and `coverage()`. |
| `docs/System/map/sections.py` | project (written here) | Page text: title, lede, legend, sections. |

**Contract** (what the rest of the template relies on):

- `python3 docs/System/map/build.py` regenerates the outputs; `--check` writes nothing and exits 1 when
  (a) the outputs differ from what the code generates today, or (b) `facts.collect()` /
  `figures.coverage()` report a problem. Every problem message names the file and constant to edit.
- Generated files are never edited by hand. Two builds of the same commit are identical (no
  timestamps, sorted inputs, only files git tracks or would track).
- Standard library only, no network: it runs in the pre-push hook and in CI with a bare `python3`.

How it stays current, once set up:

- The `doc-updater` runs `build.py` in step 6 of the Execution Loop, so the map is committed with the
  code that changed it, and fixes the files a problem names.
- `build.py --check` is an Architecture Invariant: `.claude/arch-check.sh` runs it before every push
  (hook `05`) and in CI — so it also catches commits made outside Claude Code.
- The docs gate (hook `01`) does not count the generated files as "docs updated".

---

## Session Startup

1. Read `docs/SYSTEM.md`, `docs/System/glossary.md`, and the hand-written docs in `docs/System/`.
2. If `docs/System/map/build.py` exists, read `facts.py`, `figures.py`, `sections.py` and run
   `python3 docs/System/map/build.py --check`. This is a change to an existing map: skip to the step
   the user's request needs.
3. Run `git branch --show-current`. The map's Python files, `.claude/arch-check.sh` and the CI workflow
   are code: they are written in a task branch. If there is no task yet, create one with the `task`
   skill (Goal: "System map generated from the code, checked as an invariant"), then work in its
   worktree.

---

## Steps (first setup)

### 1. Explore the system

Find what a newcomer needs to see, with evidence from the code. Use an `Explore` subagent for breadth.
For each item, note **where the code declares it**: that is what `facts.py` will read.

- Processes and entry points (main, CLI commands, workers, serverless handlers)
- Modules and how they group into the subsystems named in `docs/SYSTEM.md`
- Data stores, tables/collections, and which module reads or writes each
- Concurrency: threads, async tasks, queues, scheduled jobs and their schedules
- External services and integrations; events/messages in and out
- Configuration: config keys, `ENV` values, feature flags, defaults
- Environments and deployment shape (never hostnames or secrets: those stay in `docs/local/`)
- What is switched off, stubbed, or pending

### 2. Propose the figures

Show the user 3–8 figures, each answering one question a newcomer asks. A typical set:

| Figure | Question |
|---|---|
| Overview (always) | What are the parts, and how does data flow through them? |
| Journey of the main entity | What happens to one request / document / order, step by step, and where can it fail? |
| Who runs what | Which threads, workers, or jobs exist, what triggers each, what do they share? |
| Schedule | When do the scheduled jobs run? |
| Data model | Which tables exist and how do they relate? |
| Access matrix | Which module reads or writes which table? |
| Environments | Which branches deploy where, and how do the environments differ? |

Plus non-figure sections where a table reads better (endpoints, config keys, events, what is
switched off). Agree on the list before writing code.

### 3. Seed the kit

```bash
mkdir -p docs/System/map
cp .claude/skills/system-map/kit/*.py docs/System/map/
chmod +x docs/System/map/build.py
```

The skeletons run as-is and show the three patterns; replace their example content.

### 4. Write `facts.py`

For every figure and section, decide where each piece comes from — one of three patterns:

1. **A set the map draws one by one** (modules, tables, threads, jobs, sources, event types): read it
   from the code, keep the set the drawing knows in a `KNOWN_*` constant (or a layout dict keyed by
   name in `figures.py`), and report every difference **in both directions**: new in the code → "draw
   it here"; gone from the code → "remove it here".
2. **A value the text quotes** (version, default, allowed values, cron schedule, count): read it into
   `F`. Never type it into `figures.py` or `sections.py`.
3. **A hand-written claim the code could make false** ("X is switched off", "recovery runs in its own
   thread", "only the ingest module writes `documents`"): a guard that searches for the contradiction
   and reports it. **Every claim in the map that is not computed from `F` needs a guard.** This is what
   makes the map trustworthy; it is where most of the thinking goes.

Read only with `tracked_files()` (git-tracked or would-be-tracked files) and strip comments with
`code()` so commented-out calls never count. If an expected file is missing, report a problem with
the path rather than crashing.

### 5. Write `figures.py`

Hand layout with `svgkit.Fig` (`frame`, `box`, `arrow`, `text`). Names, counts and values come from `F`;
positions are keyed by the fact's name, so `coverage()` can report a name with no position.
Keep figures 900–1400 px wide. Use the box and arrow kinds consistently with the legend:
`proc` / `store` / `ext` / `off`, and `main` (data path) / `call` / `fail` / `off`.

### 6. Write `sections.py`

Title, `LANG` (the language the project's docs are written in), lede, legend, and one section per
figure in reading order: from the overview to the detail. Glossary terms only. Values from `F`.
Captions say how to read the figure, not what it already shows. End with the "How this map stays
current" section from the skeleton.

### 7. Build and look at it

```bash
python3 docs/System/map/build.py
```

Fix every problem it lists (text overflow included). Open `docs/System/map/index.html` in the
browser (built-in browser pane) at desktop and phone width, in light and dark: no overlapping
labels, no crossing arrows that could be avoided, nothing cut off.

### 8. Prove the check

For each pattern, break the code on purpose in the working tree, confirm `--check` fails with a
message that says what to edit, and revert:

- Add a fake module/table/thread → problem "draw it".
- Violate one guarded claim (uncomment a disabled call) → problem naming the claim.
- Append a line to `docs/System/map.md` → "stale".

Then run `--check` twice on a clean tree: both pass, nothing changes.

### 9. Wire it as an invariant

Follow the `arch-check` skill's conventions:

- `docs/SYSTEM.md` → Architecture Invariants: a row
  `| n | The system map matches the code | python3 docs/System/map/build.py --check | .claude/arch-check.sh |`
  (on `dev` in the primary checkout — it is planning).
- `.claude/arch-check.sh` (in the task branch): `run "n system-map-current" python3 docs/System/map/build.py --check`.
  Create the script from the `arch-check` skill's template if it does not exist, and make it
  executable (`chmod +x`, or `git update-index --chmod=+x`).
- CI: if `.github/workflows/arch-check.yml` does not exist, seed it from
  `docs/templates/ci-arch-check.template.yml`. `ubuntu-latest` has `python3`; no setup step needed for the map.
- `docs/SYSTEM.md` → "System Docs": link `docs/System/map/index.html` (and `map.md`).

### 10. Remove the hand copies

Search the hand-written docs (`docs/SYSTEM.md`, `docs/System/*.md`) for values and lists the map now
generates (versions, route/table lists, allowed values, schedules). Replace each with a pointer to the
map or to the source. Fewer copies, fewer differences.

Commit everything in the task branch through the Execution Loop (the `code-reviewer` reviews the map
code like any other code).

---

## Changing the map later

- **A problem from `--check`** (usually seen by the `doc-updater`): edit the file it names, rebuild.
- **A new figure or section**: add `fig_*` in `figures.py` with its coverage rule, the section in
  `sections.py`, and the facts it needs in `facts.py` — with a guard for every new hand-written claim.
- **Publishing**: `build.py --artifact page.html` writes the page head and body to one file, ready to
  publish as a page elsewhere; the `share` skill can link `docs/System/map/index.html` from a share.

## Rules

- Never edit `map.md`, `index.html` or `svg/*` by hand. Never weaken a coverage rule or a guard to make
  `--check` pass: either the map is wrong (fix the drawing) or the code is (fix the code, in a task).
- Every value in the map comes from `F`; every claim not computed from `F` has a guard.
- Standard library only; no network, no timestamps, no reading of ignored or local files.
- Never put hostnames, credentials, or deployment targets in the map — the same rule as all of `docs/`.
