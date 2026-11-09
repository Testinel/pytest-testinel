# Changelog

## [0.11.1] — 2026-11-09

### Upgrade

Requires Python 3.11–3.16. Upgrade with either:

```sh
uv add --upgrade pytest-testinel
```

```sh
python -m pip install --upgrade pytest-testinel
```

Changes since [0.9.1](https://github.com/Testinel/pytest-testinel/releases/tag/0.9.1).

### User-facing changes

#### Behavior changes and compatibility

- **Diagnostics and event payloads are now bounded.** Large rendered values,
  tracebacks, captured text, descriptions, and run metadata may be truncated or
  omitted to fit the configured limits. Visible markers and reduction metadata
  identify lost detail; test identities, outcomes, and attachment references are
  preserved.
- **Reporting is disabled for the run when the complete test inventory cannot
  fit or a test ID exceeds the backend's 1024-character limit.** A warning
  explains the failure; tests continue with their normal exit status. Oversized
  phase events are not sent. HTTP 413 remains a delivery failure that allows
  later reporting; HTTP 404 retains its existing disable behavior.
- Negotiate compact diagnostics v2 when the server explicitly advertises support.
  Older servers and file/custom backends continue to receive bounded v1 by
  default. Consumers of raw diagnostic snapshots should allow missing details
  and reduction metadata.

#### Configuration

- Add `TESTINEL_MAX_EVENT_BYTES` (default 786432, minimum 16384),
  `TESTINEL_MAX_VALUE_BYTES` (default 4096), and
  `TESTINEL_MAX_FRAME_LOCALS_BYTES` (default 16384). Invalid overrides produce a
  warning and fall back to defaults. After the start response, events honor the
  lower of the configured and advertised server ceilings.

#### Security and dependency updates

- No security-specific fix or advisory is documented in these changes.
  Runtime dependency requirements remain `pytest>=7` and `requests>=2`.
- Update the locked development tools Ruff from 0.15.22 to 0.17.4 and ty from
  0.0.60 to 0.0.99. These are development dependency updates, not runtime pins.

### Developer-facing changes

#### Reporting contract

- Replace recursive pytest dataclass snapshots with bounded extraction of known
  pytest 7–9 reporting fields. Reuse rendered argument/local values without
  evaluating them again.
- Diagnostics v2 stores the newest exception and frames once, with earlier
  causes, assertion details, captured text, and reduction counters. Preserve
  null exceptions for strict XPASS; retain ExceptionGroup output as bounded text.
- Check the entire encoded JSON body, including event/request IDs and JSON
  escaping, before sending those exact bytes as `application/json`. Log byte
  counts and reduction summaries at `DEBUG` for delivery diagnostics.
- Deprecate legacy `repr_info` snapshots, duplicate traceback representations,
  and unbounded locals/arguments in favor of v2. Legacy server input/read support
  has no removal date. Offline tooling can opt into v2 with
  `ResultsReporter(dsn, diagnostic_schema_version=2)`; file limits apply per event.
- Document the [diagnostic protocol](doc/diagnostic-protocol.md), add shared v1/v2
  golden fixtures, and provide a contract synchronization check against the
  adjacent Testinel-web checkout.

#### Testing and tooling

- Add regression coverage for exact encoded byte limits, UTF-8 truncation,
  inventory failures, capability negotiation, offline schema selection, and
  subprocess reporting of chained exceptions using v2.
- Add `tox-uv` to the development dependencies and tox requirements. Simplify
  isolated build requirements to `uv_build>=0.13.7,<0.14.0`.

## [0.9.1] — 2026-10-21

### Upgrade

Requires Python 3.11–3.16. Upgrade with either:

```sh
uv add --upgrade pytest-testinel
```

```sh
python -m pip install --upgrade pytest-testinel
```

Changes since [0.9.0](https://github.com/Testinel/pytest-testinel/releases/tag/0.9.0).

### Security and dependency updates

- Update the locked `certifi` dependency from 2026.6.17 to 2026.10.19. This is a
  lockfile update; the package's runtime dependency requirements remain
  `pytest>=7` and `requests>=2`, so installing the published plugin does not
  enforce this exact transitive dependency version.

## [0.9.0] — 2026-10-21

### Upgrade

Requires Python 3.11–3.16. Upgrade with either:

```sh
uv add --upgrade pytest-testinel
```

```sh
python -m pip install --upgrade pytest-testinel
```

Changes since [0.8.5](https://github.com/Testinel/pytest-testinel/releases/tag/0.8.5).

### User-facing changes

#### Breaking changes

- **Python 3.10 is no longer supported.** The Python requirement changes from
  `>=3.10` to `>=3.11,<3.17`. Upgrade to Python 3.11–3.16 before installing 0.9.0;
  Python 3.17 and later are also excluded by the new upper bound.
- **Reporting stops for the rest of the run after an event submission returns
  HTTP 404.** Previously, later submissions were still attempted. Correct
  `TESTINEL_DSN` or restore project availability, then start a new pytest process
  to resume reporting.

#### Added and changed

- Add UUID event and request IDs to help correlate reported results with server
  logs. IDs are logged at `DEBUG` before HTTP JSON requests are sent, including
  requests that fail during transport. Local JSON results also include event IDs.
- When an ingest 404 disables reporting, show one terminal warning and one
  `WARNING` log record. Tests continue with their normal exit status. Further
  events and new attachments are suppressed, and queued uploads are discarded;
  an upload already in progress may finish. Attachment-link and storage-upload
  404s do not disable reporting, and other request failures retain their existing
  best-effort behavior.

#### Fixed

- Fix a plugin crash when a test marked `xfail(strict=True)` unexpectedly passes
  (strict XPASS). Preserve pytest's failed outcome and exit status, and report the
  failure message even though pytest supplies no exception for this result.

#### Security and dependency updates

- Update the locked `urllib3` dependency from 2.7.0 to 2.8.0 and the locked `pip`
  dependency from 26.1.2 to 26.2. These are lockfile updates; the package's runtime
  dependency requirements remain `pytest>=7` and `requests>=2`, so installing the
  published plugin does not enforce these exact transitive dependency versions.
- No security-specific fix or advisory is documented in the changes since 0.8.5.

### Developer-facing changes

#### Reporting contract

- Every event emitted by `ResultsReporter`, including start/end and local-file
  events, now has a UUID `event_id`. HTTP event submissions and upload-link
  requests receive a fresh UUID `request_id` in both the JSON body and the
  `X-Request-ID` header. Consumers with strict JSON schemas must accept these new
  fields. Resending the same event through the HTTP backend preserves its
  `event_id` and generates a new `request_id`.
- Binary attachment uploads keep the storage provider's headers and body;
  correlation IDs are added only to HTTP JSON requests.
- Strict XPASS reports store their message in `error_info.repr_info.message`,
  with a null exception and an empty traceback.

#### Testing and tooling

- Add an 18-environment tox matrix for Python 3.11–3.16 and pytest 7, 8, and 9.
  Each environment installs the package as a wheel and clears `TESTINEL_DSN`.
  Missing interpreters fail the matrix instead of being skipped. Python 3.16
  validation uses an alpha interpreter; see [TESTING.md](TESTING.md) for the
  recorded validation and coverage limits.
- Add isolated subprocess compatibility tests for outcomes, skips and xfail,
  traceback serialization, collection, terminal output, and session finalization,
  plus regression tests for ingest 404 handling and correlation IDs.
- Add Poe tasks: `uv run poe test`, `uv run poe tox`, `uv run poe lint`, and
  `uv run poe check`. The lint task formats code, applies Ruff fixes, and runs
  mypy; the check task runs lint followed by tests.
- Document the testing plan and require comments explaining compatibility
  workarounds and their affected runtime or dependency versions.

[0.11.0]: https://github.com/Testinel/pytest-testinel/compare/0.9.1...0.11.0
[0.9.1]: https://github.com/Testinel/pytest-testinel/compare/0.9.0...0.9.1
[0.9.0]: https://github.com/Testinel/pytest-testinel/compare/0.8.5...0.9.0
