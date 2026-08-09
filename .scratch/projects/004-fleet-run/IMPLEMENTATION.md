# Implementing `fleetman run`

This guide translates `CONCEPT.md` into a staged implementation for the current
fleetman repository. It is written against the existing modules in
`src/fleetman/`, the Typer CLI in `src/fleetman/cli.py`, and the current 19-test
suite.

## Goal

Add a serial, noninteractive fleet command executor with:

- explicit all-project or filtered scope;
- safe live project selection;
- a typed dry-run plan;
- direct argv execution with project cwd;
- bounded combined output;
- exact exit codes and timeout state;
- process-group cleanup on timeout and interruption;
- continue/halt behavior;
- deterministic reporting and fleet-wide exit codes.

## Before coding

1. Read `CONCEPT.md` completely. Treat its decisions as the product contract.
2. Run the baseline:

   ```bash
   pytest -q
   ```

   Expected before implementation: 19 passing tests.
3. Inspect `git status --short` and preserve unrelated worktree changes.
4. Work in the slices below. Keep each slice independently testable.

## Slice 1 — define and extract the generic runner

### 1.1 Create `src/fleetman/runner.py`

Move generic subprocess behavior out of `sync.py`. Do not merely copy the
existing `RunOutcome(ok, output)` type: it cannot represent an exit code, timeout,
spawn error, or bounded output.

Start with types similar to:

```python
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from fleetman.models import FleetModel


class CommandRequest(FleetModel):
    argv: list[str]
    cwd: str | None = None
    timeout_sec: float | None = None


class CommandOutcome(FleetModel):
    exit_code: int | None = None
    timed_out: bool = False
    output_tail: str = ""
    output_truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and not self.timed_out
            and self.exit_code == 0
        )


class Runner(Protocol):
    def __call__(self, request: CommandRequest) -> CommandOutcome: ...
```

Exact field names may vary, but preserve the semantic distinctions. A process
that never spawned has `exit_code=None` plus `error`; a timeout has
`timed_out=True`; an ordinary failure retains its real exit code.

Use `list[str]` in the pydantic request model. Convert any incoming sequence at
the call boundary.

### 1.2 Implement bounded/spooled combined output

Do not use the current unbounded `subprocess.run(..., capture_output=True)`
implementation for fleet execution.

A practical v1 implementation is:

1. Create `tempfile.SpooledTemporaryFile` in binary mode with an in-memory
   threshold.
2. Start `subprocess.Popen` with:
   - `stdin=subprocess.DEVNULL`;
   - `stdout=spool`;
   - `stderr=subprocess.STDOUT`;
   - `cwd=request.cwd`;
   - `start_new_session=True` on POSIX;
   - no shell.
3. Wait with `process.wait(timeout=request.timeout_sec)`.
4. Seek/read only the final `OUTPUT_TAIL_BYTES` for the normal result.
5. Decode with UTF-8 and `errors="replace"`.
6. Set `output_truncated` when the total byte length exceeded the tail limit.
7. Close the spool in a `finally` block.

Keeping stdout and stderr on the same destination preserves their write order as
observed by the OS better than concatenating two completed buffers. The spool
prevents arbitrary output from accumulating in Python memory and still permits
verbose replay before it is closed.

There are two clean ways to support verbose replay:

- Have the runner accept an optional output callback/sink used while rendering.
- Return a temporary-log handle/path owned by an execution context and delete it
  after rendering.

Prefer an explicit execution-output abstraction over putting unlimited full
output in the pydantic result. Whatever design is chosen, test ownership and
cleanup. `RunResult` should retain only the bounded tail.

### 1.3 Implement process-group termination

Add a private helper with behavior equivalent to:

```python
def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=TERMINATE_GRACE_SEC)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()
```

Handle `ProcessLookupError` because the process may exit between polling and
signaling. Always reap the direct child.

On a request timeout:

- terminate the group;
- retain output written before termination;
- return `timed_out=True`, `exit_code=None`;
- do not convert the timeout into a generic string-only error.

On `OSError` during spawn, return `error=str(exc)` and `exit_code=None`.

On `KeyboardInterrupt`, terminate the process group and re-raise. The CLI needs
to distinguish interruption from project failure.

