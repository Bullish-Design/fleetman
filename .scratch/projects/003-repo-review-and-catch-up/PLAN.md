# fleetman — Remaining-Work Plan (project 003)

> Sequenced plan for the outstanding fleetman work. The indexer half is **done and
> green** (see `OVERVIEW.md §2`); everything below is the **fleet-write half**.
> Ownership is settled: **fleetman is the SOLE owner of fleet-wide sync/write**
> (repoman project 07 retired 2026-07-01) — so the write-op surface below is
> fleetman's to build. Paths relative to
> `/home/andrew/Documents/Projects/fleetman/` unless noted.

Exit-code contract throughout: `0` ok · `1` domain decision · `2` infra/config ·
`3` invalid usage (`cli.py:7-9`).

---

## Step 0 — Ownership (RESOLVED — no longer a gate)

**fleetman is the SOLE owner of fleet-wide sync/write (resolved 2026-07-01).**
repoman's competing project 07 (`repoman fleet-sync`) was retired
(`/home/andrew/Documents/Projects/repoman/.scratch/projects/07-tower-repo-set-sync/SUPERSEDED.md`);
repoman stays strictly per-repo (`CONCEPT.md §2`). The dividing line: **fleetman =
across repos** (materialize + topo-publish the declared set); **repoman/gitman =
inside one repo** (per-repo VC + lifecycle). fleetman still has to *build* the
`sync` + `flake-update` surface below — only the ownership ambiguity is gone.

The two remaining pre-Phase-1 decisions (not blockers, just scoping):
1. **Which `repos.toml` is canonical** and where it lives (nix-meta is the leading
   candidate), and the schema (`[[repo]] name/url/ref|path`).
2. **The nix seam**: `nix-terminal.repoman.fleet.{enable,manifest,projectsDir}`
   currently names *repoman* — repoint it to fleetman or rename with a deprecation
   alias. nix-meta capability `fleetSync` likewise
   (`001.../CONCEPT.md:98-100`; `002.../PLAN.md:96-108`). Resolved in Step 3.

---

## Step 1 — `fleetman sync` (Phase 1: filesystem write, no push)

Clone/fetch the *declared* repo set into the workspace. Mutates the filesystem
only; never pushes, never enters a repo's devenv.

