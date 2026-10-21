# Testing plan

The unit suite checks reporting and transport logic. The standalone compatibility
suite in `tests/compatibility/` checks the plugin against real pytest sessions and
report objects in subprocesses. Both suites run in the existing tox matrix:
Python 3.11–3.16 × pytest 7, 8, and 9, using wheel installations.

## P1 — Implemented: core pytest compatibility

- Passing tests, assertion failures, setup errors, and teardown errors: verify
  phase order, outcomes, error details, process exit status, and exactly one
  start/end event.
- Marked and runtime skips, xfail, non-strict XPASS, and strict XPASS: preserve
  pytest outcomes and exit status without plugin internal errors.
- Traceback modes `long`, `short`, `line`, and `no`, plus `--showlocals`: exercise
  serialization of actual pytest reports, chained exceptions, and exception notes.

## P2 — Implemented: collection and session behavior

- Functions, class methods, and parametrized tests: verify collected IDs,
  locations, descriptions, and association with emitted phase events.
- Normal terminal, `--no-summary`, and disabled terminal: verify live/final URL
  output, placement, and absence of duplicates.
- Empty collection, collection errors, and `-x`: verify exit status and clean
  reporter finalization, including collected tests that never execute.

## P3 — Not implemented yet

- Attachment discovery, deduplication, and completion of uploads before run end.
- Installed plugin entry-point autoload without explicit `-p` loading.
- Unconfigured runs without `TESTINEL_DSN`, including absence of reporting traffic.

Existing unit coverage of parts of these behaviors does not count as P3
subprocess compatibility coverage.

## Running the suites

```sh
# Standalone subprocess compatibility suite
uv run pytest tests/compatibility

# Complete suite
uv run pytest tests

# Compatibility suite across the entire Python/pytest matrix
uv run tox -- tests/compatibility

# One wheel-installed environment
uv run tox -e py311-pytest7 -- tests/compatibility
```

Each compatibility case starts a fresh process using the current interpreter and
its pytest installation. Third-party plugin autoload and inherited pytest options
are disabled; our plugin is loaded explicitly. Only HTTP transport is replaced,
at `requests.Session.send`, by a deterministic response and JSON-lines recorder.
Pytest hooks, report construction, serialization, and `ResultsReporter` remain
real. The suite requires no browser, credentials, live Testinel service, or network.

Assertions target event contracts, stable error fields, and output placement,
rather than snapshots of pytest's complete formatted tracebacks. A passing matrix
validates these scenarios only; P3 and third-party plugin combinations remain
outside this suite's coverage.

## Validation snapshot — 2026-10-21

The suite adds 21 subprocess cases. From an isolated checkout, the complete suite
passed 80 tests in each of all 18 tox environments (Python 3.11–3.16 × pytest 7/8/9),
using wheel installations.
Python 3.16 was the installed `3.16.0a1` interpreter; this is alpha-version evidence.
Ruff checks, formatting, mypy, and `uv lock --check` also passed.

The strict XPASS case exposed a plugin crash: pytest can produce a failed report
with no exception and a string failure representation. The reporting hook now
retains that message under `error_info.repr_info.message`, with a null exception
and empty traceback, while preserving pytest's normal failed-test exit status.