### 1.4 Migrate `sync.py`

Remove its local `RunOutcome`, `Runner`, and `subprocess_runner`. Import the new
runner API.

Update `_bootstrap_clone`, `_fetch`, and `apply_sync` to create
`CommandRequest` objects. Preserve existing sync behavior:

- clone runs without a project cwd;
- gitman initialization runs in the cloned destination;
- fetch runs in the existing repository;
- sync does not request a timeout yet;
- details use a bounded output/error description;
- dry-run still causes zero runner calls.

Do not use `from fleetman.runner import *`. Explicit imports make the refactor
auditable.

### 1.5 Update and expand runner/sync tests

Modify `tests/test_sync.py`'s `FakeRunner` to record `CommandRequest` objects and
return `CommandOutcome`.

Add `tests/test_runner.py` using actual local subprocesses. Cover:

- success and exact exit code 0;
- nonzero exit code, for example Python exiting 7;
- project cwd, using Python to print `Path.cwd()`;
- combined stdout/stderr capture;
- stdin is noninteractive/EOF;
- missing executable produces a spawn error;
- large output is truncated to the configured tail;
- timeout produces `timed_out=True`;
- on POSIX, a spawned descendant is gone after timeout;
- temporary output resources are removed after use.

Use `sys.executable` in tests instead of assuming `python` is on PATH. Skip the
process-group-specific assertion on unsupported platforms.

Run:

```bash
pytest -q tests/test_runner.py tests/test_sync.py
pytest -q
```

## Slice 2 — implement pure selection and planning

### 2.1 Create `src/fleetman/run.py`

Keep this module independent of Typer. It should raise domain exceptions or
pydantic validation errors that the CLI can translate.

Define constants or enums for allowed values rather than duplicating literals in
the CLI and selector:

```python
FAMILIES = frozenset({"template", "dantic", "man", "nix", "nvim", "other"})
KINDS = frozenset({"python", "nix", "nvim", "other"})
LAYERS = frozenset({"scaffold", "lib", "tool", "infra", "plugin", "app"})
```

Define a focused exception such as `RunValidationError(ValueError)` for unsafe
paths, unknown values, incompatible scope, and invalid timeout.

### 2.2 Define plan and result models

Implement the models from the concept:

- `PlannedProject`
- `RunPlan`
- `RunStatus`
- `RunResult`
- an aggregate such as `RunReport`

An appropriate aggregate shape is:

```python
class RunReport(FleetModel):
    plan: RunPlan
    results: list[RunResult] = []
    halted: bool = False

    @property
    def matched_count(self) -> int: ...

    @property
    def executed_count(self) -> int: ...

    @property
    def not_run_count(self) -> int: ...

    @property
    def failed(self) -> list[RunResult]: ...
```

Avoid storing separate mutable count fields that can disagree with the plan and
results. Derive counts where possible.

### 2.3 Validate scope

Create a normalized selector input, either a pydantic model or keyword arguments.
Validation rules:

1. At least one positive selector or `all_projects=True`.
2. `--all` and a positive selector cannot coexist.
3. Exclusion alone is insufficient.
4. Every family/kind/layer value belongs to its allowed set.
5. Timeout, if present, is finite and greater than zero.
6. Quiet/verbose conflict stays in the CLI because it is presentation state.

Report all invalid values in a deterministic message where practical.

### 2.4 Normalize and validate marker paths

For every `--if` value:

```python
candidate = Path(value)
if not value or candidate.is_absolute() or ".." in candidate.parts:
    raise RunValidationError(...)
```

For each project, resolve `project_dir / candidate` with `strict=False`, then use
`Path.is_relative_to(project_dir.resolve())`. Reject escapes before testing
existence.

Be careful with symlinks inside the project: a marker symlink resolving outside
the project must be rejected, even though the lexical path is relative.

### 2.5 Enforce operational project containment

Build project paths from `Path(fleet.root) / project.name`. For every candidate:

- reject a project directory that is itself a symlink;
- require it to be an existing directory;
- resolve it;
- require the resolved path to be beneath the resolved fleet root.

