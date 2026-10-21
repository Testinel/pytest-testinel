import logging
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from pytest_testinel import testinel
from pytest_testinel.results_reporter import INGEST_NOT_FOUND_WARNING, ResultsReporter


class DummyTerminalReporter:
    def __init__(self, *, no_summary: bool = False) -> None:
        self.lines: list[str] = []
        self.no_summary = no_summary

    def write_sep(self, separator: str, title: str) -> None:
        self.lines.append(f"{separator * 3} {title} {separator * 3}")

    def write_line(self, message: str) -> None:
        self.lines.append(message)


class DummyPluginManager:
    def __init__(self, terminal_reporter: DummyTerminalReporter | None) -> None:
        self.terminal_reporter = terminal_reporter

    def get_plugin(self, name: str) -> DummyTerminalReporter | None:
        if name == "terminalreporter":
            return self.terminal_reporter
        return None


class DummyConfig:
    def __init__(self, terminal_reporter: DummyTerminalReporter | None) -> None:
        self.pluginmanager = DummyPluginManager(terminal_reporter)
        self.stash = pytest.Stash()
        self.args = ["tests"]
        self.option = SimpleNamespace(no_summary=False)


class DummyResultsReporter:
    def __init__(self, run_web_url: str | None) -> None:
        self.tests: list[dict[str, Any]] = []
        self.run_web_url = run_web_url
        self.disabled_reason: str | None = None
        self.start_payload: dict[str, Any] | None = None
        self.end_calls = 0

    def report_start(self, payload: dict[str, Any]) -> str | None:
        self.start_payload = payload
        return self.run_web_url

    def report_end(self) -> str | None:
        self.end_calls += 1
        return self.run_web_url

    def report_event(self, event: str, payload: dict[str, Any]) -> None:
        return


@pytest.fixture(autouse=True)
def reset_final_run_web_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testinel, "_final_run_web_url", None)


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
        "=== Testinel live run ===",
        "View live report: https://host/projects/project-slug/runs/run-uuid/"
        "?utm_source=pytest-testinel&utm_medium=cli",
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
        "=== Testinel report ===",
        "View run report: https://host/projects/project-slug/runs/run-uuid/"
        "?utm_source=pytest-testinel&utm_medium=cli",
    ]


def test_write_run_web_url_falls_back_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = DummyConfig(None)

    testinel._write_run_web_url(  # noqa: SLF001
        config,  # type: ignore[arg-type]
        "https://host/run/",
        "end",
    )

    assert capsys.readouterr().err == (
        "=== Testinel report ===\n"
        "View run report: https://host/run/"
        "?utm_source=pytest-testinel&utm_medium=cli\n"
    )


def test_collection_finish_starts_reporting_after_collecting_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_reporter = DummyTerminalReporter()
    config = DummyConfig(terminal_reporter)
    session = SimpleNamespace(config=config, items=[object(), object()])
    results_reporter = DummyResultsReporter("https://host/run/")
    monkeypatch.setattr(testinel, "_get_test_reporter", lambda: results_reporter)
    monkeypatch.setattr(
        testinel,
        "to_test_dict",
        lambda item: {"test_id": str(id(item))},
    )

    testinel.pytest_collection_finish(session)  # type: ignore[arg-type]

    assert len(results_reporter.tests) == 2
    assert results_reporter.start_payload is not None
    assert results_reporter.start_payload["args"] == ["tests"]
    assert terminal_reporter.lines[0] == "=== Testinel live run ==="


def test_terminal_summary_outputs_final_url_after_other_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_reporter = DummyTerminalReporter()
    config = DummyConfig(terminal_reporter)
    monkeypatch.setattr(testinel, "_final_run_web_url", "https://host/run/")
    summary_hook = testinel.pytest_terminal_summary(config)  # type: ignore[arg-type]

    next(summary_hook)
    terminal_reporter.lines.append("other summary")
    with pytest.raises(StopIteration):
        next(summary_hook)

    assert terminal_reporter.lines == [
        "other summary",
        "=== Testinel report ===",
        "View run report: https://host/run/?utm_source=pytest-testinel&utm_medium=cli",
    ]
    assert testinel._final_run_web_url is None  # noqa: SLF001


def test_session_finish_defers_final_url_to_terminal_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_reporter = DummyTerminalReporter()
    config = DummyConfig(terminal_reporter)
    session = SimpleNamespace(config=config)
    results_reporter = DummyResultsReporter("https://host/run/")
    monkeypatch.setattr(testinel, "_get_test_reporter", lambda: results_reporter)

    testinel.pytest_sessionfinish(session, pytest.ExitCode.OK)  # type: ignore[arg-type]

    assert results_reporter.end_calls == 1
    assert terminal_reporter.lines == []
    assert testinel._final_run_web_url == "https://host/run/"  # noqa: SLF001


