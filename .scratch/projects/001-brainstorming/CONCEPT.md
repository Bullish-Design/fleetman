# fleetman — Concept Requirements

> Brainstorming concept doc for the `fleetman` repo. Grounded in the **actual repo**
> (README, pyproject, `src/fleetman/*`, `nix/fleetman.nix`) — which has **diverged**
> from the original master plan `repoman-fleet-PLAN.md`. Reconciliation with the tower
> plan (`00-INDEX.md` §E + build item #14, `INTEGRATION-REVIEW.md` 6c) is in the
> "Plan reconciliation" section below. Repo-owned distillation.

## Role (one paragraph)
`fleetman` is the **workspace/fleet manager** in the `*man` family. Where the other
managers (gitman, testee, repoman, …) are *per-repo*, fleetman is *per-workspace*: it
indexes a directory of sibling repos and reports **what projects exist and how they
integrate**. It derives everything from **manifests** (`pyproject.toml` for python deps,
`flake.nix` for nix inputs) — the map is *generated, never hand-maintained*. No indexed
project is imported, built, or entered (its devenv is never activated); only its
manifests are read.

## Why it's a separate repo (not folded into repoman)
`repoman` is, by design, a *per-repo* conductor — `repoman/CONCEPT.md` explicitly puts
fleet/workspace management **out of scope**. fleetman owns the workspace domain instead,
following the same family contract: a Typer CLI, a pydantic-normalized report, a devenv
module (`nix/fleetman.nix`), an agent skill, and the shared `0/1/2/3` exit codes. It is
**NOT a repoman consumer** — it ships its own `nix/fleetman.nix` and bootstraps directly
(plain devenv like gitman/testee), so it does not import repoman's meta-module.

## Purpose & scope (as built)
**Commands:** `index` (write `registry.json` + `PROJECTS.md`), `graph` (Mermaid dep
graph), `list [--family]`, `query <project>` (purpose, deps, dependents), `doctor` (is
this workspace indexable?), `init` (install the agent skill into the workspace).
Workspace root = cwd, `--root/-C`, or `$FLEETMAN_ROOT`.

**Outputs:**
- `.agents/index/PROJECTS.md` — per-family tables + Mermaid graph (solid edge = python
  dep, dashed = nix input).
- `.agents/index/registry.json` — structured source of truth for agents.

**Conventions encoded:** naming families map to roles — `*dantic` libs · `*man` tools ·
`nix-*` infra · `*.nvim` plugins · `template-*` scaffolds. Uncustomized `template-py`
clones are flagged as stubs.

**Deliberately light:** stdlib (`tomllib`) parses; deps are just `pydantic>=2` +
`typer>=0.12`. Read-only manifest analysis — no clone, no build, no devenv entry.

## Shape + outputs
- **Python package** `fleetman` (`src/fleetman/{cli,core,models,render,doctor,init}.py`),
  console script `fleetman = fleetman.cli:app`, hatchling build; ships
  `skills/fleetman/SKILL.md` as package data (so `fleetman init` installs it).
- **`nix/fleetman.nix`** — a reusable **devenv module** (not a repoman manager) exposing
  `tasks.fleetman:{index,graph,doctor}`, defaulting the workspace root to the parent of
  `DEVENV_ROOT` (override `$FLEETMAN_ROOT`). Import from a workspace-root devenv or any
  repo's devenv that should expose the fleet map.
- Tests under `tests/` (`test_core.py`). gitman/jj bootstrapped, trunk `main`.

## Plan reconciliation (IMPORTANT — fleetman ≠ the old "repoman-fleet" #14)
The tower master plan's build item **#14 ("repoman fleet extension")** specified two
*write* operations as a subsystem **inside the repoman package**:
- **`fleet sync`** — clone/fetch the repo set into `~/Documents/Projects` from a declared
  manifest (`repos.toml`, home = nix-meta; read via `--manifest`/`$REPOMAN_FLEET_MANIFEST`).
- **`fleet flake-update`** — walk the consumer DAG in topological order running
  `nix flake update` + `nixbuild test` + gitman commit/push (the publish-time lock-bump).

These are referenced by: nix-meta capability **`fleetSync`**, the **`nix-terminal.repoman.fleet.{enable,manifest,projectsDir}`** HM seam, and `00-INDEX §E` (E1 repos.toml in nix-meta, E2 derive-edges-from-manifests, E4 per-node nixbuild target, E5 halt-on-fail).

**What's actually built in fleetman is the INDEXER, not sync/flake-update.** fleetman:
- ✅ embodies **E2** (derives the DAG from real `flake.nix`/`pyproject.toml` manifests at
  runtime, not a declared edge list) — this is the discovery/graph half.
- ❌ does **not** clone/fetch (`fleet sync`) — it discovers an *existing* sibling-repo dir.
- ❌ does **not** lock-bump/topo-publish (`fleet flake-update`).
- ❌ does **not** read nix-meta's `repos.toml` (it auto-discovers from `$FLEETMAN_ROOT`,
  not a declared manifest — diverges from E1).