Do this during plan construction so dry-run and execution share the exact same
validated paths. Execution must consume `RunPlan`; it must not reconstruct paths
from names later.

### 2.6 Implement filter semantics

Implement one pure function such as:

```python
def build_run_plan(
    fleet: Fleet,
    command: Sequence[str],
    *,
    all_projects: bool,
    families: Sequence[str] = (),
    kinds: Sequence[str] = (),
    layers: Sequence[str] = (),
    if_paths: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> RunPlan:
    ...
```

The order of operations is:

1. Validate non-empty command.
2. Validate scope and values.
3. Establish the live root and candidate project paths.
4. Match any supplied family.
5. Match any supplied kind.
6. Match any supplied layer.
7. Require all marker paths.
8. Remove excluded exact names.
9. Sort by `(name.lower(), name)`.
10. Return the typed plan, including an empty project list on a valid no-match.

Do not raise for no matches. No-match is a valid domain result and maps to exit 1
in the CLI.

### 2.7 Add selector tests

Create `tests/test_run.py` with a synthetic `Fleet` and real temporary project
directories. Cover:

- `--all` equivalent includes every ordinary project;
- no scope rejected;
- all plus selector rejected;
- exclusion alone rejected;
- repeat values are ORed within a dimension;
- dimensions are ANDed;
- repeated markers are ANDed;
- exclusions apply last;
- exact/case-sensitive exclusion behavior;
- unknown family/kind/layer rejected;
- absolute, parent-traversing, empty, and symlink-escaping markers rejected;
- symlinked project directory rejected;
- deterministic case-folded ordering;
- valid no-match returns an empty plan;
- command argv is preserved exactly.

Run:

```bash
pytest -q tests/test_run.py
pytest -q
```

## Slice 3 — implement serial orchestration

### 3.1 Add `execute_run_plan`

Use a function shaped approximately as:

```python
def execute_run_plan(
    plan: RunPlan,
    *,
    halt_on_fail: bool = False,
    timeout_sec: float | None = None,
    runner: Runner = subprocess_runner,
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    ...
```

The loop must:

1. Iterate over `plan.projects` without re-sorting.
2. Record `started = clock()`.
3. Invoke the runner with the plan argv, validated project cwd, and timeout.
4. Record nonnegative elapsed duration.
5. Map the generic outcome to exactly one `RunStatus`.
6. Append one result for every runner call.
7. Break after the first non-passed status only when `halt_on_fail=True`.
8. Return the report with the original full plan.

Do not catch `KeyboardInterrupt` here unless cleanup is needed beyond the runner.
If caught, re-raise after cleanup so the CLI can return 130.

### 3.2 Define outcome-to-status mapping

Use deterministic precedence:

1. `error is not None` → `spawn_error`
2. `timed_out` → `timed_out`
3. `exit_code == 0` → `passed`
4. otherwise → `failed`

Validate impossible runner outcomes during development, such as no error, no
timeout, and `exit_code=None`. A malformed injected runner should fail loudly.

### 3.3 Add orchestration tests

Use a fake runner for exact scheduling behavior:

- commands receive the exact argv and each planned cwd;
- one result per call;
- success continues;
- ordinary failure continues by default;
- timeout and spawn error continue by default;
- halt-on-fail stops after each failure category;
- `matched_count`, `executed_count`, and `not_run_count` remain accurate;
- durations use the injected monotonic clock;
- result order equals plan order;
- KeyboardInterrupt propagates and no later calls occur.

Run the entire suite after the slice.

## Slice 4 — add the Typer command and reporting

### 4.1 Add imports and CLI option aliases

In `src/fleetman/cli.py`, import the planning/execution API under explicit names.
Avoid naming collisions between the CLI function `run` and module functions; one
simple approach is:

```python
from fleetman import run as fleet_run
```

Define reusable annotated types only where they improve readability. Repeatable
Typer options should normalize `None` to empty lists before calling core logic.

### 4.2 Add the command signature

Use a signature close to:

