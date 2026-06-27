# fleetman — Overview & Catch-Up (project 003)

> A grounded snapshot of what **fleetman** is, where it is *now* (verified against
> the code, not the docs), and the gap between its stated concept and reality.
> Companion to `PLAN.md` (remaining work) and `KICKOFF_PROMPT.md` (paste-ready
> session starter). All paths are relative to `/home/andrew/Documents/Projects/fleetman/`.

---

## 1. What fleetman IS (final concept)

fleetman is the **workspace/fleet manager** in the `*man` family. Where the other
managers (gitman, testee, repoman, docman, copyroom) are *per-repo*, fleetman is
*per-workspace*: it points at a directory of sibling repos and reports **what
projects exist and how they integrate**.

- It **derives everything from manifests** — `pyproject.toml` (python deps) and
  `flake.nix` (nix inputs) — so the map is *generated, never hand-maintained*
  (`README.md:8-10`, `src/fleetman/core.py:1-10`).
- It **imports/builds/enters nothing**: no indexed project's package is imported,
  no build runs, no devenv is activated. Only two manifest files per repo are read
  (`core.py` `_scan`, lines 85-133).
- It follows the shared `*man` **family contract**: a Typer CLI, a
  pydantic-normalized report, a devenv module (`nix/fleetman.nix`), an agent skill
  (`skills/fleetman/SKILL.md`, installed by `fleetman init`), and the `0/1/2/3`
  exit-code contract (`cli.py:7-9`).

### Why it is a separate repo from repoman

`repoman` is *by design* a per-repo conductor — its `CONCEPT.md §2` explicitly puts
fleet/workspace management **out of scope for v1**. fleetman owns that workspace
domain instead, following the same contract but at workspace scope. It is **not** a
repoman consumer: it ships its own `nix/fleetman.nix` and bootstraps as a plain
devenv (like gitman/testee), and does not import repoman's meta-module
(`.scratch/projects/001-brainstorming/CONCEPT.md:18-24`).

### The full/final concept (the whole intended arc)

The originating tower master plan called for a "repoman-fleet #14" subsystem with
**two write operations** on top of a discovery/index half:

1. **Indexer / graph** (read-only) — derive the fleet DAG from manifests. ← BUILT.
2. **`sync`** — clone/fetch a *declared* repo set (a `repos.toml` manifest) into the
   workspace: the bridge between "what should be on disk" and "what is on disk".
   ← NOT built.
3. **`flake-update`** — walk the consumer DAG in topological order running
   `nix flake update` + a per-node nixbuild test + a gitman commit (the
   publish-time lock-bump). Gated `--push`. ← NOT built.

