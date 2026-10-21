import pytest

from .support import PluginRunner


@pytest.fixture
def plugin_runner(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> PluginRunner:
    # The subprocess must use its environment's pytest and only our plugin.
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.delenv("PYTEST_PLUGINS", raising=False)
    monkeypatch.setenv("TESTINEL_DSN", "https://ingest.example.test/events")
    return PluginRunner(pytester)
