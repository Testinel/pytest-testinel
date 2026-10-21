# Changelog

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

[0.9.1]: https://github.com/Testinel/pytest-testinel/compare/0.9.0...0.9.1
[0.9.0]: https://github.com/Testinel/pytest-testinel/compare/0.8.5...0.9.0
