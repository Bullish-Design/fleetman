"""Tests for ``fleetman.run``: pure selection/planning and serial orchestration.

Selection tests use a synthetic :class:`Fleet` plus real temporary project
directories (containment validation needs real paths). Orchestration tests use
an injected fake runner for exact scheduling behavior.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fleetman.models import Fleet, Project
from fleetman.run import (
    RunPlan,
    RunStatus,
    PlannedProject,
    RunValidationError,
    build_run_plan,
    execute_run_plan,
    validate_timeout,
)
from fleetman.runner import CommandOutcome, CommandRequest


def _proj(name: str, **kw) -> Project:
    defaults = {"pkg": name, "kind": "other", "family": "other", "layer": "app"}
    defaults.update(kw)
    return Project(name=name, **defaults)


def _make_fleet(root: Path, *projects: Project) -> Fleet:
    for p in projects:
        (root / p.name).mkdir(parents=True, exist_ok=True)
    return Fleet(root=str(root), projects=list(projects))


# ------------------------------------------------------------------- scope


def test_all_includes_every_ordinary_project(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"), _proj("beta"), _proj("gamma"))
    plan = build_run_plan(fleet, ["ls"], all_projects=True)
    assert [p.name for p in plan.projects] == ["alpha", "beta", "gamma"]
    assert plan.root == str(tmp_path.resolve())
    assert plan.command == ["ls"]


def test_no_scope_rejected(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError):
        build_run_plan(fleet, ["ls"], all_projects=False)


def test_all_plus_selector_rejected(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha", family="man"))
    with pytest.raises(RunValidationError):
        build_run_plan(fleet, ["ls"], all_projects=True, families=["man"])


def test_exclusion_alone_rejected(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError):
        build_run_plan(fleet, ["ls"], all_projects=False, exclude=["alpha"])


def test_empty_command_rejected(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError):
        build_run_plan(fleet, [], all_projects=True)


# -------------------------------------------------------------- filter semantics


def test_family_values_are_or(tmp_path: Path) -> None:
    fleet = _make_fleet(
        tmp_path,
        _proj("a", family="man"),
        _proj("b", family="dantic"),
        _proj("c", family="nix"),
    )
    plan = build_run_plan(fleet, ["ls"], all_projects=False, families=["man", "dantic"])
    assert [p.name for p in plan.projects] == ["a", "b"]


def test_dimensions_are_and(tmp_path: Path) -> None:
    fleet = _make_fleet(
        tmp_path,
        _proj("py-man", family="man", kind="python"),
        _proj("man-only", family="man", kind="other"),
        _proj("py-other", family="other", kind="python"),
    )
    plan = build_run_plan(fleet, ["ls"], all_projects=False, families=["man"], kinds=["python"])
    assert [p.name for p in plan.projects] == ["py-man"]


def test_kind_and_layer_cross_dimension(tmp_path: Path) -> None:
    fleet = _make_fleet(
        tmp_path,
        _proj("a", kind="python", layer="lib"),
        _proj("b", kind="nix", layer="lib"),
        _proj("c", kind="nix", layer="app"),
    )
    plan = build_run_plan(fleet, ["ls"], all_projects=False, kinds=["python", "nix"], layers=["lib"])
    assert [p.name for p in plan.projects] == ["a", "b"]


def test_repeated_markers_are_and(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x").write_text("")
    (tmp_path / "a" / "y").write_text("")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x").write_text("")
    fleet = _make_fleet(tmp_path, _proj("a"), _proj("b"))
    plan = build_run_plan(fleet, ["ls"], all_projects=False, if_paths=["x", "y"])
    assert [p.name for p in plan.projects] == ["a"]


def test_marker_matches_file_and_dir(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_text("")
    (tmp_path / "a" / "d").mkdir()  # directories also match
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "f").write_text("")
    fleet = _make_fleet(tmp_path, _proj("a"), _proj("b"))
    # every marker must exist in the project: only "a" has both f and d
    plan = build_run_plan(fleet, ["ls"], all_projects=False, if_paths=["f", "d"])
    assert [p.name for p in plan.projects] == ["a"]
    # a single marker matches every project that has it
    plan2 = build_run_plan(fleet, ["ls"], all_projects=False, if_paths=["f"])
    assert [p.name for p in plan2.projects] == ["a", "b"]


def test_marker_symlink_inside_project_matches(tmp_path: Path) -> None:
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "real").mkdir()
    os.symlink(str(tmp_path / "c" / "real"), tmp_path / "c" / "link")
    fleet = _make_fleet(tmp_path, _proj("c"))
    plan = build_run_plan(fleet, ["ls"], all_projects=False, if_paths=["link"])
    assert [p.name for p in plan.projects] == ["c"]


def test_marker_symlink_escaping_project_is_excluded(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "c").mkdir()
    os.symlink(str(outside), tmp_path / "c" / "link")
    fleet = _make_fleet(tmp_path, _proj("c"))
    plan = build_run_plan(fleet, ["ls"], all_projects=False, if_paths=["link"])
    assert plan.projects == []  # lexically relative, but resolves outside the project


@pytest.mark.parametrize("bad", ["", "../x", "/abs/x", "a/../../x", ".//..//x"])
def test_unsafe_marker_value_rejected(tmp_path: Path, bad: str) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError):
        build_run_plan(fleet, ["ls"], all_projects=False, if_paths=[bad])


def test_exclude_applies_last_and_is_case_sensitive(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("Alpha"), _proj("alpha"), _proj("beta"))
    plan = build_run_plan(fleet, ["ls"], all_projects=True, exclude=["Alpha", "missing"])
    assert [p.name for p in plan.projects] == ["alpha", "beta"]


@pytest.mark.parametrize(
    "kw",
    [
        {"families": ["bogus"]},
        {"kinds": ["bogus"]},
        {"layers": ["bogus"]},
    ],
)
def test_unknown_filter_values_rejected(tmp_path: Path, kw: dict) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError) as exc:
        build_run_plan(fleet, ["ls"], all_projects=False, **kw)
    assert "allowed" in str(exc.value)


def test_all_invalid_values_reported_together(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    with pytest.raises(RunValidationError) as exc:
        build_run_plan(fleet, ["ls"], all_projects=False, families=["zzz", "aaa"])
    msg = str(exc.value)
    assert "zzz" in msg and "aaa" in msg


# --------------------------------------------------------- containment & order


def test_symlinked_project_directory_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), tmp_path / "alpha")
    fleet = Fleet(root=str(tmp_path), projects=[_proj("alpha")])
    with pytest.raises(RunValidationError) as exc:
        build_run_plan(fleet, ["ls"], all_projects=True)
    assert "symlink" in str(exc.value)


def test_case_folded_deterministic_ordering(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("beta"), _proj("Alpha"), _proj("alpha"), _proj("Beta"))
    plan = build_run_plan(fleet, ["ls"], all_projects=True)
    assert [p.name for p in plan.projects] == ["Alpha", "alpha", "Beta", "beta"]


def test_valid_no_match_returns_empty_plan(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    plan = build_run_plan(fleet, ["ls"], all_projects=False, families=["man"])
    assert plan.projects == []
    assert plan.command == ["ls"]


def test_command_argv_preserved_exactly(tmp_path: Path) -> None:
    fleet = _make_fleet(tmp_path, _proj("alpha"))
    argv = ["pytest", "-q", "--maxfail=1"]
    plan = build_run_plan(fleet, argv, all_projects=True)
    assert plan.command == argv


# ------------------------------------------------------------------- timeout


def test_validate_timeout() -> None:
    assert validate_timeout(None) is None
    assert validate_timeout(3.5) == 3.5
    for bad in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(RunValidationError):
            validate_timeout(bad)


# -------------------------------------------------------------- orchestration


def _plan(tmp_path: Path, names: tuple[str, ...]) -> RunPlan:
    for n in names:
        (tmp_path / n).mkdir()
    return RunPlan(
        root=str(tmp_path),
        command=["pytest"],
        projects=[PlannedProject(name=n, path=str(tmp_path / n)) for n in names],
    )


def _outcome(status: str = "passed", exit_code: int | None = None) -> CommandOutcome:
    if status == "passed":
        return CommandOutcome(exit_code=0, output_tail="out")
    if status == "failed":
        return CommandOutcome(exit_code=exit_code if exit_code is not None else 1, output_tail="out")
    if status == "timed_out":
        return CommandOutcome(timed_out=True, output_tail="out")
    return CommandOutcome(error="boom")  # spawn_error


class FakeRunner:
    """Records requests; each call pops the next scripted outcome."""

    def __init__(self, *outcomes: CommandOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[CommandRequest] = []

    def __call__(self, request: CommandRequest, *, on_output=None) -> CommandOutcome:
        self.calls.append(request)
        return self.outcomes.pop(0)


def test_execution_passes_exact_argv_and_cwd(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b"))
    fr = FakeRunner(_outcome(), _outcome())
    report = execute_run_plan(plan, runner=fr)
    assert [c.argv for c in fr.calls] == [["pytest"], ["pytest"]]
    assert [c.cwd for c in fr.calls] == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert [c.timeout_sec for c in fr.calls] == [None, None]
    assert len(report.results) == 2


def test_success_continues_and_passes(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b", "c"))
    fr = FakeRunner(_outcome(), _outcome(), _outcome())
    report = execute_run_plan(plan, runner=fr)
    assert [r.name for r in report.results] == ["a", "b", "c"]
    assert all(r.status is RunStatus.passed for r in report.results)
    assert report.halted is False
    assert report.matched_count == 3 and report.executed_count == 3
    assert report.not_run_count == 0 and report.failed == []


def test_ordinary_failure_continues_by_default(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b"))
    fr = FakeRunner(_outcome("failed", 3), _outcome())
    report = execute_run_plan(plan, runner=fr)
    assert [r.status for r in report.results] == [RunStatus.failed, RunStatus.passed]
    assert [r.exit_code for r in report.results] == [3, 0]
    assert report.halted is False


@pytest.mark.parametrize("status", ["failed", "timed_out", "spawn_error"])
def test_non_passed_continues_by_default(tmp_path: Path, status: str) -> None:
    plan = _plan(tmp_path, ("a", "b"))
    fr = FakeRunner(_outcome(status), _outcome())
    report = execute_run_plan(plan, runner=fr)
    assert report.executed_count == 2
    assert report.results[0].status.value == status
    assert report.results[1].status is RunStatus.passed


@pytest.mark.parametrize("status", ["failed", "timed_out", "spawn_error"])
def test_halt_on_fail_stops_after_each_failure_category(tmp_path: Path, status: str) -> None:
    plan = _plan(tmp_path, ("a", "b", "c"))
    fr = FakeRunner(_outcome(status), _outcome(), _outcome())
    report = execute_run_plan(plan, halt_on_fail=True, runner=fr)
    assert report.executed_count == 1
    assert report.halted is True
    assert report.matched_count == 3
    assert report.not_run_count == 2
    assert report.failed == report.results
    assert len(fr.calls) == 1


def test_halt_on_fail_with_all_passes_runs_everything(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b"))
    fr = FakeRunner(_outcome(), _outcome())
    report = execute_run_plan(plan, halt_on_fail=True, runner=fr)
    assert report.halted is False and report.executed_count == 2


def test_durations_use_injected_clock(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b"))
    fr = FakeRunner(_outcome(), _outcome())
    ticks = iter([10.0, 11.5, 12.0, 13.0])
    report = execute_run_plan(plan, runner=fr, clock=lambda: next(ticks))
    assert [r.duration_sec for r in report.results] == [1.5, 1.0]


def test_result_order_equals_plan_order(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("zebra", "apple", "mango"))
    fr = FakeRunner(_outcome("failed"), _outcome(), _outcome("timed_out"))
    report = execute_run_plan(plan, runner=fr)
    assert [r.name for r in report.results] == ["zebra", "apple", "mango"]


def test_timeout_and_spawn_error_carry_state(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a",))
    report = execute_run_plan(plan, runner=FakeRunner(_outcome("timed_out")))
    r = report.results[0]
    assert r.status is RunStatus.timed_out and r.exit_code is None and r.ok is False
    report2 = execute_run_plan(plan, runner=FakeRunner(_outcome("spawn_error")))
    r2 = report2.results[0]
    assert r2.status is RunStatus.spawn_error and r2.exit_code is None
    assert r2.error == "boom"


def test_malformed_runner_outcome_fails_loudly(tmp_path: Path) -> None:
    class BadRunner:
        def __call__(self, request: CommandRequest, *, on_output=None) -> CommandOutcome:
            return CommandOutcome()  # no error, no timeout, exit_code None

    with pytest.raises(RuntimeError):
        execute_run_plan(_plan(tmp_path, ("a",)), runner=BadRunner())


def test_on_output_receives_per_project_bytes(tmp_path: Path) -> None:
    plan = _plan(tmp_path, ("a", "b"))

    class SinkRunner:
        def __call__(self, request: CommandRequest, *, on_output=None) -> CommandOutcome:
            assert on_output is not None
            on_output(b"full:" + request.cwd.encode())
            return CommandOutcome(exit_code=0)

    got: list[tuple[str, bytes]] = []
    execute_run_plan(plan, runner=SinkRunner(), on_output=lambda n, d: got.append((n, d)))
    assert got == [("a", b"full:" + str(tmp_path / "a").encode()),
                   ("b", b"full:" + str(tmp_path / "b").encode())]


def test_keyboard_interrupt_propagates_and_stops_scheduling(tmp_path: Path) -> None:
    class BumpyRunner:
        def __init__(self) -> None:
            self.calls: list[CommandRequest] = []

        def __call__(self, request: CommandRequest, *, on_output=None) -> CommandOutcome:
            self.calls.append(request)
            if len(self.calls) == 2:
                raise KeyboardInterrupt()
            return CommandOutcome(exit_code=0)

    runner = BumpyRunner()
    with pytest.raises(KeyboardInterrupt):
        execute_run_plan(_plan(tmp_path, ("a", "b", "c")), runner=runner)
    assert len(runner.calls) == 2  # interrupted mid-run, no later projects scheduled
