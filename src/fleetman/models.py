"""Pydantic models for the fleet index.

fleetman normalizes a workspace of sibling repos into one typed, structured
report — the same contract the other `*man` tools follow, applied to the
fleet/workspace domain instead of a single repo.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

Kind = str    # "python" | "nix" | "nvim" | "other"
Family = str  # "template" | "dantic" | "man" | "nix" | "nvim" | "other"
Layer = str   # "scaffold" | "lib" | "tool" | "infra" | "plugin" | "app"


class FleetModel(BaseModel):
    """Base: forbid unknown fields so a schema drift is a loud error."""

    model_config = ConfigDict(extra="forbid")


class Project(FleetModel):
    """One repo in the workspace, with its resolved cross-repo edges."""

    name: str                          # directory name (authoritative identity)
    pkg: str                           # python package name, or == name
    kind: Kind = "other"
    family: Family = "other"
    layer: Layer = "app"
    purpose: str = ""
    stub: bool = False                 # uncustomized template-py scaffold
    requires_python: str | None = None
    has_devenv: bool = False
    has_flake: bool = False
    depends_on: list[str] = []         # python (pyproject.toml) sibling deps
    flake_inputs: list[str] = []       # nix (flake.nix) sibling inputs
    dependents: list[str] = []         # reverse of depends_on + flake_inputs


class Fleet(FleetModel):
    """The whole workspace: projects plus derived edge views."""

    root: str
    generated_by: str = "fleetman"
    projects: list[Project] = []

    def by_name(self) -> dict[str, Project]:
        return {p.name: p for p in self.projects}

    def python_edges(self) -> list[tuple[str, str]]:
        return [(p.name, d) for p in self.projects for d in p.depends_on]

    def nix_edges(self) -> list[tuple[str, str]]:
        return [(p.name, d) for p in self.projects for d in p.flake_inputs]

    def edge_count(self) -> int:
        return len(self.python_edges()) + len(self.nix_edges())
