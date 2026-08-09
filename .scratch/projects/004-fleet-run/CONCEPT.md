# fleetman — `run` concept: execute a command across selected projects

> Decision-complete concept for a new `fleetman run` command. The command selects
> projects from a live fleet harvest, executes one external program in each
> selected project, and reports typed per-project outcomes. It is motivated by
> fleet-wide copyroom updates but deliberately contains no copyroom-specific
> behavior.

## Status

**Approved concept; not implemented.** The v1 product and safety decisions are
settled here. The implementation sequence is specified in `IMPLEMENTATION.md`.

## Role

`fleetman run` is the fleet operator's general-purpose execution primitive. It
supplies project discovery, explicit scope selection, working-directory changes,
serial process supervision, and fleet-wide reporting. The user supplies an
external program and its argv. Fleetman does not interpret the program, enter a
project's devenv, generate mutations, commit changes, or contain special cases
for tools such as copyroom, gitman, pytest, or Nix.

This turns fleetman from a mapper into a narrowly scoped operator while retaining
the same source of project metadata used by `index`, `list`, and `query`.

## Motivation

The original use case is updating every copyroom-derived repository. Copyroom is
project-root-bound, so the current operation is a shell loop:

```bash
for d in */; do
  [ -f "$d/.copier-answers.yml" ] || continue
  (cd "$d" && copyroom update)
done
```

The equivalent fleetman operation is:

```bash
fleetman run --if .copier-answers.yml --dry-run -- copyroom update
fleetman run --if .copier-answers.yml -- copyroom update
```

This adds an inspectable selection, deterministic ordering, per-project status,
bounded failure output, timeout support, and a documented fleet-wide exit code.
The same primitive supports test sweeps, VCS inspection, Nix checks, and future
maintenance chores.

The number of matching projects is intentionally not part of the contract. At
the time this concept was revised, the stored registry contained 52 projects, a
fresh harvest found 63, and 18 harvested projects contained
`.copier-answers.yml`. These values will continue to change. Tests use synthetic
fixtures; live validation asserts that the dry-run matches the live filesystem,
not a hard-coded count.

## Scope and non-goals

### In v1

- Live project discovery through `core.harvest(root)`.
- Explicit all-project or filtered scope.
- Family, kind, layer, marker-path, and exclusion filters.
- Shell-free argv execution, one project at a time.
- Noninteractive subprocess supervision.
- Per-project timeout and process-group cleanup.
- Continue-on-failure and halt-on-failure modes.
- Dry-run planning with zero subprocess calls.
- Quiet, normal, and verbose reporting.
- Typed plans and results.
- Stable fleetman exit semantics.

### Not in v1

- Parallel jobs.
- Shell parsing, pipelines, redirections, or environment assignment syntax.
- Interactive subprocesses or PTY allocation.
- Automatically entering each project's devenv.
- Per-project environment overrides.
- Persistent full logs or JSON result files.
- Project-name globs.
- Named copyroom, gitman, testee, or Nix workflows.
- Automatic commits, fixes, retries, or rollback.

## Relationship to existing commands

| Capability | Responsibility |
|---|---|
| `fleetman index/graph/list/query` | Read and render the live workspace model. |
| `fleetman sync` | Reconcile declared repositories by cloning or pulling through an audited command set. |
| `fleetman run` | Execute a user-supplied argv across an explicitly selected live project set. |
| `copyroom update` | Update one project from its template. |
| `gitman` | Perform VCS operations in one repository. |
| `testee` | Verify one repository. |

`run` is the general loop into which per-project tools fit. It must not absorb
their domain behavior.

## Command surface

```text
fleetman run (--all | FILTER...) [OPTIONS...] -- COMMAND [ARG...]
```

```text
Scope:
  --all                    Select every harvested project.
  --family FAMILY         Repeatable; OR within this dimension.
  --kind KIND             Repeatable; OR within this dimension.
  --layer LAYER           Repeatable; OR within this dimension.
  --if RELPATH            Repeatable; every path must exist in the project.
  --exclude NAME          Repeatable; remove exact directory names after selection.

Execution:
  --dry-run               Print the plan; execute nothing.
  --halt-on-fail          Stop scheduling projects after the first failed outcome.
  --timeout SECONDS       Positive per-project wall-clock timeout.

Output:
  --quiet                 Print only the final summary and fatal diagnostics.
  --verbose               Show complete captured output for every executed project.

Root:
  --root, -C DIR          Workspace root; otherwise $FLEETMAN_ROOT or cwd.
```

