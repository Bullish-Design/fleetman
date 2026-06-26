"""`fleetman init`: install fleetman's agent skill into a workspace.

fleetman is workspace-scoped, so ``init`` targets the *workspace root* (the
directory whose children are repos), writing the routing skill so agents dropped
anywhere under it learn to consult the generated index.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Look for the shipped skill in the installed wheel first (force-included next to
# the package as ``_skill/SKILL.md``), then in the source tree for editable runs.
_SKILL_CANDIDATES = (
    Path(__file__).resolve().parent / "_skill" / "SKILL.md",
    Path(__file__).resolve().parents[2] / "skills" / "fleetman" / "SKILL.md",
)


def run_init(root: Path, skills_dir: str = ".claude/skills") -> Path:
    """Copy the fleetman SKILL.md into ``root/<skills_dir>/fleetman/``."""
    dest_dir = root / skills_dir / "fleetman"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "SKILL.md"
    src = next((p for p in _SKILL_CANDIDATES if p.exists()), None)
    if src is not None:
        shutil.copyfile(src, dest)
    else:  # shipped skill missing (e.g. odd install) — write a minimal stand-in
        dest.write_text(_FALLBACK_SKILL)
    return dest


_FALLBACK_SKILL = """\
---
name: fleetman
description: Use to understand the projects in this workspace and how they integrate. Consult the fleetman index before exploring repos by hand.
---

# fleetman — workspace map

Run `fleetman index` at the workspace root, then read `.agents/index/PROJECTS.md`
(tables + dependency graph) or query `.agents/index/registry.json`.
"""
