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