The headline unresolved question at concept time was whether fleetman *grows* ops
2+3 (becoming the full #14) or stays a pure indexer with sync/lock-bump living
elsewhere (`001-brainstorming/CONCEPT.md:54-81, 91-101`). Project **002**
(`.scratch/projects/002-fleet-write-ops/PLAN.md`) answers "fleetman owns both,
phased." **Ownership resolved (2026-07-01): fleetman is the SOLE owner of fleet-wide
sync/write** — repoman's competing project 07 was retired
(`repoman/.scratch/projects/07-tower-repo-set-sync/SUPERSEDED.md`), leaving repoman
strictly per-repo. fleetman still has to *build* the sync surface; only the ownership
ambiguity is gone (see §4).

---

## 2. Where fleetman IS now (verified against code)

**Version `0.1.0`** (`pyproject.toml:3`). Deps are deliberately light: `pydantic>=2`
+ `typer>=0.12`; `tomllib` is stdlib. `requires-python >=3.11`.

**Test suite: 6 passed** — verified by `devenv shell -- python -m pytest -q`
(`tests/test_core.py`, 6 tests covering python-edge resolution across dep forms,
reverse-edge dependents, nix flake-input edges, stub detection not hijacking the
graph, doctor exit codes, and deterministic render with a Mermaid graph).

**Git:** trunk `main`, 3 commits — birth + two docs commits
(`b2c637e`, `587250e`, `ac54084`). No feature work has landed since birth; the two
scratch projects (001 brainstorming, 002 write-ops plan) are docs only.

### Commands (all implemented, `src/fleetman/cli.py`)

| Command | What it does | Code |
|---|---|---|
| `fleetman index [--root/-C] [--out]` | Harvest workspace → write `registry.json` + `PROJECTS.md`; prints project + edge counts | `cli.py:48-67` |
| `fleetman graph [--root]` | Print the Mermaid dependency graph to stdout (no files) | `cli.py:70-76` |
| `fleetman list [--root] [--family]` | List projects (name/family/layer/dependent-count/stub flag) | `cli.py:79-92` |
| `fleetman query <name> [--root]` | One project's purpose, deps (python + `(nix)`), dependents | `cli.py:95-110` |
| `fleetman doctor [--root]` | Is this workspace indexable? (root is a dir · projects found · manifests present) | `cli.py:113-120`, `doctor.py` |
| `fleetman init [--root] [--skills-dir]` | Install the fleetman agent skill into the workspace | `cli.py:123-130`, `init.py` |

Workspace root resolution: `--root/-C` > `$FLEETMAN_ROOT` > cwd (`cli.py:34-39`).

### Index generation (the built core)

`core.harvest(root)` (`core.py:136-177`) scans child directories (skipping
`.agents/.git/logs/vendor/work/Archive/node_modules` + dotfiles, `core.py:21`),
then per repo `_scan` reads `pyproject.toml` (`[project].dependencies`,
`optional-dependencies`, `[tool.uv.sources]`) and `flake.nix` inputs
(`github:` / `git+https://` URLs). It resolves cross-repo edges (directory name is
authoritative so uncustomized `template-py` clones don't shadow real repos,
`core.py:147-159`), computes reverse-edge `dependents`, and infers `(family, layer)`
from the naming convention (`family_and_layer`, `core.py:32-45`).

`render.py` emits the two artifacts (`write_index`, `render.py:113-119`):

- **`.agents/index/PROJECTS.md`** — per-family tables + a "most depended-on"
  ranking + a Mermaid graph (solid `-->` = python dep, dashed `-.->` = nix input).
- **`.agents/index/registry.json`** — the structured `Fleet` dump (source of truth).

Both artifacts already exist in the real workspace at
`/home/andrew/Documents/Projects/.agents/index/` (PROJECTS.md ~10k, registry.json
~37k, generated 2026-06-30). Per project 002's Phase-0 note, a real run indexed
**90 projects / 26 internal edges** with correct edges
(`002.../PLAN.md:8-16`).

### Nix wiring (`nix/fleetman.nix`)

A reusable devenv module exposing three tasks — `fleetman:index`,
`fleetman:graph`, `fleetman:doctor` — defaulting the root to the parent of
`DEVENV_ROOT` (override `$FLEETMAN_ROOT`). Note: **only read-only tasks are wired**;
`list`/`query`/`init` are CLI-only, and there are no sync/flake-update tasks yet.

---

## 3. What is OUTSTANDING

Nothing in the read-only indexer is missing — it is built, wired, and green. The
outstanding work is entirely the **fleet write-ops half** described in project 002,
none of which exists in the tree:

- **`fleetman sync`** — a `repos.toml`-driven clone/fetch of a *declared* repo set
  (planned `manifest.py` + `sync.py`; not present in `src/fleetman/`).
- **`fleetman flake-update`** — topo-order `nix flake update` + nixbuild + gitman
  commit, gated `--push` (planned `publish.py` + a `Fleet.topo_order()`; not present).
- The **manifest source-of-truth reconciliation** (auto-discovery E2 vs a declared
  `repos.toml` E1) and the **nix-terminal / nix-meta seam** decisions.

Ownership is no longer a blocker: fleetman is the sole owner of fleet-wide
sync/write (repoman project 07 retired 2026-07-01) — the work above simply needs to
be built (§4).

---

## 4. Concept ↔ reality gap table

| Concept / plan claim | Reality in the tree | Cited evidence |
|---|---|---|
| fleetman indexes a workspace → `registry.json` + `PROJECTS.md` | BUILT & green | `core.harvest`, `render.write_index`; artifacts at `../../.agents/index/` |
| `index / graph / list / query / doctor / init` commands | ALL implemented | `cli.py:48-130` |
| Edges derived from real manifests (E2 discovery) | BUILT — python + nix edges resolve across dep forms | `core.py:147-159`; `tests/test_core.py:50-71` |
| Test suite green; light deps; v0.1.0 | VERIFIED: 6 passed; `pydantic`+`typer` only | `pytest -q` run this session; `pyproject.toml:3,15-18` |
| `fleetman sync` (declared `repos.toml` clone/fetch) | **NOT built** — no `manifest.py`/`sync.py`; auto-discovers on-disk dirs instead | `002.../PLAN.md:34-64`; `001.../CONCEPT.md:67-70`; `ls src/fleetman/` |
| `fleetman flake-update` (topo publish, gated push) | **NOT built** — no `publish.py`; `Fleet` has `python_edges`/`nix_edges` but **no `topo_order()`** | `002.../PLAN.md:68-93`; `models.py:51-58` |
| Reads nix-meta's `repos.toml` manifest (E1) | **NOT done** — root comes from `$FLEETMAN_ROOT`/cwd, not a manifest | `cli.py:34-39`; `001.../CONCEPT.md:69-70` |
| nix module wires the fleet ops | Only `index/graph/doctor` tasks wired; no sync/flake-update tasks | `nix/fleetman.nix:20-24` |
| "fleetman owns both write-ops" (002 decision) | **RESOLVED — fleetman is the SOLE owner** of fleet-wide sync/write (repoman project 07 retired 2026-07-01) | `002.../PLAN.md`; `repoman .../07-tower-repo-set-sync/SUPERSEDED.md` |
| repoman project 07 (`repoman fleet-sync`) | **RETIRED (2026-07-01)** — cancelled, never built; fleet-sync descoped to fleetman | `repoman .../07-tower-repo-set-sync/SUPERSEDED.md` |

**Bottom line:** the indexer is done and correct; the entire fleet-write surface
(`sync` + `flake-update`) is still **unbuilt** and is fleetman's remaining work.
Ownership is settled — fleetman is the sole owner (repoman 07 retired 2026-07-01) —
so the only open items are the build itself and the manifest/seam decisions (see
`PLAN.md`).
