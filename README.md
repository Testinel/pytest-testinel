## Official Testinel plugin for pytest

Testinel’s pytest plugin captures structured test execution data directly from pytest and sends it to Testinel, where your test results become searchable, comparable, and actually useful. No log scraping. No brittle CI hacks. Just deterministic test analytics.

## 📦 Getting Started
### Prerequisites

You need a Testinel [account](https://testinel.first.institute/accounts/signup/?next=/projects/) and [project](https://testinel.first.institute/projects/).

### Installation

Getting Testinel into your project is straightforward. Just run this command in your terminal:

```
pip install --upgrade pytest-testinel
```

### Configuration

Set Testinel reporter DSN environment variable `TESTINEL_DSN`.

Examples:

```
# Report to Testinel (HTTPS)
export TESTINEL_DSN="https://your.testinel.endpoint/ingest"
```

```
# Report to a local file (JSON)
export TESTINEL_DSN="file:///tmp/testinel-results.json"
```

```
# Or use a direct file path
export TESTINEL_DSN="./testinel-results.json"
```

Set Testinel plugin log level with `--testinel-log-level`.

Supported values:

- `DEBUG`
- `INFO`
- `WARNING` (default)
- `ERROR`
- `CRITICAL`

Example:

```bash
pytest --testinel-log-level=INFO
```

### Recommended pytest flags

For better debugging and richer failure context, it is highly recommended to run pytest with:

`--showlocals --tb=long -vv`

Why:

- `--showlocals`: includes local variable values in tracebacks, which makes root-cause analysis much faster.
- `--tb=long`: shows full, non-truncated tracebacks so you can see complete failure paths.
- `-vv`: increases verbosity, showing more detailed test identifiers and execution output.

Example:

```bash
pytest --showlocals --tb=long -vv
```

### Recommended Playwright flags

If you run browser tests with `pytest-playwright`, these flags provide better artifacts for debugging:

`--tracing=retain-on-failure --video=retain-on-failure --screenshot=only-on-failure --output=test-results`

Why:

- `--tracing=retain-on-failure`: captures a full Playwright trace for failed tests only.
- `--video=retain-on-failure`: keeps video recordings only for failed tests.
- `--screenshot=only-on-failure`: saves screenshots at failure time.
- `--output=test-results`: stores artifacts in a predictable directory.

Example:

```bash
pytest --tracing=retain-on-failure --video=retain-on-failure --screenshot=only-on-failure --output=test-results
```