**→ Headline open reconciliation (needs a decision):** does fleetman *grow* the
`sync` + `flake-update` write-operations (becoming the full #14), or does it stay a
pure read-only indexer with sync/lock-bump living elsewhere (repoman? a new tool?
dropped)? If fleetman is the new home, the tower seams must be repointed:
- `nix-terminal.repoman.fleet.*` → likely a `fleetman`-named seam (or fleetman provides
  the CLI the existing seam shells out to).
- nix-meta capability `fleetSync` → wires fleetman.
- `repos.toml` (E1) vs fleetman's manifest auto-discovery (E2) — reconcile the manifest
  source of truth.

## Inter-repo position
- **Family contract:** `*man` tool — Typer CLI + pydantic report + devenv module + agent
  skill + `0/1/2/3` exit codes. Sibling to gitman/testee/repoman/docman/copyroom.
- **Consumed by:** a workspace-root devenv (or any repo's devenv via `nix/fleetman.nix`);
  agents read `.agents/index/registry.json`. In the tower fleet it is the natural home
  for "what repos exist + how they integrate" — overlapping the `repoman fleet-sync`
  discovery role the master plan put in repoman.
- **Reads:** sibling repos' `pyproject.toml` + `flake.nix` only. Imports/builds nothing.

## Open questions
1. **The headline reconciliation above** — does fleetman own `sync` + `flake-update`, or
   only the index/graph? This gates the nix-meta `fleetSync` capability + the
   nix-terminal `repoman.fleet` seam + the `repos.toml`-vs-auto-discovery (E1/E2) call.
2. **Manifest source of truth:** auto-discover from `$FLEETMAN_ROOT` (as built) vs a
   declared `repos.toml` in nix-meta (plan E1). Possibly both (discovery + an optional
   manifest filter).
3. **Seam naming:** keep `nix-terminal.repoman.fleet.*` or rename to a fleetman seam.
4. **Add `loci-core` + the nix-* / *.nvim fleet repos** to whatever fleetman indexes
   (CONFLICT-4 / E3 wanted loci-core in the repo set).

## Validation / done-criteria (as an indexer)
- `fleetman index --root <Projects>` writes a `registry.json` + `PROJECTS.md` that lists
  the tower fleet repos with correct family classification + dep/nix-input edges.
- `fleetman graph` emits a Mermaid graph matching the real DAG
  (`loci-core → loci.nvim → nix-nvim → nix-terminal → nix-meta` + leaf libs).
- `fleetman doctor` reports the workspace indexable; `fleetman query <repo>` returns
  purpose/deps/dependents.
- `nix flake check` / the repo's own test suite green.

## Status
Repo **exists + is substantially built** (Python pkg with cli/core/models/render/doctor/
init, tests, `nix/fleetman.nix` devenv module, agent skill), gitman/jj bootstrapped
(trunk `main`). This CONCEPT.md added this session. **It is the renamed/redesigned
successor to the master plan's "repoman-fleet" (#14)** but currently implements only the
**indexer** half — the `sync`/`flake-update` write-operations + the nix-meta/nix-terminal
seam reconciliation remain open (see above).