```python
@app.command()
def run(
    command: Annotated[list[str], typer.Argument(help="Command and arguments after --.")],
    root: RootOpt = None,
    all_projects: Annotated[bool, typer.Option("--all")] = False,
    family: Annotated[list[str] | None, typer.Option("--family")] = None,
    kind: Annotated[list[str] | None, typer.Option("--kind")] = None,
    layer: Annotated[list[str] | None, typer.Option("--layer")] = None,
    if_path: Annotated[list[str] | None, typer.Option("--if")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    halt_on_fail: Annotated[bool, typer.Option("--halt-on-fail")] = False,
    timeout: Annotated[float | None, typer.Option("--timeout")] = None,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
) -> None:
    ...
```

Verify this exact annotation style against Typer 0.27 rather than assuming the
minimum declared Typer 0.12 behaves identically. If supporting the full declared
dependency range matters, run a small version matrix or raise the minimum Typer
version based on demonstrated need.

### 4.3 Validate CLI-owned concerns

The command body should:

1. Resolve the root through `_root()`.
2. Exit 2 with a diagnostic if it is not a directory.
3. Exit 3 if `quiet and verbose`.
4. Harvest once.
5. Call `build_run_plan` and convert `RunValidationError` to a concise diagnostic
   plus exit 3.
6. Exit 1 after rendering a no-match message when the plan is empty.

Typer itself handles a missing command before entering the function and exits 2.
Do not promise or test exit 3 for parser-level failures.

### 4.4 Render dry-run plans

Create small private renderer functions rather than embedding all formatting in
the command body. A dry-run should show:

- a safely quoted argv, using `shlex.join(plan.command)` for human readability;
- an explicit note that execution uses argv without a shell;
- ordered project names and resolved paths;
- the final matched/no-execution summary.

`shlex.join` is display-only. Never feed the joined string back to a shell.

Even in quiet mode, print the dry-run summary. Decide whether quiet dry-run omits
the project list; the concept implies quiet suppresses project-level lines, so it
should.

### 4.5 Render execution results

Keep presentation derived entirely from `RunReport`:

- normal: status line per result; bounded tail only for non-passed results;
- verbose: status plus complete output block for every result;
- quiet: summary only;
- all modes: deterministic final summary;
- halted reports include not-run count;
- errors go to stderr where consistent with existing CLI behavior.

If the runner's full-output lifecycle requires rendering before temporary output
cleanup, make that ownership explicit with a context manager around execution and
rendering. Do not leak temporary files merely to keep the CLI thin.

### 4.6 Handle interruption

Wrap execution at the narrow CLI boundary:

```python
try:
    report = fleet_run.execute_run_plan(...)
except KeyboardInterrupt:
    typer.echo("fleetman: interrupted; active command terminated.", err=True)
    raise typer.Exit(130)
```

The runner must already have terminated and reaped its process group before the
exception reaches this handler.

### 4.7 Select the final exit code

After execution:

- 0 when every executed result passed;
- 2 when any result did not pass;
- 1 was already returned for a valid empty plan;
- 3 was already returned for semantic validation;
- 130 is used only for interruption.

An execution report cannot contain zero results for a non-empty plan unless
interrupted before scheduling; treat impossible states as internal errors rather
than claiming success.

### 4.8 Add CLI tests

Create `tests/test_run_cli.py` using `typer.testing.CliRunner`. Avoid running
arbitrary real tools through the full live workspace; use temporary synthetic
roots.

Cover:

- `--` preserves `-q` and other command flags;
- command without `--` but without option-looking arguments follows Typer's
  actual behavior;
- missing command exits 2;
- missing positive scope exits 3;
- `--all` plus selector exits 3;
- quiet plus verbose exits 3;
- invalid/zero/negative/non-finite timeout exits 3;
- invalid filter exits 3;
- unsafe marker path exits 3;
- nonexistent root exits 2;
- no match exits 1;
- non-empty dry-run exits 0 and runs nothing;
- success exits 0;
- command failure and timeout exit 2;
- halt summary gives matched/executed/not-run counts;
- quiet and normal output contracts;
- Ctrl-C maps to 130, using injection/monkeypatching rather than sending a
  fragile real terminal signal where possible.

Because the CLI directly imports a default runner, make injection monkeypatchable
without exposing a public testing-only CLI option.

## Slice 5 — documentation and package integration

### 5.1 Update `README.md`

