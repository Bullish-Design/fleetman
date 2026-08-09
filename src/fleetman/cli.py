"""fleetman CLI — the workspace/fleet manager in the `*man` family.

Where the other managers are per-repo, fleetman is per-*workspace*: it indexes a
directory of sibling repos and reports how they integrate. It derives everything
from manifests (`pyproject.toml`, `flake.nix`) — it never enters a repo's devenv.

Exit codes (shared 0/1/2/3 contract):
  0 ok · 1 domain-decision · 2 infra/config · 3 invalid usage
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Annotated

import typer

from fleetman import core
from fleetman import run as fleet_run
from fleetman.doctor import doctor_exit, run_doctor
from fleetman.init import run_init
from fleetman.manifest import ManifestError, load_manifest, resolve_manifest_path
from fleetman.render import projects_md, registry_json, write_index
from fleetman.sync import apply_sync, plan_sync

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
def sync(
    root: RootOpt = None,
    manifest: Annotated[
        Path | None,
        typer.Option("--manifest", help="repos.toml (default: $FLEETMAN_MANIFEST or discovered)."),
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Actually clone/fetch (default: dry-run, no writes).")
    ] = False,
) -> None:
    """Reconcile the declared repo set (repos.toml) with what's on disk.

    Clones missing repos, fetches present ones (via gitman), surfaces unmanaged
    dirs. Dry-run by default: prints the plan and exits 1 if there is drift.
    """
    base = _root(root)
    if not base.is_dir():
        typer.echo(f"fleetman: not a directory: {base}", err=True)
        raise typer.Exit(2)
    mpath = resolve_manifest_path(manifest, base)
    if mpath is None:
        typer.echo("fleetman: no repos.toml found (pass --manifest or set FLEETMAN_MANIFEST).", err=True)
        raise typer.Exit(2)
    try:
        man = load_manifest(mpath)
    except ManifestError as exc:
        typer.echo(f"fleetman: {exc}", err=True)
        raise typer.Exit(2)

    plan = plan_sync(base, man, core.harvest(base))
    marks = {"clone": "+", "fetch": "~", "unmanaged": "?"}
    for item in plan.items:
        extra = f"  ({item.note})" if item.note else ""
        typer.echo(f"  {marks.get(item.action.value, '·')} {item.action.value:10} {item.name}{extra}")
    if not plan.items:
        typer.echo("fleetman: manifest empty and workspace bare — nothing to do.")

    if not apply:
        typer.echo(f"fleetman: dry-run against {mpath}. re-run with --apply to clone/fetch.")
        raise typer.Exit(1 if plan.has_drift() else 0)

    results = apply_sync(plan, dry_run=False)
    for r in results:
        typer.echo(f"  [{'ok' if r.ok else 'XX'}] {r.action.value} {r.name}: {r.detail[:100]}")
    raise typer.Exit(2 if any(not r.ok for r in results) else 0)


#: Default subprocess runner for ``run``. Kept as a module-level name so tests
#: can inject a fake without exposing a public testing-only CLI option.
_RUNNER = fleet_run.subprocess_runner

#: Status label per RunStatus value, right-padded to width 7 in the status line.
_STATUS_LABELS = {
    "passed": "ok",
    "failed": "failed",
    "timed_out": "timeout",
    "spawn_error": "spawn",
}


@app.command()
def run(
    command: Annotated[list[str], typer.Argument(help="Command and arguments after --.")],
    root: RootOpt = None,
    all_projects: Annotated[bool, typer.Option("--all", help="Select every harvested project.")] = False,
    family: Annotated[list[str] | None, typer.Option("--family", help="Repeatable; OR within family.")] = None,
    kind: Annotated[list[str] | None, typer.Option("--kind", help="Repeatable; OR within kind.")] = None,
    layer: Annotated[list[str] | None, typer.Option("--layer", help="Repeatable; OR within layer.")] = None,
    if_path: Annotated[list[str] | None, typer.Option("--if", help="Repeatable; every path must exist in the project.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Repeatable; remove exact names after selection.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the plan; execute nothing.")] = False,
    halt_on_fail: Annotated[bool, typer.Option("--halt-on-fail", help="Stop scheduling after the first failed outcome.")] = False,
    timeout: Annotated[float | None, typer.Option("--timeout", help="Positive per-project wall-clock timeout in seconds.")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print only the final summary.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Show complete captured output for every project.")] = False,
) -> None:
    """Execute a command across explicitly selected projects.

    Scope is explicit: pass --all or at least one of --family/--kind/--layer/--if.
    Put -- before the command so its arguments are not parsed as fleetman options.
    """
    base = _root(root)
    if not base.is_dir():
        typer.echo(f"fleetman: not a directory: {base}", err=True)
        raise typer.Exit(2)
    if quiet and verbose:
        typer.echo("fleetman: --quiet and --verbose are mutually exclusive.", err=True)
        raise typer.Exit(3)
    try:
        timeout_sec = fleet_run.validate_timeout(timeout)
    except fleet_run.RunValidationError as exc:
        typer.echo(f"fleetman: {exc}", err=True)
        raise typer.Exit(3)

    fleet = core.harvest(base)
    try:
        plan = fleet_run.build_run_plan(
            fleet,
            command,
            all_projects=all_projects,
            families=family or [],
            kinds=kind or [],
            layers=layer or [],
            if_paths=if_path or [],
            exclude=exclude or [],
        )
    except fleet_run.RunValidationError as exc:
        typer.echo(f"fleetman: {exc}", err=True)
        raise typer.Exit(3)
    if not plan.projects:
        typer.echo("fleetman: no projects matched.", err=True)
        raise typer.Exit(1)
    if dry_run:
        _render_dry_run(plan, quiet=quiet)
        return

    full_output: dict[str, bytes] = {}

    def _collect(name: str, data: bytes) -> None:
        full_output[name] = data

    try:
        report = fleet_run.execute_run_plan(
            plan,
            halt_on_fail=halt_on_fail,
            timeout_sec=timeout_sec,
            runner=_RUNNER,
            on_output=_collect,
        )
    except KeyboardInterrupt:
        typer.echo("fleetman: interrupted; active command terminated.", err=True)
        raise typer.Exit(130)
    if not report.results:
        # Impossible for a non-empty plan unless interrupted before scheduling.
        typer.echo("fleetman: internal error: no results produced.", err=True)
        raise typer.Exit(2)
    _render_run(report, quiet=quiet, verbose=verbose, full_output=full_output)
    raise typer.Exit(0 if not report.failed else 2)


def _render_dry_run(plan: fleet_run.RunPlan, *, quiet: bool) -> None:
    """Render the plan with zero subprocess calls. Quiet still shows the summary."""
    typer.echo(f"fleetman: dry-run matched {len(plan.projects)} projects; no commands executed")
    if not quiet:
        typer.echo(f"  command: {shlex.join(plan.command)}")
        typer.echo("  (argv is executed directly; no shell is used)")
        for pp in plan.projects:
            typer.echo(f"    {pp.name}  {pp.path}")


def _status_line(result: fleet_run.RunResult) -> str:
    label = _STATUS_LABELS[result.status.value]
    detail = ""
    if result.status is fleet_run.RunStatus.failed:
        detail = f"  exit {result.exit_code}"
    elif result.status is fleet_run.RunStatus.spawn_error and result.error:
        detail = f"  {result.error}"
    return f"[{label:<7}] {result.name}  {result.duration_sec:.1f}s{detail}"


def _render_run(
    report: fleet_run.RunReport,
    *,
    quiet: bool,
    verbose: bool,
    full_output: dict[str, bytes],
) -> None:
    """Render execution results; presentation derives entirely from the report."""
    for result in report.results:
        if quiet:
            continue
        typer.echo(_status_line(result))
        raw = full_output.get(result.name, b"")
        if verbose and raw:
            typer.echo("  --- full output ---")
            for line in raw.decode("utf-8", errors="replace").splitlines():
                typer.echo(f"  {line}")
            typer.echo("  --- end ---")
        elif result.status is not fleet_run.RunStatus.passed and result.output_tail:
            typer.echo(result.output_tail)
    _render_summary(report)


def _render_summary(report: fleet_run.RunReport) -> None:
    """Deterministic final summary for every mode, including quiet."""
    if report.halted:
        typer.echo(
            f"fleetman: matched {report.matched_count}; executed {report.executed_count} — "
            f"{len(report.passed)} passed, {len(report.failed)} failed, "
            f"{report.not_run_count} not run (halted)"
        )
        return
    line = (
        f"fleetman: matched {report.matched_count}; executed {report.executed_count} — "
        f"{len(report.passed)} passed, {len(report.failed)} failed"
    )
    if report.failed:
        names = ", ".join(r.name for r in report.failed)
        if len(names) > 120:
            names = names[:117] + "…"
        line += f" ({names})"
    typer.echo(line)


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
