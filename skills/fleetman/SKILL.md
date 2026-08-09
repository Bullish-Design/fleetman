---
name: fleetman
description: Use when you need to understand what projects exist in this workspace and how they integrate — or execute one command across selected projects — before exploring repos by hand. This workspace is mapped by fleetman; read its generated index instead of re-deriving the layout.
---

# fleetman — the workspace map and fleet runner

This is a workspace of many sibling repos. **fleetman** indexes them into one
typed registry plus a dependency graph, derived from each repo's manifests
(`pyproject.toml`, `flake.nix`), and can execute one command across a selected
subset. Consult the index before grepping the tree, and dry-run any
fleet-wide operation before running it.

## First move

Read the generated index at the workspace root:

- **`.agents/index/PROJECTS.md`** — per-family tables (purpose, layer, deps) and a
  Mermaid dependency graph. Start here.
- **`.agents/index/registry.json`** — the same data, structured, for querying.

If those files look stale or absent, regenerate them:

```
fleetman index            # writes .agents/index/{registry.json,PROJECTS.md}
```

## Commands

- **Re-index the workspace:** `fleetman index`
- **Print just the dependency graph:** `fleetman graph`
- **List projects (optionally by family):** `fleetman list --family man`
- **Inspect one project's deps/dependents:** `fleetman query muse`
- **Check the workspace is indexable:** `fleetman doctor`
- **Dry-run reconcile declared repos:** `fleetman sync`
- **Execute one command across selected projects:** `fleetman run`

`fleetman` operates on a *workspace root* (default: cwd, or `$FLEETMAN_ROOT`, or
`--root <dir>`). It reads manifests and spawns commands — it never enters a
repo's devenv.

## Running a command across projects

`fleetman run` is the general fleet-execution primitive: pick projects
explicitly, preview with `--dry-run`, then run one argv per project in
deterministic order.

Agent workflow:

1. **Resolve the intended workspace root** (`--root <dir>` or `$FLEETMAN_ROOT`)
   so the operation targets the fleet you mean.
2. **Prefer a narrow selector** (`--family`, `--kind`, `--layer`, `--if`, plus
   `--exclude`) over `--all`. Mutating operations should not be whole-fleet by
   default.
3. **Dry-run mutating operations first** — `--dry-run` prints the exact argv and
   every resolved project path and executes nothing:
   ```
   fleetman run --if .copier-answers.yml --dry-run -- copyroom update
   ```
4. **Review every resolved project path** in the dry-run output before
   executing; `--if` markers cannot escape a project, but scope is still yours.
5. **Run the exact argv** with `--` so command flags are not eaten by fleetman:
   ```
   fleetman run --if .copier-answers.yml -- copyroom update
   ```
6. **Report per-project failures** from the status lines and final summary
   (`matched/executed/not-run`). Do not claim fleet-wide success from a partial
   result — exit code 2 means at least one project failed, and
   `--halt-on-fail` leaves remaining projects unrun.

Whole-fleet *reads* are fine with explicit `--all`:

```
fleetman run --all -- git status --short
```

Notes:

- Commands run **without a shell**; pipelines/redirections need an explicit
  `sh -c '…'`.
- Commands use the **invoking environment**, not each project's devenv.
- `--timeout SECONDS` bounds each project and terminates its process group.
- `--dry-run` predicts fleetman selection only — never command effects.

## Conventions it encodes

- Naming families map to roles: `*dantic` libs, `*man` tools, `nix-*` infra,
  `*.nvim` plugins, `template-*` scaffolds.
- Solid graph edges = python deps; dashed = nix flake inputs.
- Projects flagged ⚠ are uncustomized `template-py` scaffolds (stubs, not real
  integrations).