`--quiet` and `--verbose` are mutually exclusive.

## Command parsing

`COMMAND [ARG...]` is an argv vector executed with `shell=False`. The documented
form requires `--`, especially when command arguments start with `-`:

```bash
fleetman run --kind python -- pytest -q --maxfail=1
```

Typer may accept a command without `--` when none of its arguments look like
fleetman options, but that is not the documented interface. Fleetman does not
attempt to detect whether an otherwise valid invocation omitted the separator.

The following shell expressions are not interpreted by fleetman:

```bash
FOO=bar pytest
pytest | tee results.txt
test -f marker && do-something
```

When shell behavior is deliberately required, the user invokes a shell
explicitly and owns its quoting and safety boundary:

```bash
fleetman run --all -- sh -c 'test -f marker && do-something'
```

## Root and project discovery

Root precedence is the existing `_root()` contract:

1. `--root` / `-C`
2. `$FLEETMAN_ROOT`
3. current working directory

The root must resolve to an existing directory or fleetman exits 2.

The project set comes from a **fresh** `core.harvest(root)`. This is live
filesystem discovery, not a read of `.agents/index/registry.json`. Therefore a
run may include projects added since the last `fleetman index`, and its matched
count may differ from the stored registry. Dry-run is the authoritative preview
of the current operation.

`core.harvest()` currently treats every non-hidden, non-skipped child directory
as a project. Before `run` ships, discovery must adopt an explicit symlink policy:

- v1 rejects symlinked project directories from operational selection;
- ordinary project directories resolve beneath the resolved workspace root;
- a violation is a fleetman validation error, not a subprocess failure.

Indexing behavior may continue to observe symlinks if needed, but `run` must not
silently execute outside the workspace boundary.

## Scope selection

The user must provide either `--all` or at least one positive selector:
`--family`, `--kind`, `--layer`, or `--if`.

- `--all` cannot be combined with a positive selector.
- `--exclude` is not a positive selector and may accompany either `--all` or
  positive selectors.
- Values repeated within family, kind, or layer are ORed.
- Different filter dimensions are ANDed.
- Repeated `--if` paths are ANDed: every path must exist.
- Exclusions are applied last by exact, case-sensitive directory name.
- Selected projects are sorted by `(name.lower(), name)`.

Allowed typed values are:

| Filter | Values |
|---|---|
| family | `template`, `dantic`, `man`, `nix`, `nvim`, `other` |
| kind | `python`, `nix`, `nvim`, `other` |
| layer | `scaffold`, `lib`, `tool`, `infra`, `plugin`, `app` |

Invalid values exit 3. Classification is inferred metadata, not a capability
guarantee. For example, `--kind python` says that fleetman classified the project
as Python; it does not guarantee that `pytest` is installed or configured.

### Safe `--if` paths

Each `--if` value must be:

- non-empty;
- relative, never absolute;
- free of `..` components;
- resolved beneath the selected project directory.

The predicate is general path existence. Files, directories, and symlinks may
match, but resolution must remain inside the project. No glob syntax is supported
in v1.

## Planning and dry-run

Selection produces a typed `RunPlan` containing:

- the resolved workspace root;
- the exact command argv;
- the ordered selected project names and resolved paths.

`--dry-run` renders this plan and performs zero runner calls. It prints the exact
argv in an unambiguous representation and every resolved project path. It exits:

- 0 when one or more projects match;
- 1 when no project matches;
- 3 when scope or path validation fails.

Dry-run predicts fleetman-controlled behavior only. It does not determine
whether the external command would succeed or mutate a project.

## Execution semantics

### Ordering and working environment

- Execution is serial in deterministic plan order.
- Each subprocess receives the project directory as cwd.
- The subprocess inherits fleetman's environment.
- Fleetman does not activate the project's devenv.
- Fleetman may add identification variables only in a future version; v1 does
  not alter the environment.

### Noninteractive process model

V1 is explicitly noninteractive:

- stdin is `DEVNULL`;
- the program is executed without a shell;
- stdout and stderr are captured together in observable order;
- output storage is bounded or spooled rather than accumulated without limit;
- the subprocess starts in its own process group/session.