def test_session_finish_outputs_final_url_when_summary_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_reporter = DummyTerminalReporter(no_summary=True)
    config = DummyConfig(terminal_reporter)
    session = SimpleNamespace(config=config)
    results_reporter = DummyResultsReporter("https://host/run/")
    monkeypatch.setattr(testinel, "_get_test_reporter", lambda: results_reporter)

    testinel.pytest_sessionfinish(session, pytest.ExitCode.OK)  # type: ignore[arg-type]

    assert terminal_reporter.lines == [
        "=== Testinel report ===",
        "View run report: https://host/run/?utm_source=pytest-testinel&utm_medium=cli",
    ]
    assert testinel._final_run_web_url is None  # noqa: SLF001


def test_session_finish_falls_back_to_stderr_without_terminal_reporter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = DummyConfig(None)
    session = SimpleNamespace(config=config)
    results_reporter = DummyResultsReporter("https://host/run/")
    monkeypatch.setattr(testinel, "_get_test_reporter", lambda: results_reporter)

    testinel.pytest_sessionfinish(session, pytest.ExitCode.OK)  # type: ignore[arg-type]

    assert capsys.readouterr().err == (
        "=== Testinel report ===\n"
        "View run report: https://host/run/"
        "?utm_source=pytest-testinel&utm_medium=cli\n"
    )
    assert testinel._final_run_web_url is None  # noqa: SLF001


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


@pytest.mark.parametrize("has_terminal", [True, False])
def test_reporting_warning_is_shown_once_per_session(
    capsys: pytest.CaptureFixture[str], has_terminal: bool
) -> None:
    terminal_reporter = DummyTerminalReporter() if has_terminal else None
    config = DummyConfig(terminal_reporter)
    reporter = DummyResultsReporter(None)
    reporter.disabled_reason = INGEST_NOT_FOUND_WARNING

    for _ in range(3):
        testinel._write_reporting_warning(
            cast(pytest.Config, config), cast(ResultsReporter, reporter)
        )

    message = f"WARNING: {INGEST_NOT_FOUND_WARNING}"
    if terminal_reporter is not None:
        assert terminal_reporter.lines == [message]
        assert capsys.readouterr().err == ""
    else:
        assert capsys.readouterr().err == f"{message}\n"

    next_config = DummyConfig(terminal_reporter)
    testinel._write_reporting_warning(
        cast(pytest.Config, next_config), cast(ResultsReporter, reporter)
    )
    if terminal_reporter is not None:
        assert terminal_reporter.lines == [message, message]
    else:
        assert capsys.readouterr().err == f"{message}\n"


@pytest.mark.parametrize(
    ("rejected_event", "test_passes", "has_terminal", "expected_events"),
    [
        ("start", True, True, ["start"]),
        ("start", False, True, ["start"]),
        ("setup", True, True, ["start", "setup"]),
        ("call", False, True, ["start", "setup", "call"]),
        ("end", True, True, ["start", "setup", "call", "teardown", "end"]),
        ("start", True, False, ["start"]),
    ],
)
def test_ingest_404_warning_in_real_pytest_session(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    rejected_event: str,
    test_passes: bool,
    has_terminal: bool,
    expected_events: list[str],
) -> None:
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv(
        "TESTINEL_DSN", "https://secret.example.test/ingest/private-dsn/"
    )
    pytester.makeconftest(
        """
import json as json_module
from pathlib import Path
from typing import Any

import requests
from requests import Response

events: list[str] = []

def post(url: str, *, json: dict, **kwargs: Any) -> Response:
    events.append(json["event"])
    Path("events.json").write_text(json_module.dumps(events))
    response = Response()
    response.status_code = 404 if json["event"] == REJECTED_EVENT else 200
    response.url = url
    response.reason = "private reason"
    response._content = b'{"run_web_url": "https://host/run/", "detail": "private body"}'
    return response

def pytest_configure() -> None:
    requests.post = post
""".replace("REJECTED_EVENT", repr(rejected_event))
    )
    pytester.makepyfile(f"def test_example() -> None:\n    assert {test_passes!r}\n")
    args = [
        "-p",
        "pytest_testinel.testinel",
        "--log-file=reporting.log",
        "--log-file-level=WARNING",
        "--log-level=CRITICAL",
    ]
    if not has_terminal:
        args.extend(["-p", "no:terminal"])

    result = pytester.runpytest_subprocess(*args)

    assert result.ret == (
        pytest.ExitCode.OK if test_passes else pytest.ExitCode.TESTS_FAILED
    )
    output = result.stdout.str() if has_terminal else result.stderr.str()
    assert output.count(f"WARNING: {INGEST_NOT_FOUND_WARNING}") == 1
    assert "View run report:" not in output
    assert "private-dsn" not in output
    assert "private reason" not in output
    assert "private body" not in output
    assert "secret.example.test" not in output
    logs = (pytester.path / "reporting.log").read_text()
    assert logs.count(INGEST_NOT_FOUND_WARNING) == 1
    assert "private" not in logs
    assert "secret.example.test" not in logs
    assert json.loads((pytester.path / "events.json").read_text()) == expected_events
