# fleetman — `sync` + `flake-update` Implementation Plan

> Successor work to `001-brainstorming/CONCEPT.md`. Resolves the CONCEPT's
> **headline open reconciliation**: fleetman becomes the full "repoman-fleet #14"
> by growing the two *write* operations (`sync`, `flake-update`) on top of the
> already-built read-only indexer. Phased so the fleet can stop at any boundary.

## Status of the baseline (Phase 0 — already done)
The indexer half is **built and validated** against the real workspace:
- `fleetman index --root ..` → 90 projects, 26 internal edges; writes
  `.agents/index/{registry.json,PROJECTS.md}`.
- Edges derive correctly from real manifests (`loci-core → knappy`,
  `nix-meta → nix-terminal → nixbuild/repoman`, `muse → browsee/citegeist/…`).
- `doctor` exit 0; `pytest` 6 passed.
- This embodies plan item **E2** (derive the DAG from manifests at runtime).

Nothing to build here — only record the decision in `CONCEPT.md` (§"Plan
reconciliation" / open question #1) once a direction is chosen.

## Recommended direction
**fleetman owns both write-ops, phased.** It is already the natural home (it owns
the workspace domain + the derived DAG that `flake-update` needs for topo order).
Phasing means each phase is independently shippable and the fleet can halt at
read-only, sync-only, or full publish without rework.

The reconciliation with the master plan:
- Keep **E2** (auto-discovery) as the *index* source of truth — what's on disk.
- Adopt **E1** (`repos.toml`, home = nix-meta) as the *sync* source of truth —
  what *should* be on disk. `sync` is the bridge between the declared set and the
  discovered set; `doctor` reports the drift between them.

---

## Phase 1 — `fleetman sync` (filesystem write, no remote push)

Clone/fetch the declared repo set into the workspace. Mutates the filesystem
only — never pushes, never enters a repo's devenv.

**New surface**
- `src/fleetman/manifest.py` — pydantic `RepoSpec{name, url, ref?}` + `Manifest`,
  parse `repos.toml`. Source resolution order:
  `--manifest` > `$FLEETMAN_MANIFEST` > `$REPOMAN_FLEET_MANIFEST` > a `repos.toml`
  discovered in nix-meta under the workspace root. (Reuses the existing
  `_root()` resolution style in `cli.py`.)
- `src/fleetman/sync.py` — `plan_sync(root, manifest) -> SyncPlan` (pure: classify
  each spec as *clone* / *fetch* / *up-to-date*, and each on-disk dir as
  *unmanaged*), then `apply_sync(plan, *, dry_run)` that shells **gitman** for the
  actual clone/fetch (per global policy: never raw git/jj).
- `cli.py` — `fleetman sync [--manifest PATH] [--dry-run] [--root]`. Default to a
  preview (`--dry-run` semantics shown, apply behind confirmation or an explicit
  flag — clone is filesystem-mutating but reversible).

**Behavior / reconciliation**
- Missing dir → clone at `ref`. Present dir → fetch (no checkout-force; report if
  the working copy is dirty/diverged rather than clobbering).
- Drift report: manifest-but-not-on-disk (to clone) and on-disk-but-not-in-
  manifest (`unmanaged` — surfaced, never deleted).
- Wire the drift check into `doctor` as a non-fatal check when a manifest exists.

**Tests** (extend `tests/`): tmp-dir workspace + fake `repos.toml`; assert
`plan_sync` classification (clone/fetch/unmanaged) without touching the network —
`apply_sync` is mocked at the gitman boundary.

**Exit codes:** 0 ok · 1 drift/domain-decision · 2 manifest/infra error · 3 usage.

---

## Phase 2 — `fleetman flake-update` (topo-order publish — pushes, gated)

The publish-time lock-bump (plan E4/E5). Walk the consumer DAG in topological
order; per node: `nix flake update` → nixbuild test → gitman commit. **This is
the only operation that touches remotes** and must stay behind an explicit
`--push` flag; never push without an ask.

**New surface**
- Topo order from the already-derived edges: reuse `Fleet.nix_edges()` (and
  optionally `python_edges()`) to build a DAG and emit a stable topological
  order; detect/abort on cycles. Add `Fleet.topo_order()` to `models.py` (pure,
  unit-testable).
- `src/fleetman/publish.py` — `run_flake_update(fleet, *, push, halt_on_fail=True)`
  iterating nodes: `nix flake update` (in each repo, but **not** via its devenv —
  invoked at repo root), `nixbuild test` (E4 per-node target), then gitman
  commit + optional push. Halt-on-fail (E5): first failure stops the walk and
  reports the partial progress.
- `cli.py` — `fleetman flake-update [--push] [--only NAME...] [--root]`,
  dry-run/print-the-plan by default.

**Tests:** topo-order correctness (incl. cycle detection) is the pure, high-value
unit; the subprocess pipeline is integration-mocked at the nixbuild/gitman
boundary with halt-on-fail asserted.

**Risk:** highest surface; outward-facing (push). Keep gated and default-dry.

---

## Phase 3 — Tower seam reconciliation (cross-repo; separate PRs)

These live in *other* repos and follow from the scope decision — list here so the
blast radius is explicit. Not done in fleetman's tree except the devenv tasks.

- **`nix/fleetman.nix`** (this repo): add `tasks."fleetman:sync"` and
  `tasks."fleetman:flake-update"` alongside the existing index/graph/doctor tasks.
- **nix-meta**: `repos.toml` (E1) is the manifest home; wire the `fleetSync`
  capability to `fleetman sync`.
- **nix-terminal**: decide the seam name — keep `repoman.fleet.{enable,manifest,
  projectsDir}` shelling out to fleetman, **or** rename to a `fleetman.*` seam.
  *Recommend* renaming for honesty (it's no longer repoman), with a deprecation
  alias. (Open sub-decision — CONCEPT open Q#3.)
- `00-INDEX §E` items E1/E4/E5 then map onto fleetman commands.

---

## Open sub-decisions (carry from CONCEPT, surface before coding each phase)
1. **Manifest home/format** — confirm `repos.toml` in nix-meta and its schema
   (`[[repo]] name/url/ref`?) before Phase 1 (CONCEPT Q#2).
2. **Seam rename** vs alias in nix-terminal (CONCEPT Q#3) — Phase 3.
3. **Add `loci-core` + nix-*/`*.nvim`** to the declared repo set (CONCEPT Q#4 /
   E3) — a `repos.toml` content question, not a fleetman code question.

## Suggested commit slicing
1. docs: this PLAN + CONCEPT decision note (no code).
2. feat: `manifest.py` + `sync` (Phase 1) — clone/fetch + drift, dry-run default.
3. feat: `topo_order` + `flake-update` (Phase 2) — gated `--push`.
4. chore: devenv tasks for sync/flake-update (Phase 3, fleetman-local part).
5. (cross-repo) nix-meta / nix-terminal wiring — tracked separately.
