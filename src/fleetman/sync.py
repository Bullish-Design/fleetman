"""``fleetman sync``: reconcile the declared repo set with what's on disk.

Clone what's missing, fetch what's present. **Filesystem-only** — never pushes,
never force-checkouts, never deletes an unmanaged directory (it surfaces it and
leaves it). The plan (:func:`plan_sync`) is pure and network-free; the apply
(:func:`apply_sync`) shells out through an injectable ``runner`` so tests never
touch git or the network.

VCS boundary: fetching an existing checkout goes through **gitman** (``gitman
pull`` — fetch + non-clobbering integrate). Initial acquisition uses a *bootstrap*
``git clone`` because gitman has no ``clone`` verb yet — see
``.scratch/projects/002-fleet-write-ops/PLAN.md`` §1. That bootstrap is confined to
:func:`_bootstrap_clone`, the single audited spot to swap for ``gitman clone`` once
it lands.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path

from fleetman.manifest import Manifest
from fleetman.models import Fleet, FleetModel


class RepoAction(str, Enum):
    clone = "clone"          # declared, not on disk → acquire
    fetch = "fetch"          # declared, on disk → advance (gitman pull)
    unmanaged = "unmanaged"  # on disk, not declared → surface, never touch


class SyncItem(FleetModel):
    name: str
    action: RepoAction
    path: str
    url: str | None = None
    ref: str | None = None
    note: str | None = None          # e.g. "present but not a git repo"


class SyncResult(FleetModel):
    name: str
    action: RepoAction
    ok: bool
    detail: str = ""


class SyncPlan(FleetModel):
    items: list[SyncItem] = []

    def to_clone(self) -> list[SyncItem]:
        return [i for i in self.items if i.action is RepoAction.clone]

    def to_fetch(self) -> list[SyncItem]:
        return [i for i in self.items if i.action is RepoAction.fetch]

    def unmanaged(self) -> list[SyncItem]:
        return [i for i in self.items if i.action is RepoAction.unmanaged]

    def has_drift(self) -> bool:
        """True if the declared set and the disk disagree (something to clone, or
        an on-disk repo nobody declared)."""
        return any(i.action in (RepoAction.clone, RepoAction.unmanaged) for i in self.items)


class RunOutcome(FleetModel):
    ok: bool
    output: str = ""


# A runner executes an argv in an optional cwd and reports the outcome.
Runner = Callable[[Sequence[str], "Path | None"], RunOutcome]


def subprocess_runner(cmd: Sequence[str], cwd: Path | None = None) -> RunOutcome:
    """Default runner: run ``cmd`` in ``cwd``, capturing combined output."""
    try:
        proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)
    except OSError as exc:  # command missing, permission, etc.
        return RunOutcome(ok=False, output=str(exc))
    return RunOutcome(ok=proc.returncode == 0, output=(proc.stdout + proc.stderr).strip())


def plan_sync(root: Path, manifest: Manifest, discovered: Fleet) -> SyncPlan:
    """Classify each declared repo (clone/fetch) and each on-disk project not in the
    manifest (unmanaged). Pure: filesystem presence only — no git, no network."""

    declared = {r.name: r for r in manifest.repos}
    on_disk = {p.name for p in discovered.projects}
    items: list[SyncItem] = []

    for name, spec in sorted(declared.items()):
        path = root / name
        if path.is_dir():
            note = None if (path / ".git").exists() else "present but not a git repo"
            items.append(SyncItem(name=name, action=RepoAction.fetch, path=str(path),
                                  url=spec.url, ref=spec.ref, note=note))
        else:
            items.append(SyncItem(name=name, action=RepoAction.clone, path=str(path),
                                  url=spec.url, ref=spec.ref))

    for name in sorted(on_disk - set(declared)):
        items.append(SyncItem(name=name, action=RepoAction.unmanaged, path=str(root / name)))

    return SyncPlan(items=items)


def _bootstrap_clone(url: str, ref: str | None, dest: Path, runner: Runner) -> RunOutcome:
    """Acquire a new checkout, then bring it under gitman management.

    Bootstrap seam: gitman has no ``clone`` verb yet, so the *initial* acquisition
    is a raw ``git clone`` (the one audited exception to "never raw git"), followed
    by ``gitman init --colocate`` to hand the checkout to gitman. Swap the first
    call for ``gitman clone`` here once that verb exists — nothing else changes.
    """
    cmd = ["git", "clone"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    out = runner(cmd, None)
    if not out.ok:
        return out
    colocate = runner(["gitman", "init", "--colocate"], dest)
    if not colocate.ok:
        return RunOutcome(ok=True, output=f"cloned; gitman init skipped ({colocate.output})")
    return RunOutcome(ok=True, output="cloned + colocated under gitman")


def _fetch(dest: Path, runner: Runner) -> RunOutcome:
    """Advance an existing checkout via gitman (fetch + non-clobbering integrate).

    Uses ``gitman pull`` deliberately: it does not force-checkout, so a dirty or
    diverged working copy is reported by gitman rather than clobbered — the safe
    behavior given gitman's open ``start``-after-``land`` / ``reconcile`` issues.
    """
    return runner(["gitman", "pull"], dest)


def apply_sync(plan: SyncPlan, *, dry_run: bool, runner: Runner = subprocess_runner) -> list[SyncResult]:
    """Execute a plan. ``dry_run`` performs no effects (zero runner calls). Unmanaged
    items are never acted on and produce no result."""

    results: list[SyncResult] = []
    for item in plan.items:
        if item.action is RepoAction.clone:
            if dry_run:
                results.append(SyncResult(name=item.name, action=item.action, ok=True, detail="would clone"))
            else:
                out = _bootstrap_clone(item.url or "", item.ref, Path(item.path), runner)
                results.append(SyncResult(name=item.name, action=item.action, ok=out.ok, detail=out.output))
        elif item.action is RepoAction.fetch:
            if dry_run:
                results.append(SyncResult(name=item.name, action=item.action, ok=True, detail="would fetch"))
            else:
                out = _fetch(Path(item.path), runner)
                results.append(SyncResult(name=item.name, action=item.action, ok=out.ok, detail=out.output))
        # RepoAction.unmanaged → intentionally no effect, no result.
    return results
