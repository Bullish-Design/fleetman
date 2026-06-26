"""Tests for fleetman's harvest + rendering, on a synthetic workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleetman import core
from fleetman.doctor import doctor_exit, run_doctor
from fleetman.render import projects_md, registry_json


def _repo(root: Path, name: str, *, pyproject: str | None = None, flake: str | None = None,
          readme: str | None = None) -> None:
    d = root / name
    d.mkdir(parents=True)
    if pyproject is not None:
        (d / "pyproject.toml").write_text(pyproject)
    if flake is not None:
        (d / "flake.nix").write_text(flake)
    if readme is not None:
        (d / "README.md").write_text(readme)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # A lib, a tool depending on it (git+ form), an app depending on both,
    # an uncustomized clone, and two nix repos with a flake-input edge.
    _repo(tmp_path, "embeddy", pyproject='[project]\nname="embeddy"\n'
          'description="Embedding library."\ndependencies=[]\n')
    _repo(tmp_path, "taskman", pyproject='[project]\nname="taskman"\n'
          'description="Task tool."\ndependencies=['
          '"embeddy @ git+https://github.com/x/embeddy@abc",\n"typer",\n]\n')
    _repo(tmp_path, "muse", pyproject='[project]\nname="muse"\n'
          'description="App."\ndependencies=["embeddy>=1","taskman"]\n'
          '[project.optional-dependencies]\nx=["embeddy[extra]"]\n')
    _repo(tmp_path, "agent-sidecar", pyproject='[project]\nname="template-py"\n'
          'description="A Python project template with modern tooling."\n')
    _repo(tmp_path, "nixos-core", flake='{ inputs = { nixpkgs.url = "github:NixOS/nixpkgs"; }; }')
    _repo(tmp_path, "nix-meta", flake='{ inputs = {\n'
          'nixos-core.url = "git+https://github.com/Bullish-Design/nixos-core.git?ref=main";\n'
          '}; }')
    # noise that must be ignored
    (tmp_path / ".git").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


def test_python_edges_resolve_across_dep_forms(workspace: Path) -> None:
    fleet = core.harvest(workspace)
    by = fleet.by_name()
    assert by["taskman"].depends_on == ["embeddy"]          # git+ form resolved
    assert by["muse"].depends_on == ["embeddy", "taskman"]  # bare + extra forms
    assert ".git" not in by and "logs" not in by            # SKIP honored


def test_dependents_are_reverse_edges(workspace: Path) -> None:
    by = core.harvest(workspace).by_name()
    assert by["embeddy"].dependents == ["muse", "taskman"]
    # has dependents -> classified as a lib even though its name isn't a known family
    assert by["embeddy"].family == "other" and by["embeddy"].layer == "lib"
    # name-driven family detection: *man -> tool
    assert by["taskman"].family == "man" and by["taskman"].layer == "tool"


def test_nix_flake_input_edge(workspace: Path) -> None:
    by = core.harvest(workspace).by_name()
    assert by["nix-meta"].flake_inputs == ["nixos-core"]
    assert by["nix-meta"].kind == "nix" and by["nix-meta"].family == "nix"


def test_uncustomized_clone_flagged_without_hijacking_graph(workspace: Path) -> None:
    by = core.harvest(workspace).by_name()
    assert by["agent-sidecar"].stub is True
    # The clone declares name="template-py" but must not capture muse's "embeddy"/etc;
    # directory names stay authoritative.
    assert by["muse"].depends_on == ["embeddy", "taskman"]


def test_doctor_passes_on_real_workspace(workspace: Path) -> None:
    assert doctor_exit(run_doctor(workspace)) == 0
    assert doctor_exit(run_doctor(workspace / "embeddy")) == 2  # no child projects


def test_render_is_deterministic_and_has_graph(workspace: Path) -> None:
    fleet = core.harvest(workspace)
    md = projects_md(fleet)
    assert projects_md(fleet) == md                      # deterministic
    assert "```mermaid" in md
    assert "embeddy --> " not in md                      # embeddy is a target, not source
    assert "taskman --> embeddy" in md                   # solid python edge
    assert "nix_meta -.-> nixos_core" in md              # dashed nix edge
    assert registry_json(fleet).count('"name"') >= 6     # all projects serialized
