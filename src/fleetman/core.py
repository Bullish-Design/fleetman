"""Harvest a workspace of sibling repos into a typed :class:`Fleet`.

Pure stdlib + pydantic. Operates on a workspace root (a directory whose children
are project repos); it never enters a project's devenv. Cross-repo edges are
*derived* from each repo's manifests:

  - python deps  -> ``pyproject.toml`` ``[project].dependencies`` /
    ``optional-dependencies`` / ``[tool.uv.sources]``
  - nix inputs   -> ``flake.nix`` ``inputs`` (``github:`` / ``git+https://``)
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from fleetman.models import Fleet, Project

# Directories that aren't projects.
SKIP = {".agents", ".git", "logs", "vendor", "work", "Archive", "node_modules"}

BOILERPLATE = "A Python project template with modern tooling."

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
# Flake input URLs are always quoted, e.g.
#   "github:Bullish-Design/nixbuild"
#   "git+https://github.com/Bullish-Design/nixos-core.git?ref=main"
_FLAKE_URL = re.compile(r'"(git\+https?://[^"]+|github:[^"]+)"')


def family_and_layer(name: str, is_lib: bool) -> tuple[str, str]:
    """Infer (family, layer) from a project's name — the naming convention."""
    n = name.lower()
    if n.startswith(("template-", "template_")):
        return "template", "scaffold"
    if n.endswith("dantic"):
        return "dantic", "lib"
    if n.endswith(".nvim"):
        return "nvim", "plugin"
    if n.startswith(("nix", "nixos")):
        return "nix", "infra"
    if n.endswith("man"):
        return "man", "tool"
    return "other", ("lib" if is_lib else "app")


def dep_name(spec: str) -> str | None:
    """Canonical package name from a PEP 508 requirement / source spec."""
    spec = spec.strip().split(";", 1)[0]   # drop env marker
    spec = spec.split("@", 1)[0]           # drop "name @ url"
    spec = spec.split("[", 1)[0]           # drop extras
    m = _REQ_NAME.match(spec.strip())
    return m.group(1).lower() if m else None


def flake_refs(text: str) -> set[str]:
    """Sibling repo names referenced as flake inputs (owner-agnostic)."""
    names: set[str] = set()
    for url in _FLAKE_URL.findall(text):
        if url.startswith("github:"):
            parts = url[len("github:"):].split("/")
            repo = parts[1] if len(parts) > 1 else ""
        else:  # git+https://host/owner/repo(.git)?ref=...
            path = url.split("://", 1)[-1].split("?", 1)[0].split("#", 1)[0]
            repo = path.rstrip("/").rsplit("/", 1)[-1]
        repo = repo.removesuffix(".git").strip().lower()
        if repo:
            names.add(repo)
    return names


def _readme_summary(d: Path) -> str:
    for fn in ("README.md", "README.rst", "README.txt", "README"):
        p = d / fn
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip().lstrip("#").strip()
            if s and not s.startswith(("![", "[!", "<", "=", "-")):
                return s[:160]
    return ""


def _scan(d: Path) -> dict:
    """Read one project directory into a scratch dict (pre-edge-resolution)."""
    name = d.name
    info: dict = {
        "name": name, "pkg": name, "kind": "other", "purpose": "",
        "requires_python": None, "has_devenv": (d / "devenv.nix").exists(),
        "has_flake": (d / "flake.nix").exists(), "stub": False,
        "raw_deps": [], "flake_refs": set(), "has_pyproject": False,
    }

    pp = d / "pyproject.toml"
    if pp.exists():
        info["has_pyproject"] = True
        info["kind"] = "python"
        try:
            data = tomllib.loads(pp.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            info["purpose"] = f"(unparsed pyproject: {exc})"
            data = {}
        proj = data.get("project", {}) or {}
        info["pkg"] = proj.get("name") or name
        info["purpose"] = (proj.get("description") or "").strip()
        info["requires_python"] = proj.get("requires-python")
        deps = list(proj.get("dependencies", []) or [])
        for extra in (proj.get("optional-dependencies", {}) or {}).values():
            deps.extend(extra or [])
        deps.extend((data.get("tool", {}).get("uv", {}).get("sources", {}) or {}).keys())
        info["raw_deps"] = deps

    if info["has_flake"]:
        try:
            info["flake_refs"] = flake_refs((d / "flake.nix").read_text(encoding="utf-8"))
        except OSError:
            pass

    if info["kind"] == "other":
        if info["has_flake"]:
            info["kind"] = "nix"
        elif name.endswith(".nvim"):
            info["kind"] = "nvim"

    if not info["purpose"]:
        info["purpose"] = _readme_summary(d)

    info["stub"] = (
        (info["has_pyproject"] and info["pkg"] != name and info["pkg"] == "template-py")
        or info["purpose"] == BOILERPLATE
    )
    return info


def harvest(root: Path) -> Fleet:
    """Scan ``root``'s child directories and resolve cross-repo edges."""
    dirs = sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name not in SKIP and not p.name.startswith(".")
    )
    scratch = [_scan(d) for d in dirs]

    # Resolve names. Directory names are authoritative; a package name only
    # registers if it doesn't shadow a real directory (uncustomized clones
    # still declare name="template-py").
    by_key: dict[str, dict] = {}
    for s in scratch:
        by_key[s["name"].lower()] = s
    for s in scratch:
        by_key.setdefault(s["pkg"].lower(), s)

    for s in scratch:
        py = {by_key[dn]["name"] for spec in s["raw_deps"]
              if (dn := dep_name(spec)) and dn in by_key and by_key[dn] is not s}
        nix = {by_key[r]["name"] for r in s["flake_refs"]
               if r in by_key and by_key[r] is not s}
        s["depends_on"] = sorted(py)
        s["flake_inputs"] = sorted(nix - py)  # don't double-count

    dependents: dict[str, set[str]] = {s["name"]: set() for s in scratch}
    for s in scratch:
        for dep in (*s["depends_on"], *s["flake_inputs"]):
            dependents[dep].add(s["name"])

    projects: list[Project] = []
    for s in scratch:
        deps = sorted(dependents[s["name"]])
        fam, layer = family_and_layer(s["name"], is_lib=bool(deps))
        projects.append(Project(
            name=s["name"], pkg=s["pkg"], kind=s["kind"], family=fam, layer=layer,
            purpose=s["purpose"], stub=s["stub"], requires_python=s["requires_python"],
            has_devenv=s["has_devenv"], has_flake=s["has_flake"],
            depends_on=s["depends_on"], flake_inputs=s["flake_inputs"], dependents=deps,
        ))

    return Fleet(root=str(root), projects=projects)