**Deliverables / files:**
- `src/fleetman/manifest.py` — pydantic `RepoSpec{name, url, ref?}` + `Manifest`;
  parse `repos.toml`. Source resolution order mirrors `_root()`:
  `--manifest` > `$FLEETMAN_MANIFEST` > `$REPOMAN_FLEET_MANIFEST` > a `repos.toml`
  discovered under the workspace root (per Step 0's canonical-home decision).
- `src/fleetman/sync.py` — pure `plan_sync(root, manifest) -> SyncPlan`
  (classify each spec *clone* / *fetch* / *up-to-date*; each on-disk dir
  *unmanaged*), then `apply_sync(plan, *, dry_run)` shelling **gitman** for the
  actual clone/fetch (global policy: never raw git/jj). Missing → clone at `ref`;
  present → fetch, **no checkout-force** (report dirty/diverged, never clobber).
- `src/fleetman/cli.py` — `fleetman sync [--manifest] [--dry-run] [--root]`,
  **dry-run/preview by default**; apply behind an explicit flag/confirmation.
- Fold a non-fatal drift check into `doctor.py` when a manifest exists
  (manifest-but-absent = to-clone; on-disk-but-unlisted = `unmanaged`, surfaced
  never deleted).
- `nix/fleetman.nix` — add a `fleetman:sync` task alongside the read-only ones.

**Acceptance:**
- `plan_sync` classifies clone/fetch/up-to-date/unmanaged correctly against a
  tmp-dir workspace + fake `repos.toml`, **with no network** (mock at the gitman
  boundary in `tests/`).
- `fleetman sync --dry-run` prints the plan and writes nothing; a fresh workspace
  clones the declared set; a re-run is a no-op fetch.
- Exit codes: 0 ok · 1 drift/dirty (decision) · 2 manifest/infra · 3 usage.

**Risks:** mutates the filesystem — keep dry-run the default and reversible-only
(clone/fetch, never destructive). Must not fight gitman's jj/colocated working
state → skip-dirty, never force.

---

## Step 2 — `fleetman flake-update` (Phase 2: topo-order publish, gated push)

The publish-time lock-bump. Walk the consumer DAG in topological order; per node:
`nix flake update` → nixbuild test → gitman commit. **The only operation that
touches remotes** — behind an explicit `--push`; never push without an ask.

**Deliverables / files:**
- `src/fleetman/models.py` — add `Fleet.topo_order()` (pure): build a DAG from the
  already-derived `nix_edges()` (optionally `python_edges()`), emit a stable topo
  order, detect/abort on cycles. (Currently `Fleet` has only `python_edges`,
  `nix_edges`, `edge_count` — `models.py:51-58`.)
- `src/fleetman/publish.py` — `run_flake_update(fleet, *, push, halt_on_fail=True)`
  iterating nodes: `nix flake update` at repo root (not via its devenv) → nixbuild
  test → gitman commit + optional push; first failure halts and reports partial
  progress.
- `src/fleetman/cli.py` — `fleetman flake-update [--push] [--only NAME...] [--root]`,
  **print-the-plan / dry-run by default**.
- `nix/fleetman.nix` — add a `fleetman:flake-update` task.

**Acceptance:**
- `Fleet.topo_order()` unit-tested for ordering + cycle detection (the high-value
  pure unit).
- The subprocess pipeline is integration-mocked at the nixbuild/gitman boundary;
  halt-on-fail asserted.
- Default invocation prints the plan and pushes nothing; `--push` gated and never
  the default.

**Risks:** highest-blast-radius, outward-facing (push). Keep gated + default-dry.
flake-update is unambiguously fleetman's (repoman 07 never claimed it, and is now
retired).

---

## Step 3 — Tower seam wiring (cross-repo; separate PRs)

Lives in *other* repos. Listed so the blast radius is explicit. Not done in
fleetman's tree except the local devenv tasks (covered in Steps 1-2).

**Deliverables / files:**
- **nix-meta**: `repos.toml` (E1) as the canonical manifest home; wire the
  `fleetSync` capability to **fleetman**.
- **nix-terminal**: decide the seam — keep `repoman.fleet.{enable,manifest,
  projectsDir}` shelling out to fleetman, or rename to a `fleetman.*` seam with a
  deprecation alias.
- **repoman**: fleet-write is fleetman's; project 07 is already retired
  (`repoman/.scratch/projects/07-tower-repo-set-sync/SUPERSEDED.md`). repoman keeps
  only its per-repo/gitman lane — no fleet code.

**Acceptance:** the chosen tool is reachable through the tower seam on both machines;
no two tools clone the same set; docs in nix-meta/nix-terminal name the owner.

**Risks:** cross-repo coordination; the seam currently *names repoman*, so a rename
has downstream consumers — prefer an alias.

---

## Suggested commit slicing
1. docs: this plan (ownership already resolved — fleetman sole owner; repoman 07
   retired in its own repo).
2. feat: `manifest.py` + `sync` (Step 1) — clone/fetch + drift, dry-run default.
3. feat: `topo_order` + `flake-update` (Step 2) — gated `--push`.
4. chore: devenv tasks for sync/flake-update (fleetman-local part of Step 3).
5. (cross-repo) nix-meta / nix-terminal wiring — tracked separately.

## Conventions (apply to every step)
- Run all in-repo commands inside devenv: `devenv shell -- <cmd>` (tests, lint).
- Route all VC through **gitman** (jj + colocated git); never raw git/jj. Branch
  (lane) first off `main`. Commit as you go. **Do not push without an explicit ask.**
- Verify before commit: `devenv shell -- python -m pytest -q` green (baseline: 6
  passed). No AI-authorship trailers in commits/PRs/docs.
