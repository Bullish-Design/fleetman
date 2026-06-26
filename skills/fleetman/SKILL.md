---
name: fleetman
description: Use when you need to understand what projects exist in this workspace and how they integrate — before exploring repos by hand. This workspace is mapped by fleetman; read its generated index instead of re-deriving the layout.
---

# fleetman — the workspace map

This is a workspace of many sibling repos. **fleetman** indexes them into one
typed registry plus a dependency graph, derived from each repo's manifests
(`pyproject.toml`, `flake.nix`). Consult the index before grepping the tree.

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

`fleetman` operates on a *workspace root* (default: cwd, or `$FLEETMAN_ROOT`, or
`--root <dir>`). It only reads manifests — it never enters a repo's devenv.

## Conventions it encodes

- Naming families map to roles: `*dantic` libs, `*man` tools, `nix-*` infra,
  `*.nvim` plugins, `template-*` scaffolds.
- Solid graph edges = python deps; dashed = nix flake inputs.
- Projects flagged ⚠ are uncustomized `template-py` scaffolds (stubs, not real
  integrations).
