"""fleetman CLI — the workspace/fleet manager in the `*man` family.

Where the other managers are per-repo, fleetman is per-*workspace*: it indexes a
directory of sibling repos and reports how they integrate. It derives everything
from manifests (`pyproject.toml`, `flake.nix`) — it never enters a repo's devenv.

Exit codes (shared 0/1/2/3 contract):
  0 ok · 1 domain-decision · 2 infra/config · 3 invalid usage
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from fleetman import core
from fleetman.doctor import doctor_exit, run_doctor
from fleetman.init import run_init
from fleetman.render import projects_md, registry_json, write_index

app = typer.Typer(
    name="fleetman",
    help="Workspace manager: index sibling repos and how they integrate.",
    no_args_is_help=True,
    add_completion=False,
)

_INDEX_SUBDIR = ".agents/index"


def _root(explicit: Path | None) -> Path:
    """Resolve the workspace root: --root > $FLEETMAN_ROOT > cwd."""
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("FLEETMAN_ROOT")
    return Path(env).resolve() if env else Path.cwd().resolve()


RootOpt = Annotated[
    Path | None,
    typer.Option("--root", "-C", help="Workspace root (default: $FLEETMAN_ROOT or cwd)."),
]


@app.command()
def index(
    root: RootOpt = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help=f"Output dir (default: <root>/{_INDEX_SUBDIR})."),
    ] = None,
) -> None:
    """Harvest the workspace and write registry.json + PROJECTS.md."""
    base = _root(root)
    if not base.is_dir():
        typer.echo(f"fleetman: not a directory: {base}", err=True)
        raise typer.Exit(2)
    fleet = core.harvest(base)
    out_dir = out.resolve() if out else base / _INDEX_SUBDIR
    written = write_index(fleet, out_dir)
    typer.echo(f"fleetman: indexed {len(fleet.projects)} projects, "
               f"{fleet.edge_count()} internal edges.")
    for p in written:
        typer.echo(f"  wrote {p}")


@app.command()
def graph(root: RootOpt = None) -> None:
    """Print the Mermaid dependency graph to stdout (no files written)."""
    fleet = core.harvest(_root(root))
    md = projects_md(fleet)
    start = md.find("```mermaid")
    typer.echo(md[start:] if start != -1 else "_No internal edges detected._")


@app.command("list")
def list_projects(
    root: RootOpt = None,
    family: Annotated[str | None, typer.Option("--family", help="Filter by family.")] = None,
) -> None:
    """List projects (status-like read), optionally filtered by family."""
    fleet = core.harvest(_root(root))
    rows = [p for p in fleet.projects if family in (None, p.family)]
    if not rows:
        typer.echo("fleetman: no projects matched.", err=True)
        raise typer.Exit(1)
    for p in sorted(rows, key=lambda p: (p.family, p.name.lower())):
        flag = " ⚠stub" if p.stub else ""
        typer.echo(f"{p.name:32} {p.family:9} {p.layer:9} ←{len(p.dependents)}{flag}")


@app.command()
def query(
    name: Annotated[str, typer.Argument(help="Project name to inspect.")],
    root: RootOpt = None,
) -> None:
    """Show one project's purpose, dependencies, and dependents."""
    fleet = core.harvest(_root(root))
    p = fleet.by_name().get(name)
    if p is None:
        typer.echo(f"fleetman: no such project: {name}", err=True)
        raise typer.Exit(3)
    deps = p.depends_on + [f"{d} (nix)" for d in p.flake_inputs]
    typer.echo(f"{p.name}  [{p.family}/{p.layer}]  {p.pkg}")
    typer.echo(f"  purpose:    {p.purpose or '—'}")
    typer.echo(f"  depends on: {', '.join(deps) or '—'}")
    typer.echo(f"  dependents: {', '.join(p.dependents) or '—'}")


@app.command()
def doctor(root: RootOpt = None) -> None:
    """Validate that the workspace root is indexable."""
    base = _root(root)
    checks = run_doctor(base)
    for c in checks:
        typer.echo(f"[{'ok' if c.ok else 'XX'}] {c.name}: {c.detail}")
    raise typer.Exit(doctor_exit(checks))


@app.command()
def init(
    root: RootOpt = None,
    skills_dir: Annotated[str, typer.Option("--skills-dir")] = ".claude/skills",
) -> None:
    """Install the fleetman agent skill into the workspace root."""
    dest = run_init(_root(root), skills_dir)
    typer.echo(f"fleetman: wrote skill → {dest}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
