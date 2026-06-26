# fleetman

The **workspace/fleet manager** in the `*man` family. Where the other managers
(gitman, testee, repoman, …) are *per-repo*, fleetman is *per-workspace*: it
indexes a directory of sibling repos and reports **what projects exist and how
they integrate**.

It derives everything from manifests — `pyproject.toml` (python deps) and
`flake.nix` (nix inputs) — so the map is generated, never hand-maintained. No
indexed project is imported or built; only its manifests are read.

## Why it's separate from repoman

`repoman` is, by design, a *per-repo* conductor — `repoman/CONCEPT.md` explicitly
puts fleet/workspace management out of scope. fleetman owns that workspace domain
instead, following the same family contract (Typer CLI, pydantic-normalized
report, devenv module, agent skill, `0/1/2/3` exit codes).

## Usage

```
fleetman index            # write .agents/index/{registry.json,PROJECTS.md}
fleetman graph            # print the Mermaid dependency graph
fleetman list --family man
fleetman query muse       # one project's purpose, deps, dependents
fleetman doctor           # is this workspace indexable?
fleetman init             # install the agent skill into the workspace
```

The workspace root defaults to the current directory; override with `--root <dir>`
or `$FLEETMAN_ROOT`.

## Outputs

- **`.agents/index/PROJECTS.md`** — per-family tables + Mermaid graph (solid edge =
  python dep, dashed = nix input).
- **`.agents/index/registry.json`** — structured source of truth for agents.

## Conventions encoded

Naming families map to roles: `*dantic` libs · `*man` tools · `nix-*` infra ·
`*.nvim` plugins · `template-*` scaffolds. Uncustomized `template-py` clones are
flagged as stubs.
