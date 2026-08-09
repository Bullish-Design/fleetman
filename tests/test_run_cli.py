"""CLI tests for ``fleetman run``, using ``typer.testing.CliRunner``.

All runs use temporary synthetic roots with real local subprocesses
(``sys.executable``) — never arbitrary tools against the live workspace.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fleetman import cli
from fleetman.runner import CommandOutcome, CommandRequest

runner = CliRunner()

_PASS = [sys.executable, "-c", "pass"]


def _root(tmp_path: Path, names: list[str]) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    for n in names:
        (root / n).mkdir()
    return root


def _cmd(root: Path, *args: str) -> list[str]:
    """Build a CLI argv with --root positioned before any ``--`` separator."""
    return ["run", "--root", str(root), *args]


# ---------------------------------------------------------------- parsing


def test_double_dash_preserves_option_looking_argv(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    # --verbose proves --version was passed through to python (normal mode hides
    # passed-project output).
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--verbose", "--"), sys.executable, "--version"])
    assert result.exit_code == 0
    assert "Python" in result.output


def test_option_looking_argv_requires_double_dash(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, ["run", "--root", str(root), "--all", sys.executable, "--version"])
    # Without ``--``, --version is parsed as a fleetman option -> parser error.
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_command_without_dash_plain_tokens_follows_typer(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    # Plain tokens need no ``--``; python with DEVNULL stdin reads EOF and exits 0.
    result = runner.invoke(cli.app, ["run", "--root", str(root), "--all", sys.executable])
    assert result.exit_code == 0


def test_missing_command_exits_2(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, ["run", "--root", str(root), "--all"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output


# ------------------------------------------------------------ semantic errors


def test_missing_positive_scope_exits_3(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--"), *_PASS])
    assert result.exit_code == 3
    assert "no scope" in result.output


def test_all_plus_selector_exits_3(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--family", "man", "--"), *_PASS])
    assert result.exit_code == 3


def test_quiet_plus_verbose_exits_3(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--quiet", "--verbose", "--"), *_PASS])
    assert result.exit_code == 3


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
def test_invalid_timeout_exits_3(tmp_path: Path, value: str) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--timeout", value, "--"), *_PASS])
    assert result.exit_code == 3
    assert "timeout" in result.output


def test_unparseable_timeout_is_parser_error_2(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--timeout", "abc", "--"), *_PASS])
    assert result.exit_code == 2  # Typer/Click parser-level, not semantic


def test_invalid_filter_exits_3(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--family", "bogus", "--"), *_PASS])
    assert result.exit_code == 3
    assert "unknown family" in result.output


def test_unsafe_marker_path_exits_3(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    for marker in ("../x", "/abs/x", ""):
        result = runner.invoke(cli.app, [*_cmd(root, "--if", marker, "--"), *_PASS])
        assert result.exit_code == 3


def test_nonexistent_root_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["run", "--root", str(tmp_path / "nope"), "--all", "--", *_PASS])
    assert result.exit_code == 2
    assert "not a directory" in result.output


def test_no_match_exits_1(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])  # family "other", never "man"
    result = runner.invoke(cli.app, [*_cmd(root, "--family", "man", "--"), *_PASS])
    assert result.exit_code == 1
    assert "no projects matched" in result.output


# ------------------------------------------------------------------- dry-run


def test_dry_run_exits_0_runs_nothing_and_renders_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path, ["beta", "alpha"])
    calls: list[CommandRequest] = []

    def spy(request: CommandRequest, *, on_output=None) -> CommandOutcome:
        calls.append(request)
        raise AssertionError("dry-run must not execute")

    monkeypatch.setattr(cli, "_RUNNER", spy)
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--dry-run", "--"), *_PASS])
    assert result.exit_code == 0
    assert calls == []
    assert "dry-run matched 2 projects" in result.output
    assert "no commands executed" in result.output
    assert shlex.join(_PASS) in result.output
    assert "alpha" in result.output and "beta" in result.output  # deterministic order
    # plan renders resolved paths
    assert str(root / "alpha") in result.output


def test_dry_run_quiet_hides_project_list_but_shows_summary(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--dry-run", "--quiet", "--"), *_PASS])
    assert result.exit_code == 0
    assert "dry-run matched 1 projects" in result.output
    assert str(root / "alpha") not in result.output  # quiet suppresses project lines


# ---------------------------------------------------------------- execution


def test_success_exits_0(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--"), *_PASS])
    assert result.exit_code == 0
    assert "[ok" in result.output
    assert "matched 1; executed 1" in result.output
    assert "0 failed" in result.output


def test_command_failure_exits_2(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--"),
                                     sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.exit_code == 2
    assert "[failed" in result.output
    assert "exit 3" in result.output
    assert "1 failed" in result.output


def test_timeout_exits_2_and_marks_timeout(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--timeout", "0.2", "--"),
                                     sys.executable, "-c", "import time; time.sleep(30)"])
    assert result.exit_code == 2
    assert "[timeout]" in result.output


def test_halt_summary_gives_matched_executed_not_run(tmp_path: Path) -> None:
    root = _root(tmp_path, ["a", "b", "c"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--halt-on-fail", "--"),
                                     sys.executable, "-c", "import sys; sys.exit(1)"])
    assert result.exit_code == 2
    assert "matched 3; executed 1" in result.output
    assert "0 passed, 1 failed, 2 not run (halted)" in result.output


# --------------------------------------------------------------- output modes


def test_normal_mode_hides_passed_output(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--"),
                                     sys.executable, "-c", "print('SECRET-OUTPUT')"])
    assert result.exit_code == 0
    assert "[ok" in result.output
    assert "SECRET-OUTPUT" not in result.output


def test_normal_mode_shows_failed_output_tail(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--"),
                                     sys.executable, "-c", "print('FAIL-TAIL'); import sys; sys.exit(1)"])
    assert result.exit_code == 2
    assert "[failed" in result.output
    assert "FAIL-TAIL" in result.output


def test_verbose_mode_shows_complete_output_block(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--verbose", "--"),
                                     sys.executable, "-c", "print('FULL-VERBOSE')"])
    assert result.exit_code == 0
    assert "--- full output ---" in result.output
    assert "FULL-VERBOSE" in result.output
    assert "--- end ---" in result.output


def test_quiet_mode_prints_only_summary(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--quiet", "--"),
                                     sys.executable, "-c", "print('NOISY')"])
    assert result.exit_code == 0
    assert "[ok" not in result.output
    assert "NOISY" not in result.output
    assert "matched 1; executed 1" in result.output


def test_quiet_mode_still_shows_failed_summary(tmp_path: Path) -> None:
    root = _root(tmp_path, ["alpha"])
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--quiet", "--"),
                                     sys.executable, "-c", "import sys; sys.exit(2)"])
    assert result.exit_code == 2
    assert "[failed" not in result.output
    assert "1 failed" in result.output


# ------------------------------------------------------------ interruption


def test_ctrl_c_maps_to_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path, ["alpha"])

    def boom(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli.fleet_run, "execute_run_plan", boom)
    result = runner.invoke(cli.app, [*_cmd(root, "--all", "--"), *_PASS])
    assert result.exit_code == 130
    assert "interrupted" in result.output
