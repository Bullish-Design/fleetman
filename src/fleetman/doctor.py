"""`fleetman doctor`: validate that a workspace root is indexable."""

from __future__ import annotations

from pathlib import Path

from fleetman.core import SKIP
from fleetman.models import FleetModel


class Check(FleetModel):
    name: str
    ok: bool
    detail: str


def run_doctor(root: Path) -> list[Check]:
    checks: list[Check] = []

    checks.append(Check(
        name="workspace root",
        ok=root.is_dir(),
        detail=str(root) if root.is_dir() else f"not a directory: {root}",
    ))
    if not root.is_dir():
        return checks

    children = [p for p in root.iterdir()
                if p.is_dir() and p.name not in SKIP and not p.name.startswith(".")]
    checks.append(Check(
        name="projects found",
        ok=bool(children),
        detail=f"{len(children)} candidate project directories",
    ))

    manifested = [p for p in children
                  if (p / "pyproject.toml").exists() or (p / "flake.nix").exists()]
    checks.append(Check(
        name="manifests present",
        ok=bool(manifested),
        detail=f"{len(manifested)} with pyproject.toml or flake.nix",
    ))
    return checks


def doctor_exit(checks: list[Check]) -> int:
    """0 if all good, else 2 (infra/config) — nothing here is a domain decision."""
    return 0 if all(c.ok for c in checks) else 2
