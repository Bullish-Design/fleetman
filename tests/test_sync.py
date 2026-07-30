"""Tests for the fleet manifest + sync planner/applier (network-free)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from fleetman import core
from fleetman.doctor import doctor_exit, run_doctor
from fleetman.manifest import ManifestError, Manifest, RepoSpec, load_manifest, resolve_manifest_path
from fleetman.sync import (
    RepoAction,
    RunOutcome,
    SyncItem,
    SyncPlan,
    apply_sync,
    plan_sync,
)


# --------------------------------------------------------------------------- manifest


def test_load_manifest_parses_repos(tmp_path: Path) -> None:
    p = tmp_path / "repos.toml"
    p.write_text('[[repo]]\nname="a"\nurl="https://h/a"\nref="main"\n'
                 '[[repo]]\nname="b"\nurl="https://h/b"\n')
    man = load_manifest(p)
    assert man.names() == {"a", "b"}
    assert man.repos[0].ref == "main" and man.repos[1].ref is None


def test_load_manifest_rejects_duplicates(tmp_path: Path) -> None:
    p = tmp_path / "repos.toml"
    p.write_text('[[repo]]\nname="a"\nurl="u"\n[[repo]]\nname="a"\nurl="v"\n')
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_load_manifest_rejects_bad_name(tmp_path: Path) -> None:
    p = tmp_path / "repos.toml"
    p.write_text('[[repo]]\nname="a/b"\nurl="u"\n')
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_resolve_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("")
    (tmp_path / "repos.toml").write_text("")  # discovery candidate
    monkeypatch.setenv("FLEETMAN_MANIFEST", str(tmp_path / "env.toml"))

    assert resolve_manifest_path(explicit, tmp_path) == explicit.resolve()  # flag wins
    assert resolve_manifest_path(None, tmp_path) == (tmp_path / "env.toml").resolve()  # env next
    monkeypatch.delenv("FLEETMAN_MANIFEST")
    assert resolve_manifest_path(None, tmp_path) == (tmp_path / "repos.toml").resolve()  # discovery


# --------------------------------------------------------------------------- plan_sync


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # "present" is a declared repo already on disk (a git checkout);
    # "stray" is an on-disk project nobody declared; "missing" is declared-only.
    (tmp_path / "present").mkdir()
    (tmp_path / "present" / ".git").mkdir()
    (tmp_path / "present" / "pyproject.toml").write_text('[project]\nname="present"\n')
    (tmp_path / "stray").mkdir()
    (tmp_path / "stray" / "pyproject.toml").write_text('[project]\nname="stray"\n')
    return tmp_path


def test_plan_classifies_clone_fetch_unmanaged(workspace: Path) -> None:
    man = Manifest(repos=[
        RepoSpec(name="present", url="https://h/present"),
        RepoSpec(name="missing", url="https://h/missing", ref="main"),
    ])
    plan = plan_sync(workspace, man, core.harvest(workspace))
    acts = {i.name: i.action for i in plan.items}
    assert acts["present"] is RepoAction.fetch
    assert acts["missing"] is RepoAction.clone
    assert acts["stray"] is RepoAction.unmanaged
    assert plan.has_drift()  # a clone + an unmanaged both count as drift


def test_plan_flags_present_non_git_dir(tmp_path: Path) -> None:
    (tmp_path / "here").mkdir()  # present but not a git checkout
    man = Manifest(repos=[RepoSpec(name="here", url="u")])
    plan = plan_sync(tmp_path, man, core.harvest(tmp_path))
    item = next(i for i in plan.items if i.name == "here")
    assert item.action is RepoAction.fetch and item.note == "present but not a git repo"


def test_plan_no_drift_when_all_present(workspace: Path) -> None:
    man = Manifest(repos=[
        RepoSpec(name="present", url="u"),
        RepoSpec(name="stray", url="u"),  # declaring stray removes the unmanaged flag
    ])
    plan = plan_sync(workspace, man, core.harvest(workspace))
    assert not plan.has_drift()


# --------------------------------------------------------------------------- apply_sync


class FakeRunner:
    """Records argv/cwd instead of executing; always succeeds."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, cmd: Sequence[str], cwd: Path | None = None) -> RunOutcome:
        self.calls.append((list(cmd), str(cwd) if cwd is not None else None))
        return RunOutcome(ok=True, output="")


def test_apply_dry_run_makes_no_calls() -> None:
    plan = SyncPlan(items=[SyncItem(name="x", action=RepoAction.clone, path="/tmp/x", url="u")])
    fr = FakeRunner()
    res = apply_sync(plan, dry_run=True, runner=fr)
    assert fr.calls == []
    assert res[0].detail == "would clone"


def test_apply_clone_bootstraps_git_then_gitman() -> None:
    plan = SyncPlan(items=[
        SyncItem(name="x", action=RepoAction.clone, path="/tmp/x", url="https://h/x", ref="main"),
    ])
    fr = FakeRunner()
    apply_sync(plan, dry_run=False, runner=fr)
    cmds = [c[0] for c in fr.calls]
    assert cmds[0][:2] == ["git", "clone"] and "--branch" in cmds[0] and "main" in cmds[0]
    assert cmds[1][:3] == ["gitman", "init", "--colocate"]


def test_apply_fetch_uses_gitman_pull() -> None:
    plan = SyncPlan(items=[SyncItem(name="p", action=RepoAction.fetch, path="/tmp/p", url="u")])
    fr = FakeRunner()
    apply_sync(plan, dry_run=False, runner=fr)
    assert fr.calls[0][0] == ["gitman", "pull"]
    assert fr.calls[0][1] == "/tmp/p"


def test_apply_never_touches_unmanaged() -> None:
    plan = SyncPlan(items=[SyncItem(name="s", action=RepoAction.unmanaged, path="/tmp/s")])
    fr = FakeRunner()
    res = apply_sync(plan, dry_run=False, runner=fr)
    assert fr.calls == [] and res == []


# --------------------------------------------------------------------------- doctor


def test_doctor_reports_drift_non_fatally(workspace: Path) -> None:
    (workspace / "repos.toml").write_text(
        '[[repo]]\nname="present"\nurl="u"\n[[repo]]\nname="notyet"\nurl="u"\n'
    )
    checks = run_doctor(workspace)
    drift = next(c for c in checks if c.name == "fleet manifest")
    assert drift.ok is True and "notyet" in drift.detail   # surfaced, still exit 0
    assert doctor_exit(checks) == 0


def test_doctor_flags_malformed_manifest(workspace: Path) -> None:
    (workspace / "repos.toml").write_text('[[repo]]\nname="a"\nname="a"\n')  # invalid TOML
    checks = run_doctor(workspace)
    assert doctor_exit(checks) == 2