Add `run` to the usage block and explain:

- explicit `--all` or positive selector;
- `--` before argv;
- dry-run previews selection only;
- commands execute without a shell;
- commands use the invoking environment, not each project's devenv;
- the copyroom marker example.

Do not include a fixed count of matching repositories.

### 5.2 Update `skills/fleetman/SKILL.md`

Document an agent workflow:

1. Resolve the intended workspace root.
2. Use a narrow selector when possible.
3. Dry-run mutating fleet operations.
4. Review every resolved project path.
5. Run the exact argv.
6. Report failures and do not claim fleet-wide success from a partial result.

Include the copyroom example and the explicit `--all` form for whole-fleet reads.
Keep the skill consistent with the actual CLI help.

### 5.3 Do not add a generic devenv task

`fleetman run` accepts arbitrary argv, which does not fit a fixed named task well.
Document direct invocation from a shell where the required program is available.

### 5.4 Verify packaged skill data

The current `pyproject.toml` force-includes `skills/fleetman/SKILL.md`. Build the
package and inspect the wheel or installed package to ensure the updated skill is
still included.

## Slice 6 — full validation

### 6.1 Static and automated validation

Run the repository's available checks. At minimum:

```bash
pytest -q
python -m compileall -q src tests
```

If the devenv defines lint, type-check, or build tasks, run those as well. Do not
introduce a new checker solely for this feature unless the repository adopts it
generally.

### 6.2 CLI help and parsing smoke tests

Run:

```bash
fleetman run --help
fleetman run --all --dry-run -- git status --short
fleetman run --kind python --dry-run -- pytest -q
```

Confirm option names, documented examples, argv display, deterministic ordering,
and exit codes.

### 6.3 Live marker-selection validation

Against the actual workspace root, compare fleetman dry-run selection with a
direct predicate over the same fresh harvest. Assert set equality, not a fixed
count. Do not execute `copyroom update` as part of automated validation.

One suitable comparison script can import `core.harvest`, select projects whose
validated project path contains `.copier-answers.yml`, and compare those names to
the plan returned by `build_run_plan`.

### 6.4 Manual failure exercises in a temporary workspace

Use disposable temporary project directories to confirm presentation for:

- one pass and one failure while continuing;
- halt on the first failure;
- timeout;
- large output truncation;
- missing executable;
- Ctrl-C with a child process.

Never use a destructive sample command against the real fleet.

### 6.5 Review compatibility with sync

Exercise both sync modes after runner extraction:

- dry-run planning remains network-free and makes no runner calls;
- injected fake clone/fetch tests preserve command and cwd behavior;
- real network operations are not needed for the test suite.

## Suggested commit sequence

Keep commits small enough to review and revert independently:

1. `refactor: extract generic command runner`
2. `test: cover subprocess execution and cleanup`
3. `feat: add fleet run planning and selection`
4. `feat: execute and report fleet run plans`
5. `feat: expose fleetman run command`
6. `docs: document fleet run workflow`

Before each commit:

1. Inspect `git status --short`.
2. Include all relevant active project changes unless explicitly excluded.
3. Run the tests appropriate to the slice.
4. Review the staged diff.
5. Commit and push the current branch promptly per the repository agreement.

## Completion checklist

- [ ] Generic runner exposes exit code, timeout, spawn error, and bounded output.
- [ ] Timeout and Ctrl-C terminate descendant processes.
- [ ] Sync uses the extracted runner and all previous behavior remains tested.
- [ ] Run planning is pure, deterministic, and validates workspace containment.
- [ ] `--all` or a positive selector is mandatory.
- [ ] `--if` cannot escape a project through syntax or symlinks.
- [ ] Dry-run performs zero subprocess calls.
- [ ] Execution is serial, direct-argv, and noninteractive.
- [ ] Halted reports distinguish matched, executed, and not-run projects.
- [ ] Quiet, normal, and verbose output contracts are tested.
- [ ] Parser-level exit 2 and semantic exit 3 are documented accurately.
- [ ] CLI, README, and packaged skill use the same syntax.
- [ ] Live validation compares dynamic sets rather than fixed project counts.
- [ ] Full test suite and package build pass without network access.
