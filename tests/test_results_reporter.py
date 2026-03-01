import json
from pathlib import Path

import pytest

from pytest_testinel import results_reporter
from pytest_testinel.results_reporter import (
    FileReportingBackend,
    HttpReportingBackend,
    ResultsReporter,
)


class DummyBackend(results_reporter.ReportingBackend):
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.started = False
        self.ended = False

    def record_event(self, event: dict) -> None:
        self.events.append(event)

    def on_start(self) -> None:
        self.started = True

    def on_end(self) -> None:
        self.ended = True


def test_results_reporter_uses_explicit_backend() -> None:
    backend = DummyBackend()
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)

    reporter.tests = [{"test_id": "a::b::c"}]
    reporter.report_start(payload={"k": "v"})
    reporter.report_end()

    assert reporter.backend is backend
    assert backend.started is True
    assert backend.ended is True
    assert backend.events[0]["event"] == "start"
    assert backend.events[0]["sdk"]["name"] == "testinel.pytest"
    assert "version" in backend.events[0]["sdk"]
    assert backend.events[0]["tests"] == reporter.tests
    assert backend.events[1]["event"] == "end"
    assert "sdk" not in backend.events[1]
    assert backend.events[0]["run_id"] == backend.events[1]["run_id"]


def test_results_reporter_http_backend_posts_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_post(url: str, json: dict, headers: dict, verify: bool) -> None:
        calls.append({"url": url, "json": json, "headers": headers, "verify": verify})

    monkeypatch.setattr(results_reporter.requests, "post", fake_post)

    reporter = ResultsReporter(dsn="https://example.test/ingest")
    reporter.report_event(event="call", payload={"ok": True})

    assert isinstance(reporter.backend, HttpReportingBackend)
    assert calls[0]["url"] == "https://example.test/ingest"
    assert calls[0]["json"]["event"] == "call"
    assert "sdk" not in calls[0]["json"]
    assert calls[0]["headers"]["User-Agent"].startswith("testinel.pytest/")
    assert calls[0]["headers"]["X-Testinel-Client"].startswith("testinel.pytest/")
    assert calls[0]["verify"] is False


def test_results_reporter_file_backend_writes_json(tmp_path: Path) -> None:
    output_file = tmp_path / "results.json"
    reporter = ResultsReporter(dsn=f"file://{output_file.as_posix()}")

    reporter.report_start(payload={"run": 1})
    reporter.report_end()

    assert isinstance(reporter.backend, FileReportingBackend)
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["event"] == "start"
    assert data[1]["event"] == "end"
    assert data[0]["run_id"] == data[1]["run_id"]


def test_results_reporter_plain_path_uses_file_backend(tmp_path: Path) -> None:
    output_file = tmp_path / "results.json"
    reporter = ResultsReporter(dsn=str(output_file))

    assert isinstance(reporter.backend, FileReportingBackend)
    assert reporter.backend.filename == str(output_file)


def test_results_reporter_file_dsn_with_host_is_invalid() -> None:
    with pytest.raises(ValueError, match="host component"):
        ResultsReporter(dsn="file://host/path/to/file.json")


def test_results_reporter_unsupported_scheme_is_invalid() -> None:
    with pytest.raises(ValueError, match="Unsupported TESTINEL_DSN scheme"):
        ResultsReporter(dsn="ftp://example.test/results.json")
