"""``fleetman.runner`` — the generic process boundary for fleet commands.

Owns the request/outcome types, bounded combined-output capture, per-process
timeout, process-group termination, and the default subprocess implementation.
Both ``sync`` and ``run`` drive this seam; neither embeds subprocess calls.

Platform note: on POSIX the child starts in its own session/process group, so a
timeout or interruption can terminate *descendants*, not just the direct child.
On Windows only the direct child is terminated (``proc.terminate()``).
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from typing import Protocol

from fleetman.models import FleetModel

#: Bounded tail retained on the outcome; longer output is truncated.
OUTPUT_TAIL_BYTES = 4096

#: Grace period after SIGTERM before SIGKILL when terminating a timed-out group.
TERMINATE_GRACE_SEC = 3.0

#: The spool rolls to a disk-backed temp file beyond this many bytes, so verbose
#: replay never accumulates unbounded output in Python memory.
SPOOL_MEMORY_LIMIT = 1 << 20  # 1 MiB


class CommandRequest(FleetModel):
    """An argv vector to execute, with an optional cwd and per-process timeout."""

    argv: list[str]
    cwd: str | None = None
    timeout_sec: float | None = None


class CommandOutcome(FleetModel):
    """Result of one request: exact exit code, timeout state, spawn error, and a
    bounded tail of combined output.

    Semantic distinctions (never collapse them into one string field):

    - a process that never spawned has ``exit_code=None`` plus ``error``;
    - a timeout has ``timed_out=True``;
    - an ordinary failure retains its real exit code.
    """

    exit_code: int | None = None
    timed_out: bool = False
    output_tail: str = ""
    output_truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True only when a process spawned, ran to completion, and exited 0."""
        return self.error is None and not self.timed_out and self.exit_code == 0


class Runner(Protocol):
    """Executes a :class:`CommandRequest` and reports a typed outcome.

    ``on_output`` is an optional sink for the *complete* combined output bytes —
    used for verbose replay before the outcome's bounded tail is computed.
    """

    def __call__(
        self,
        request: CommandRequest,
        *,
        on_output: Callable[[bytes], None] | None = None,
    ) -> CommandOutcome: ...


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Terminate ``proc``'s process group, escalating to SIGKILL after a short
    grace period. Safe to call after the process already exited."""
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:  # exited between poll() and killpg()
            pass
    else:
        proc.terminate()
    try:
        proc.wait(timeout=TERMINATE_GRACE_SEC)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        proc.wait()


def _finish_spool(
    spool: tempfile.SpooledTemporaryFile,
    outcome: CommandOutcome,
    on_output: Callable[[bytes], None] | None,
) -> CommandOutcome:
    """Attach the bounded output tail, optionally replaying the full spool first."""
    spool.seek(0, os.SEEK_END)
    total = spool.tell()
    outcome.output_truncated = total > OUTPUT_TAIL_BYTES
    if on_output is not None:
        spool.seek(0)
        raw = spool.read()
        on_output(raw)
        tail = raw[-OUTPUT_TAIL_BYTES:] if outcome.output_truncated else raw
    elif outcome.output_truncated:
        spool.seek(-OUTPUT_TAIL_BYTES, os.SEEK_END)
        tail = spool.read()
    else:
        spool.seek(0)
        tail = spool.read()
    outcome.output_tail = tail.decode("utf-8", errors="replace")
    return outcome


def subprocess_runner(
    request: CommandRequest,
    *,
    on_output: Callable[[bytes], None] | None = None,
) -> CommandOutcome:
    """Execute ``request`` with bounded combined capture.

    - stdin is ``DEVNULL`` (noninteractive: commands cannot wait on hidden input);
    - no shell — argv is executed directly;
    - stdout and stderr share one spool so their OS-observed order is preserved;
    - POSIX: the child starts its own session, so timeouts and interruption can
      terminate descendants via the process group.
    """
    argv = list(request.argv)
    if not argv:
        return CommandOutcome(error="empty argv")
    spool = tempfile.SpooledTemporaryFile(mode="w+b", max_size=SPOOL_MEMORY_LIMIT)
    try:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=request.cwd,
                stdin=subprocess.DEVNULL,
                stdout=spool,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:  # missing executable, permission, bad cwd, ...
            return _finish_spool(spool, CommandOutcome(error=str(exc)), on_output)
        try:
            proc.wait(timeout=request.timeout_sec)
        except subprocess.TimeoutExpired:
            _terminate_process_group(proc)
            return _finish_spool(spool, CommandOutcome(timed_out=True), on_output)
        except KeyboardInterrupt:
            _terminate_process_group(proc)
            raise
        return _finish_spool(spool, CommandOutcome(exit_code=proc.returncode), on_output)
    finally:
        spool.close()
