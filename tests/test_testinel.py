import logging

import pytest

from pytest_testinel import testinel


class DummyTerminalReporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, message: str) -> None:
        self.lines.append(message)


class DummyPluginManager:
    def __init__(self, terminal_reporter: DummyTerminalReporter) -> None:
        self.terminal_reporter = terminal_reporter

    def get_plugin(self, name: str) -> DummyTerminalReporter | None:
        if name == "terminalreporter":
            return self.terminal_reporter
        return None


class DummyConfig:
    def __init__(self, terminal_reporter: DummyTerminalReporter) -> None:
        self.pluginmanager = DummyPluginManager(terminal_reporter)


def test_plugin_uses_pytest_native_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pytest_testinel")

    testinel.logger.info("native logging")

    assert caplog.record_tuples == [
        ("pytest_testinel.testinel", logging.INFO, "native logging")
    ]
    assert testinel.logger.handlers == []
    assert testinel.logger.propagate is True


def test_plugin_does_not_register_custom_log_level_option() -> None:
    assert not hasattr(testinel, "pytest_addoption")


def test_write_run_web_url_outputs_start_message() -> None:
    terminal_reporter = DummyTerminalReporter()
    config = DummyConfig(terminal_reporter)

    testinel._write_run_web_url(  # noqa: SLF001
        config,  # type: ignore[arg-type]
        "https://host/projects/project-slug/runs/run-uuid/",
        "start",
    )

    assert terminal_reporter.lines == [
        "Testinel: watch this test run at https://host/projects/project-slug/runs/run-uuid/"
        "?utm_source=pytest-testinel&utm_medium=cli"
    ]


def test_write_run_web_url_outputs_end_message() -> None:
    terminal_reporter = DummyTerminalReporter()
    config = DummyConfig(terminal_reporter)

    testinel._write_run_web_url(  # noqa: SLF001
        config,  # type: ignore[arg-type]
        "https://host/projects/project-slug/runs/run-uuid/",
        "end",
    )

    assert terminal_reporter.lines == [
        "Testinel: test run finished. View it at https://host/projects/project-slug/runs/run-uuid/"
        "?utm_source=pytest-testinel&utm_medium=cli"
    ]


def test_add_run_web_url_utm_params_preserves_existing_query() -> None:
    run_web_url = testinel._add_run_web_url_utm_params(  # noqa: SLF001
        "https://host/projects/project-slug/runs/run-uuid/?tab=failed#details"
    )

    assert (
        run_web_url == "https://host/projects/project-slug/runs/run-uuid/"
        "?tab=failed&utm_source=pytest-testinel&utm_medium=cli#details"
    )


def test_add_run_web_url_utm_params_replaces_existing_utm_values() -> None:
    run_web_url = testinel._add_run_web_url_utm_params(  # noqa: SLF001
        "https://host/projects/project-slug/runs/run-uuid/"
        "?utm_source=old&utm_medium=old&tab=failed"
    )

    assert (
        run_web_url == "https://host/projects/project-slug/runs/run-uuid/"
        "?tab=failed&utm_source=pytest-testinel&utm_medium=cli"
    )
