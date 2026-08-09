"""Tests for the generic subprocess runner, using real local processes.

Uses ``sys.executable`` rather than assuming ``python`` is on PATH. The
process-group-specific assertion is skipped on non-POSIX platforms.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from fleetman import runner as runner_mod
from fleetman.runner import (
    OUTPUT_TAIL_BYTES,
    CommandOutcome,
    CommandRequest,
    subprocess_runner,
)

_PASS = [sys.executable, "-c", "pass"]


def _run(argv: list[str], **kw) -> CommandOutcome:
    return subprocess_runner(CommandRequest(argv=argv, **kw))


# ------------------------------------------------------------------ basic runs


def test_success_exit_zero() -> None:
    out = _run(_PASS)
    assert out.exit_code == 0
    assert out.ok is True
    assert out.timed_out is False
    assert out.error is None


def test_nonzero_exit_code() -> None:
    out = _run([sys.executable, "-c", "import sys; sys.exit(7)"])
    assert out.exit_code == 7
    assert out.ok is False
    assert out.timed_out is False
    assert out.error is None


def test_project_cwd(tmp_path: Path) -> None:
    out = _run([sys.executable, "-c", "import pathlib; print(pathlib.Path.cwd())"],
               cwd=str(tmp_path))
    assert out.exit_code == 0
    assert str(tmp_path) in out.output_tail


def test_combined_output_preserves_os_write_order() -> None:
    # Unbuffered writes to both fds keep the observed order in the combined spool.
    script = "import os; os.write(1, b'OUT\\n'); os.write(2, b'ERR\\n')"
    out = _run([sys.executable, "-c", script])
    assert out.output_tail == "OUT\nERR\n"


def test_stdin_is_noninteractive_eof() -> None:
    script = "import sys; print(len(sys.stdin.buffer.read()))"
    out = _run([sys.executable, "-c", script])
    assert out.exit_code == 0
    assert out.output_tail.strip() == "0"


def test_missing_executable_is_spawn_error() -> None:
    out = _run(["definitely-not-a-real-binary-xyz"])
    assert out.exit_code is None
    assert out.ok is False
    assert out.error is not None


# ------------------------------------------------------------ bounded output


def test_large_output_truncated_to_tail() -> None:
    script = f"print('x' * {OUTPUT_TAIL_BYTES * 4})"
    out = _run([sys.executable, "-c", script])
    assert out.output_truncated is True
    assert len(out.output_tail) == OUTPUT_TAIL_BYTES
    assert out.output_tail.endswith("x\n")  # tail, not head


def test_small_output_not_truncated_and_replays_full() -> None:
    seen: list[bytes] = []

    def sink(data: bytes) -> None:
        seen.append(data)

    out = subprocess_runner(
        CommandRequest(argv=[sys.executable, "-c", "print('hi')"]), on_output=sink
    )
    assert out.output_truncated is False
    assert out.output_tail == "hi\n"
    assert seen == [b"hi\n"]


def test_sink_receives_full_large_output() -> None:
    seen: list[bytes] = []

    def sink(data: bytes) -> None:
        seen.append(data)

    script = f"print('y' * {OUTPUT_TAIL_BYTES * 4})"
    out = subprocess_runner(
        CommandRequest(argv=[sys.executable, "-c", script]), on_output=sink
    )
    assert out.output_truncated is True
    assert len(seen) == 1 and len(seen[0]) == OUTPUT_TAIL_BYTES * 4 + 1  # + newline
    assert seen[0].startswith(b"y")


# -------------------------------------------------------------- timeout/cleanup


def test_timeout_marks_timed_out() -> None:
    started = time.monotonic()
    out = _run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_sec=0.3)
    elapsed = time.monotonic() - started
    assert out.timed_out is True
    assert out.exit_code is None
    assert out.ok is False
    assert elapsed < 15.0  # not the full 60s sleep


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")
def test_timeout_terminates_descendant_processes() -> None:
    script = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    out = _run([sys.executable, "-c", script], timeout_sec=0.3)
    assert out.timed_out is True
    pid = int(out.output_tail.strip().splitlines()[-1])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # descendant is gone
        time.sleep(0.05)
    pytest.fail(f"descendant {pid} still alive after timeout")


def test_spool_closed_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list = []
    real = runner_mod.tempfile.SpooledTemporaryFile

    def spy(*args, **kwargs):
        obj = real(*args, **kwargs)
        created.append(obj)
        return obj

    monkeypatch.setattr(runner_mod.tempfile, "SpooledTemporaryFile", spy)
    out = _run(_PASS)
    assert out.exit_code == 0
    assert created and all(o.closed for o in created)


def test_spool_closed_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list = []
    real = runner_mod.tempfile.SpooledTemporaryFile

    def spy(*args, **kwargs):
        obj = real(*args, **kwargs)
        created.append(obj)
        return obj

    monkeypatch.setattr(runner_mod.tempfile, "SpooledTemporaryFile", spy)
    out = _run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_sec=0.2)
    assert out.timed_out is True
    assert created and all(o.closed for o in created)


# ------------------------------------------------------------- interruption


def test_keyboard_interrupt_terminates_group_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate Ctrl-C during wait: the process group is terminated and the
    KeyboardInterrupt propagates (the CLI maps it to exit 130)."""
    killpg_calls: list[int] = []

    class FakeProc:
        _waits = 0

        def __init__(self, *args, **kwargs):
            self.returncode = None
            self.pid = 4242

        def poll(self) -> None:
            return None

        def wait(self, timeout=None) -> int:
            self._waits += 1
            if self._waits == 1:  # the timeout wait -> interrupt
                raise KeyboardInterrupt()
            return 0  # the graceful-wait after the group termination

    def fake_killpg(pid: int, sig: int) -> None:
        killpg_calls.append((pid, sig))

    monkeypatch.setattr(runner_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(runner_mod.os, "killpg", fake_killpg)
    with pytest.raises(KeyboardInterrupt):
        _run([sys.executable, "-c", "pass"])
    assert len(killpg_calls) == 1 and killpg_calls[0][0] == 4242


def test_empty_argv_is_spawn_error() -> None:
    out = _run([])
    assert out.exit_code is None and out.error == "empty argv"
