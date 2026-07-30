"""Declarative fleet manifest (``repos.toml``): what *should* be on disk.

The read-only indexer (:func:`fleetman.core.harvest`) reports what *is* on disk;
this module parses the **declared** set. ``fleetman sync`` reconciles the two.

Schema (array-of-tables, ``owner``-agnostic):

    [[repo]]
    name = "argentic"                               # dest dir under the workspace root
    url  = "https://github.com/Bullish-Design/argentic"
    ref  = "main"                                   # optional: branch/tag/commit

Auth is deliberately out of scope — cloning relies on ambient git credentials;
fleetman never handles secrets.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import ValidationError

from fleetman.models import FleetModel


class ManifestError(RuntimeError):
    """``repos.toml`` is missing, malformed, or internally inconsistent."""


class RepoSpec(FleetModel):
    """One declared repo: a destination name, a URL, and an optional ref."""

    name: str                      # directory name under the workspace root
    url: str                       # canonical git URL (https or ssh)
    ref: str | None = None         # branch/tag/commit; None → remote default branch


class Manifest(FleetModel):
    repos: list[RepoSpec] = []

    def names(self) -> set[str]:
        return {r.name for r in self.repos}


def load_manifest(path: Path) -> Manifest:
    """Parse + validate a ``repos.toml``. Raises :class:`ManifestError` on any problem."""

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc

    raw = data.get("repo", [])
    if not isinstance(raw, list):
        raise ManifestError(f"{path}: [[repo]] must be an array of tables")

    try:
        specs = [RepoSpec.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise ManifestError(f"{path}: invalid repo entry: {exc}") from exc

    seen: set[str] = set()
    for s in specs:
        if not s.name or "/" in s.name or s.name.startswith("."):
            raise ManifestError(f"{path}: invalid repo name {s.name!r}")
        if s.name in seen:
            raise ManifestError(f"{path}: duplicate repo {s.name!r}")
        seen.add(s.name)
    return Manifest(repos=specs)


def resolve_manifest_path(explicit: Path | None, root: Path) -> Path | None:
    """Locate the manifest. Order: ``--manifest`` > ``$FLEETMAN_MANIFEST`` >
    ``$REPOMAN_FLEET_MANIFEST`` > a discovered ``repos.toml`` (workspace root, then
    a ``nix-meta/`` child — its intended home). ``None`` if nothing is found."""

    if explicit is not None:
        return explicit.resolve()
    for env in ("FLEETMAN_MANIFEST", "REPOMAN_FLEET_MANIFEST"):
        val = os.environ.get(env)
        if val:
            return Path(val).resolve()
    for candidate in (root / "repos.toml", root / "nix-meta" / "repos.toml"):
        if candidate.is_file():
            return candidate.resolve()
    return None
