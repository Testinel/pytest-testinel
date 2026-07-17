## Official Testinel plugin for pytest

Testinel’s pytest plugin captures structured Selenium and Playwright test execution data directly from pytest and sends it to Testinel, where your test results become searchable, comparable, and actually useful. No log scraping. No brittle CI hacks. Just deterministic test analytics.

## 📦 Getting Started
### Prerequisites

You need a Testinel [account](https://testinel.dev/accounts/signup/?next=/projects/) and [project](https://testinel.dev/projects/).

### Installation

Getting Testinel into your project is straightforward. Just run this command in your terminal:

```
pip install --upgrade pytest-testinel
```

Or with `uv`:

```
uv add pytest-testinel
```

### Configuration

Set Testinel reporter DSN environment variable `TESTINEL_DSN`. Get DSN for your project at [https://testinel.dev/projects/](https://testinel.dev/projects/).

Examples:

```
# Report to Testinel (HTTPS)
export TESTINEL_DSN="https://your.testinel.endpoint/ingest/xxxxxxxxxxxx/"
```

```
# Report to a local file (JSON)
export TESTINEL_DSN="file:///tmp/testinel-results.json"

# Or use a direct file path
export TESTINEL_DSN="./testinel-results.json"
```

### Logging

Testinel uses pytest's standard logging controls.

To set the logging level to `INFO` for all pytest loggers and show the messages
live, run:

```bash
pytest --log-level=INFO --log-cli-level=INFO
```

To change the level for pytest-testinel only, set its parent logger in your
project's `conftest.py`:

```python
import logging


def pytest_configure() -> None:
    logging.getLogger("pytest_testinel").setLevel(logging.INFO)
```

To also show these messages live, enable pytest's live logging:

```bash
pytest -o log_cli=true
```

Other loggers keep their existing levels. Replace `INFO` with `DEBUG`,
`WARNING`, or another standard Python logging level as needed.

Use pytest's `--log-file`, `--log-format`, and related options to control log
storage and formatting.

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