Commands that require prompts or a TTY are outside the v1 contract and should
fail rather than wait on hidden input. An interactive/PTY mode can be designed
later without weakening the default batch behavior.

### Output policy

Normal mode prints a completion line per executed project:

```text
[ok     ] project-name  3.2s
[failed ] other-name    0.9s  exit 1
[timeout] slow-name    60.0s
```

For failed, timed-out, or spawn-error outcomes, normal mode also prints a bounded
tail of combined output. The truncation limit and marker are constants covered
by tests.

Verbose mode prints the complete spooled output for every executed project in a
clearly delimited block. Quiet mode suppresses project lines and output tails,
but never suppresses the final summary or fatal fleetman diagnostics.

Fleetman reports output; it does not parse it into domain-specific advice.

### Timeouts and termination

`--timeout` is a positive number of seconds applied independently to every
project. On expiry, fleetman:

1. marks the outcome `timed_out`;
2. terminates the subprocess process group;
3. waits for a short fixed grace period;
4. kills the group if it remains alive;
5. records the captured output tail and duration;
6. continues unless `--halt-on-fail` was set.

This behavior must prevent descendant processes from surviving a timed-out run
on supported POSIX platforms. Platform limitations must be explicit in code and
tests.

### Failure and interruption

An ordinary nonzero command exit, timeout, or per-project spawn error is a failed
outcome. Execution continues by default.

With `--halt-on-fail`, fleetman stops scheduling after the first failed outcome.
The final report distinguishes:

- matched projects;
- executed projects;
- passed projects;
- failed projects;
- projects not executed because the run halted.

On Ctrl-C or another handled termination signal, fleetman terminates the active
process group, does not schedule another project, prints an interruption summary,
and exits 130. Interruption is not converted into an ordinary project failure.

## Typed model

Planning and execution are separate concepts. Dry-run does not fabricate
successful `RunResult` objects.

```python
class PlannedProject(FleetModel):
    name: str
    path: str


class RunPlan(FleetModel):
    root: str
    command: list[str]
    projects: list[PlannedProject]


class RunStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    timed_out = "timed_out"
    spawn_error = "spawn_error"


class RunResult(FleetModel):
    name: str
    path: str
    command: list[str]
    status: RunStatus
    exit_code: int | None
    duration_sec: float
    output_tail: str = ""
    output_truncated: bool = False
    error: str | None = None
```

The aggregate execution response also retains matched and unexecuted counts so
halted runs can be summarized accurately. `ok: bool` may be a derived property;
it is not the primary state representation.

The generic runner outcome must expose at least:

- exact exit code when a process started and exited;
- timeout state;
- spawn error when no process started;
- bounded/spooled combined output;
- truncation state.

Duration belongs to orchestration or the generic runner, but must use a monotonic
clock.

## Reporting

A normal successful summary resembles:

```text
fleetman: matched 18; executed 18 — 16 passed, 2 failed (copyroom, shellij)
```

A halted summary resembles:

```text
fleetman: matched 18; executed 4 — 3 passed, 1 failed, 14 not run (halted)
```

Dry-run uses planning language rather than execution language:

```text
fleetman: dry-run matched 18 projects; no commands executed
```

Project names in summaries are deterministic. Very large failure lists may be
bounded with an explicit remaining count.

## Exit codes

Fleetman follows the existing family as closely as Typer permits:

| Code | Meaning |
|---|---|
| 0 | At least one project matched and every executed command passed; or a non-empty dry-run plan. |
| 1 | No projects matched. |
| 2 | Command failure, timeout, spawn/infrastructure failure, invalid root, or Typer/Click parser-level usage error. |
| 3 | Semantic validation performed by fleetman: invalid scope combination, filter value, timeout, or unsafe `--if` path. |
| 130 | Interrupted by the user. |

Typer emits parser errors before the command body and uses exit 2 for them. V1
does not globally rewrite Typer's parser behavior. In particular, a missing
command is a parser-level exit 2, while `--timeout 0` is a semantic exit 3.

## Safety and trust model

Fleetman multiplies a user-supplied operation across projects. It cannot infer
whether that operation is read-only, reversible, or destructive. Safety comes
from explicit and inspectable boundaries:

