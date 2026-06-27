# KICKOFF — fleetman catch-up session

> Paste this into a fresh session. You are starting from the fleetman repo root:
> `/home/andrew/Documents/Projects/fleetman/`.

You are picking up **fleetman**, the workspace/fleet manager in the `*man` family.
Unlike the per-repo managers (gitman, repoman, testee), fleetman is *per-workspace*:
it points at a directory of sibling repos and reports what projects exist and how
they integrate, deriving the whole map from each repo's `pyproject.toml` and
`flake.nix` manifests. It imports/builds/enters nothing.

## Current state (verified)

- The **read-only indexer half is DONE and green.** Commands `index`, `graph`,
  `list`, `query`, `doctor`, `init` all work; `index` writes
  `.agents/index/{registry.json,PROJECTS.md}` (per-family tables + a Mermaid dep
  graph). Version `0.1.0`; deps are just `pydantic` + `typer`; **6 tests pass**
  (`devenv shell -- python -m pytest -q`).
- The **fleet-write half is entirely unbuilt**: no `fleetman sync` (declared
  `repos.toml` clone/fetch) and no `fleetman flake-update` (topo-order publish).
  These are planned in `.scratch/projects/002-fleet-write-ops/PLAN.md`.

## Read these first

- `.scratch/projects/003-repo-review-and-catch-up/OVERVIEW.md` — what fleetman is,
  where it is now (command-by-command, with code cites), and a concept-vs-reality
  gap table.
- `.scratch/projects/003-repo-review-and-catch-up/PLAN.md` — the sequenced
  remaining-work plan. **Start at Step 0.**
- `.scratch/projects/002-fleet-write-ops/PLAN.md` — the write-ops design being
  reconciled.

## Your first task — Step 1 (`fleetman sync`)

**Ownership is RESOLVED (2026-07-01): fleetman is the SOLE owner of fleet-wide
sync/write; repoman's competing project 07 was retired.** There is nothing to
re-litigate — go straight to building the write-op surface (see PLAN.md Step 0,
now a resolved note, and Step 1).

Start at **PLAN.md Step 1 — `fleetman sync`**: build the sync surface. Add
`src/fleetman/manifest.py` (pydantic `RepoSpec` + `Manifest`, parse `repos.toml`)
and `src/fleetman/sync.py` (pure `plan_sync` classifying clone/fetch/up-to-date/
unmanaged, then `apply_sync` shelling **gitman**), wire `fleetman sync` into
`cli.py` (dry-run by default), and fold a non-fatal drift check into `doctor.py`.
See PLAN.md Step 1 for the full deliverables + acceptance criteria.

## Conventions (follow exactly)

- Run every in-repo command inside devenv: `devenv shell -- <cmd>` — tests, lint,
  builds. Never bare `python`/`pytest`/`uv`.
- Route all version control through **gitman** (jj + colocated git); never raw
  git/jj. Branch (a lane) off `main` before changing anything. Commit as you go.
  **Do not push without an explicit ask.**
- **Verify before you commit:** `devenv shell -- python -m pytest -q` must stay
  green (baseline: 6 passed). Do not bury a command's failure behind a pipe or
  `|| true` — surface every outcome.
- No AI-authorship trailers or "Generated with" lines in commits, PRs, or docs.
- Keep fleetman's design honest: it reads manifests only; `sync`/`flake-update`
  shell out to **gitman**/nix and stay dry-run by default, with any push gated.
