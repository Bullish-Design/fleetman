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
fleetman sync             # dry-run reconcile of declared repos (repos.toml)
fleetman run              # execute one command across explicitly selected projects
fleetman init             # install the agent skill into the workspace
```

The workspace root defaults to the current directory; override with `--root <dir>`
or `$FLEETMAN_ROOT`.

## Run a command across projects

`fleetman run` executes one external command in every *explicitly selected*
project from a fresh harvest, one at a time, reporting per-project status:

```
fleetman run --if .copier-answers.yml --dry-run -- copyroom update
fleetman run --if .copier-answers.yml -- copyroom update
fleetman run --kind python -- pytest -q
fleetman run --all -- git status --short
```

- **Scope is explicit:** pass `--all` or at least one of `--family`, `--kind`,
  `--layer`, `--if`. `--exclude` removes exact names after selection.
- **`--` separates fleetman options from the command argv.** Anything after it
  is passed to the command verbatim; command flags that look like fleetman
  options (e.g. `pytest -q`) need it.
- **No shell.** The argv is executed directly (`shell=False`); pipelines,
  redirections, and `FOO=bar` assignments are not interpreted. Invoke a shell
  explicitly when you want them: `fleetman run --all -- sh -c '…'`.
- **No devenv activation.** Commands use the invoking environment, not each
  project's devenv.
- **`--dry-run` previews selection only** — it prints the matched projects and
  argv and executes nothing; it cannot predict command effects.
- `--halt-on-fail` stops after the first failed outcome; `--timeout SECONDS`
  bounds each project and terminates the process group on expiry.

Exit codes: `0` all passed (or non-empty dry-run plan) · `1` no match · `2`
command failure/timeout, invalid root, or parser-level usage · `3` semantic
validation (bad scope combination, filter value, timeout, or unsafe `--if`
path) · `130` interrupted.

## Outputs

- **`.agents/index/PROJECTS.md`** — per-family tables + Mermaid graph (solid edge =
  python dep, dashed = nix input).
- **`.agents/index/registry.json`** — structured source of truth for agents.

## Conventions encoded

Naming families map to roles: `*dantic` libs · `*man` tools · `nix-*` infra ·
`*.nvim` plugins · `template-*` scaffolds. Uncustomized `template-py` clones are
flagged as stubs.