1. **Explicit scope:** the user supplies `--all` or a positive selector.
2. **Live preview:** `--dry-run` shows the exact current project paths and argv.
3. **Workspace containment:** operational project paths and marker predicates
   cannot silently resolve outside the workspace/project boundary.
4. **No implicit shell:** argv is executed directly unless the user explicitly
   invokes a shell.
5. **Noninteractive default:** commands cannot hang on hidden prompts.
6. **Process supervision:** timeout and interruption terminate descendant
   processes, not merely the immediate child.
7. **No invented side effects:** fleetman does not commit, fix, retry, or alter
   command output.
8. **Failure visibility:** every matched/executed/not-run count is reported.

Execute-by-default is intentional. The explicit command and explicit scope are
the authorization. `--dry-run` previews fleetman selection but cannot preview
arbitrary command effects, so a mandatory `--go` would provide limited safety
while degrading routine read-only use.

## Architecture

### `src/fleetman/runner.py` — generic process boundary

Extract and replace the minimal runner currently embedded in `sync.py`. The new
module owns generic command request/outcome types, bounded output capture,
timeout, process-group termination, and the default subprocess implementation.
`sync.py` imports the generic runner types and continues to pass no timeout.

Because fleetman is at version 0.1 and this is an internal seam, tests and fakes
should be migrated directly rather than preserving a weak callable signature
through factory indirection.

### `src/fleetman/run.py` — pure planning and serial orchestration

This module owns:

- selector validation;
- safe marker-path validation;
- project selection and ordering;
- `RunPlan`, `RunStatus`, `RunResult`, and aggregate result models;
- serial execution through an injected runner;
- halt-on-fail bookkeeping.

It does not print or raise `typer.Exit`.

### `src/fleetman/cli.py` — parsing and presentation

The CLI command owns:

- root resolution and root validation;
- Typer options and arguments;
- translating model/domain validation errors to exit 3;
- plan/result rendering;
- quiet/verbose behavior;
- final exit selection;
- interruption handling.

### Documentation

- `README.md` adds the command and its explicit-scope rule.
- `skills/fleetman/SKILL.md` documents dry-run-first fleet maintenance with the
  copyroom example.
- No fixed devenv task is added because arbitrary command argv does not map well
  to a named no-argument task.

## Worked examples

```bash
# Preview and update every project with a copyroom marker.
fleetman run --if .copier-answers.yml --dry-run -- copyroom update
fleetman run --if .copier-answers.yml -- copyroom update

# Run tests in every project classified as Python.
fleetman run --kind python -- pytest -q

# Check all Nix projects, stopping at the first failure.
fleetman run --kind nix --halt-on-fail -- nix flake check

# Inspect every harvested project; --all is explicit.
fleetman run --all -- git status --short

# Select either man or dantic family projects, then require pyproject.toml.
fleetman run --family man --family dantic --if pyproject.toml -- pytest -q

# Deliberately request shell syntax.
fleetman run --all -- sh -c 'test -f README.md && wc -l README.md'
```

## Validation and done criteria

The feature is complete when all of the following hold:

- Existing tests remain green after the runner extraction.
- Pure selector tests prove OR-within/AND-across semantics and deterministic
  ordering.
- Unsafe marker paths and operational symlink escapes are rejected.
- Dry-run makes zero runner calls and accurately renders resolved paths/argv.
- Real subprocess tests prove cwd, exact exit code, combined output, bounded
  capture, timeout, and descendant cleanup.
- CLI tests prove `--` passthrough, explicit-scope validation, incompatible
  options, exit codes, halt summaries, and no-match behavior.
- Ctrl-C stops the active process group and prevents later projects from running.
- Quiet and verbose reporting follow their contracts.
- A live copyroom-marker dry-run matches a direct filesystem predicate over the
  current harvested fleet; no permanent numeric count is asserted.
- README and the packaged fleetman skill describe the same syntax and safety
  contract as the CLI.
- `pytest` passes without network access.

## Future extensions

- `--jobs N`, with bounded concurrency and deterministic final result ordering.
- `--name` and glob selectors.
- `--if-not`.
- `--save` for structured results and complete logs.
- Explicit `--interactive`/PTY behavior, likely restricted to one selected
  project unless a careful multiplexing design is adopted.
- `--devenv`, implemented as an explicit wrapper rather than an ambient default.
- Named sugar such as `fleetman update-templates` only after a repeated workflow
  proves that it deserves a stable domain command.
