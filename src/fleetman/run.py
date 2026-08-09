"""``fleetman run``: pure selection/planning and serial orchestration.

This module owns:

- selector validation and the allowed-value constants;
- safe marker-path validation (``--if`` cannot escape a project);
- project selection and deterministic ordering;
- the typed plan/result/report models;
- serial execution through an injectable runner, with halt-on-fail bookkeeping.

It never prints and never raises ``typer.Exit`` — the CLI translates the domain
exceptions (:class:`RunValidationError`) and renders the report.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path

from fleetman.models import Fleet, FleetModel
from fleetman.runner import CommandOutcome, CommandRequest, Runner, subprocess_runner

FAMILIES = frozenset({"template", "dantic", "man", "nix", "nvim", "other"})
KINDS = frozenset({"python", "nix", "nvim", "other"})
LAYERS = frozenset({"scaffold", "lib", "tool", "infra", "plugin", "app"})


class RunValidationError(ValueError):
    """Semantic validation failure: scope, filter values, timeout, unsafe paths."""


class PlannedProject(FleetModel):
    name: str
    path: str


class RunPlan(FleetModel):
    root: str
    command: list[str]
    projects: list[PlannedProject] = []


class RunStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    timed_out = "timed_out"
    spawn_error = "spawn_error"


class RunResult(FleetModel):
    name: str
    path: str
    command: list[str]
    status: RunStatus
    exit_code: int | None
    duration_sec: float
    output_tail: str = ""
    output_truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.passed


class RunReport(FleetModel):
    plan: RunPlan
    results: list[RunResult] = []
    halted: bool = False

    @property
    def matched_count(self) -> int:
        return len(self.plan.projects)

    @property
    def executed_count(self) -> int:
        return len(self.results)

    @property
    def not_run_count(self) -> int:
        return max(0, self.matched_count - self.executed_count)

    @property
    def passed(self) -> list[RunResult]:
        return [r for r in self.results if r.status is RunStatus.passed]

    @property
    def failed(self) -> list[RunResult]:
        return [r for r in self.results if r.status is not RunStatus.passed]


def validate_timeout(value: float | None) -> float | None:
    """Semantic check for ``--timeout``: finite and positive. ``None`` passes."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunValidationError(f"timeout must be a positive number, got {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise RunValidationError(f"timeout must be a finite positive number, got {value!r}")
    return float(value)


def _check_values(dimension: str, values: Sequence[str], allowed: frozenset[str]) -> None:
    bad = sorted(v for v in values if v not in allowed)
    if bad:
        quoted = ", ".join(repr(v) for v in bad)
        raise RunValidationError(
            f"unknown {dimension} value(s): {quoted}; allowed: {', '.join(sorted(allowed))}"
        )


def _validate_marker_values(if_paths: Sequence[str]) -> list[str]:
    """Validate the ``--if`` *values* (independent of any project)."""
    markers: list[str] = []
    for value in if_paths:
        if not value:
            raise RunValidationError("--if marker path must not be empty")
        candidate = Path(value)
        if candidate.is_absolute():
            raise RunValidationError(f"--if marker path must be relative: {value!r}")
        if ".." in candidate.parts:
            raise RunValidationError(
                f"--if marker path must not traverse parents: {value!r}"
            )
        markers.append(value)
    return markers


def _validated_project_paths(fleet: Fleet) -> dict[str, Path]:
    """Resolve every harvested project to a validated operational path.

    Rejects symlinked project directories and any path that resolves outside the
    resolved workspace root. Dry-run and execution share these exact paths;
    execution must never reconstruct paths from names later.
    """
    root = Path(fleet.root).resolve()
    paths: dict[str, Path] = {}
    for p in fleet.projects:
        lexical = Path(fleet.root) / p.name
        if lexical.is_symlink():
            raise RunValidationError(f"project directory is a symlink: {p.name}")
        if not lexical.is_dir():
            raise RunValidationError(f"project directory not found: {p.name}")
        resolved = lexical.resolve()
        if not resolved.is_relative_to(root):
            raise RunValidationError(
                f"project directory escapes the workspace root: {p.name}"
            )
        paths[p.name] = resolved
    return paths


def _marker_matches(project_dir: Path, marker: str) -> bool:
    """True when ``marker`` resolves inside ``project_dir`` and exists.

    A symlink inside the project that resolves *outside* it fails the predicate
    even though its lexical path is relative. Existence is tested only after the
    escape check.
    """
    candidate = (project_dir / marker).resolve(strict=False)
    return candidate.is_relative_to(project_dir.resolve()) and candidate.exists()


def build_run_plan(
    fleet: Fleet,
    command: Sequence[str],
    *,
    all_projects: bool,
    families: Sequence[str] = (),
    kinds: Sequence[str] = (),
    layers: Sequence[str] = (),
    if_paths: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> RunPlan:
    """Pure selection: validate scope/paths, filter, order, and return a typed plan.

    A valid no-match returns an empty project list (the CLI exits 1); it is not
    an error here.
    """
    argv = list(command)
    if not argv:
        raise RunValidationError("command must not be empty")
    fams, kinds_, lays = list(families), list(kinds), list(layers)
    ifs = _validate_marker_values(if_paths)
    exs = set(exclude)

    positive = fams or kinds_ or lays or ifs
    if all_projects and positive:
        raise RunValidationError(
            "--all cannot be combined with a positive selector "
            "(--family/--kind/--layer/--if)"
        )
    if not all_projects and not positive:
        raise RunValidationError(
            "no scope: pass --all or at least one of --family/--kind/--layer/--if"
        )

    _check_values("family", fams, FAMILIES)
    _check_values("kind", kinds_, KINDS)
    _check_values("layer", lays, LAYERS)

    paths = _validated_project_paths(fleet)
    root = Path(fleet.root).resolve()

    selected = [
        p
        for p in fleet.projects
        if (not fams or p.family in fams)
        and (not kinds_ or p.kind in kinds_)
        and (not lays or p.layer in lays)
        and (not ifs or all(_marker_matches(paths[p.name], m) for m in ifs))
        and p.name not in exs
    ]
    selected.sort(key=lambda p: (p.name.lower(), p.name))
    projects = [PlannedProject(name=p.name, path=str(paths[p.name])) for p in selected]
    return RunPlan(root=str(root), command=argv, projects=projects)


def _map_status(outcome: CommandOutcome) -> RunStatus:
    """Deterministic outcome→status mapping with loud failure on impossible states."""
    if outcome.error is not None:
        return RunStatus.spawn_error
    if outcome.timed_out:
        return RunStatus.timed_out
    if outcome.exit_code == 0:
        return RunStatus.passed
    if outcome.exit_code is None:
        raise RuntimeError(
            "runner returned an impossible outcome: no error, no timeout, "
            "and exit_code=None"
        )
    return RunStatus.failed


def _sink_for(name: str, on_output: Callable[[str, bytes], None]) -> Callable[[bytes], None]:
    def sink(data: bytes) -> None:
        on_output(name, data)

    return sink


def execute_run_plan(
    plan: RunPlan,
    *,
    halt_on_fail: bool = False,
    timeout_sec: float | None = None,
    runner: Runner = subprocess_runner,
    clock: Callable[[], float] = time.monotonic,
    on_output: Callable[[str, bytes], None] | None = None,
) -> RunReport:
    """Execute every project in plan order through ``runner``.

    ``on_output`` receives ``(project name, full combined output bytes)`` for
    each executed project — the CLI uses it for verbose output blocks.

    ``KeyboardInterrupt`` is deliberately not caught here: the runner terminates
    its process group and re-raises, and the CLI maps it to exit 130.
    """
    results: list[RunResult] = []
    halted = False
    for pp in plan.projects:
        started = clock()
        sink = _sink_for(pp.name, on_output) if on_output is not None else None
        outcome = runner(
            CommandRequest(argv=plan.command, cwd=pp.path, timeout_sec=timeout_sec),
            on_output=sink,
        )
        duration = max(0.0, clock() - started)
        status = _map_status(outcome)
        results.append(
            RunResult(
                name=pp.name,
                path=pp.path,
                command=list(plan.command),
                status=status,
                exit_code=outcome.exit_code,
                duration_sec=duration,
                output_tail=outcome.output_tail,
                output_truncated=outcome.output_truncated,
                error=outcome.error,
            )
        )
        if halt_on_fail and status is not RunStatus.passed:
            halted = True
            break
    return RunReport(plan=plan, results=results, halted=halted)
