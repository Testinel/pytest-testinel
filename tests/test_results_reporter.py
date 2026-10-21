import json
import logging
import threading
from pathlib import Path
from typing import Any

import pytest
import requests
from requests import Response

from pytest_testinel import http_reporting_backend, reporting_backend
from pytest_testinel.results_reporter import (
    INGEST_NOT_FOUND_WARNING,
    ResultsReporter,
)
from pytest_testinel.file_reporting_backend import FileReportingBackend
from pytest_testinel.http_reporting_backend import HttpReportingBackend
from pytest_testinel.http_reporting_backend import EVENT_TIMEOUT, UPLOAD_TIMEOUT


class DummyBackend(reporting_backend.ReportingBackend):
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.started = False
        self.ended = False
        self.responses: list[dict | None] = []

    def record_event(self, event: dict) -> dict | None:
        self.events.append(event)
        if self.responses:
            return self.responses.pop(0)
        return None

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


def test_results_reporter_captures_run_web_url_from_start_response() -> None:
    backend = DummyBackend()
    backend.responses.append(
        {
            "test_run_id": "run-uuid",
            "run_web_url": "https://host/projects/project-slug/runs/run-uuid/",
        }
    )
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)

    run_web_url = reporter.report_start(payload={})

    assert run_web_url == "https://host/projects/project-slug/runs/run-uuid/"
    assert reporter.run_web_url == "https://host/projects/project-slug/runs/run-uuid/"


def test_results_reporter_ignores_start_response_without_run_web_url() -> None:
    backend = DummyBackend()
    backend.responses.append({"test_run_id": "run-uuid"})
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)

    run_web_url = reporter.report_start(payload={})

    assert run_web_url is None
    assert reporter.run_web_url is None


@pytest.mark.parametrize("run_web_url", [None, "", 123, [], {}])
def test_results_reporter_ignores_invalid_run_web_url_values(
    run_web_url: object,
) -> None:
    backend = DummyBackend()
    backend.responses.append({"run_web_url": run_web_url})
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)

    captured_url = reporter.report_start(payload={})

    assert captured_url is None
    assert reporter.run_web_url is None


def test_results_reporter_reuses_start_run_web_url_on_end() -> None:
    backend = DummyBackend()
    backend.responses.extend(
        [
            {
                "test_run_id": "run-uuid",
                "run_web_url": "https://host/projects/project-slug/runs/run-uuid/",
            },
            None,
        ]
    )
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)
    reporter.report_start(payload={})

    run_web_url = reporter.report_end()

    assert run_web_url == "https://host/projects/project-slug/runs/run-uuid/"


def test_results_reporter_http_backend_posts_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_post(
        url: str,
        json: dict,
        headers: dict,
        allow_redirects: bool,
        timeout: tuple[int, int],
    ) -> Response:
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "allow_redirects": allow_redirects,
                "timeout": timeout,
            }
        )
        response = Response()
        response.status_code = 200
        response._content = b""
        return response

    monkeypatch.setattr(http_reporting_backend.requests, "post", fake_post)

    reporter = ResultsReporter(dsn="https://example.test/ingest")
    reporter.report_event(event="call", payload={"ok": True})

    assert isinstance(reporter.backend, HttpReportingBackend)
    assert calls[0]["url"] == "https://example.test/ingest"
    assert calls[0]["json"]["event"] == "call"
    assert "sdk" not in calls[0]["json"]
    assert calls[0]["headers"]["User-Agent"].startswith("testinel.pytest/")
    assert calls[0]["headers"]["X-Testinel-Client"].startswith("testinel.pytest/")
    assert calls[0]["allow_redirects"] is True
    assert calls[0]["timeout"] == EVENT_TIMEOUT


def test_http_backend_rejects_unsuccessful_event_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Response()
    response.status_code = 500
    response.reason = "Internal Server Error"

    monkeypatch.setattr(
        http_reporting_backend.requests, "post", lambda *a, **k: response
    )

    backend = HttpReportingBackend("https://secret.example.test/ingest")

    with pytest.raises(requests.HTTPError):
        backend.record_event({"event": "call"})


def test_http_backend_uses_upload_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attachment = tmp_path / "attachment.txt"
    attachment.write_text("content", encoding="utf-8")
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs: object) -> Response:
        calls.append({"method": method, "url": url, **kwargs})
        response = Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(http_reporting_backend.requests, "request", fake_request)
    backend = HttpReportingBackend("https://example.test/ingest")

    backend.upload_file(
        "https://uploads.example.test/signed",
        "PUT",
        {"Authorization": "secret"},
        str(attachment),
    )

    assert calls[0]["timeout"] == UPLOAD_TIMEOUT


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


def test_results_reporter_waits_for_uploads_before_end() -> None:
    class DummyQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object | None]] = []

        def join(self) -> None:
            self.calls.append(("join", None))

        def put(self, item: object) -> None:
            self.calls.append(("put", item))

    class DummyThread:
        def __init__(self) -> None:
            self.join_timeout: object = object()

        def join(self, timeout: float | None = None) -> None:
            self.join_timeout = timeout

    backend = DummyBackend()
    reporter = ResultsReporter(dsn="https://example.test/ingest", backend=backend)
    dummy_queue = DummyQueue()
    dummy_thread = DummyThread()
    reporter._upload_queue = dummy_queue  # type: ignore
    reporter._uploader = dummy_thread  # type: ignore

    reporter.report_end()

    assert dummy_queue.calls == [("put", None)]
    assert dummy_thread.join_timeout is None


def test_report_event_failure_is_logged_without_raising_or_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingBackend(DummyBackend):
        def record_event(self, event: dict) -> dict | None:
            raise RuntimeError("https://secret.example.test/ingest?token=secret")

    reporter = ResultsReporter("unused", backend=FailingBackend())
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")

    reporter.report_event("call", {"secret": "payload"})
    reporter.report_end()

    assert "Reporting event failed" in caplog.text
    assert "event='call'" in caplog.text
    assert "Event 'call' reported" not in caplog.text
    assert "secret.example.test" not in caplog.text
    assert "token=secret" not in caplog.text
    assert all(record.name.startswith("pytest_testinel.") for record in caplog.records)


@pytest.mark.parametrize(
    "transport_error",
    [requests.ConnectionError("connection reset"), requests.Timeout("timed out")],
)
def test_transport_failures_remain_warnings(
    caplog: pytest.LogCaptureFixture,
    transport_error: requests.RequestException,
) -> None:
    class TransportFailingBackend(DummyBackend):
        def record_event(self, event: dict) -> dict | None:
            raise transport_error

        def request_upload_link(self, filename: str) -> dict | None:
            raise transport_error

    reporter = ResultsReporter("unused", backend=TransportFailingBackend())
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")

    reporter.report_start({})
    reporter.report_event("call", {})
    reporter.report_attachment("screenshot.png")
    reporter.report_end()

    failure_records = [
        record for record in caplog.records if "failed" in record.getMessage()
    ]
    assert len(failure_records) == 4
    assert all(record.levelno == logging.WARNING for record in failure_records)


def test_http_failures_remain_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class HttpFailingBackend(DummyBackend):
        def record_event(self, event: dict) -> dict | None:
            response = Response()
            response.status_code = 503
            raise requests.HTTPError(response=response)

    reporter = ResultsReporter("unused", backend=HttpFailingBackend())
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")

    reporter.report_event("call", {})
    reporter.report_end()

    failure_records = [
        record for record in caplog.records if "failed" in record.getMessage()
    ]
    assert len(failure_records) == 2
    assert all(record.levelno == logging.WARNING for record in failure_records)


def test_report_end_runs_cleanup_after_delivery_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class EndFailingBackend(DummyBackend):
        def record_event(self, event: dict) -> dict | None:
            if event["event"] == "end":
                raise RuntimeError("delivery failed")
            return super().record_event(event)

    backend = EndFailingBackend()
    reporter = ResultsReporter("unused", backend=backend)
    caplog.set_level(logging.INFO, logger="pytest_testinel")

    reporter.report_end()

    assert backend.ended is True
    assert "Reporting end event failed" in caplog.text
    assert "Reporting ended." not in caplog.text


def test_report_start_and_cleanup_failures_are_best_effort(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LifecycleFailingBackend(DummyBackend):
        def on_start(self) -> None:
            raise RuntimeError("start failed")

        def on_end(self) -> None:
            raise RuntimeError("cleanup failed")

    reporter = ResultsReporter("unused", backend=LifecycleFailingBackend())
    caplog.set_level(logging.WARNING, logger="pytest_testinel")

    assert reporter.report_start({}) is None
    assert reporter.report_end() is None

    assert "Reporting start failed" in caplog.text
    assert "Reporting backend cleanup failed" in caplog.text


def test_upload_failure_logs_safe_metadata_only(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    secret_directory = tmp_path / "private-token"
    secret_directory.mkdir()
    attachment = secret_directory / "screenshot.png"
    attachment.write_bytes(b"image")

    class UploadFailingBackend(DummyBackend):
        def request_upload_link(self, filename: str) -> dict | None:
            return {
                "upload_url": "https://uploads.example.test/signed?token=secret",
                "method": "PUT",
                "headers": {"Authorization": "secret"},
            }

        def upload_file(
            self, upload_url: str, method: str, headers: dict, filename: str
        ) -> Response:
            response = Response()
            response.status_code = 503
            response.reason = "Unavailable"
            response.headers["X-Request-ID"] = "request-123"
            response._content = b"secret response body"
            return response

    reporter = ResultsReporter("unused", backend=UploadFailingBackend())
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")

    reporter.report_attachment(str(attachment))
    reporter.report_end()

    assert "status=503" in caplog.text
    assert "request-123" in caplog.text
    assert "response_size=20" in caplog.text
    assert "screenshot.png" in caplog.text
    assert "secret response body" not in caplog.text
    assert "private-token" not in caplog.text
    assert "uploads.example.test" not in caplog.text
    assert "Authorization" not in caplog.text


def test_malformed_upload_link_response_is_best_effort(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class MalformedBackend(DummyBackend):
        def request_upload_link(self, filename: str) -> dict | None:
            return ["unexpected"]  # type: ignore[return-value]

    reporter = ResultsReporter("unused", backend=MalformedBackend())
    caplog.set_level(logging.WARNING, logger="pytest_testinel")

    reporter.report_attachment("/private/path/screenshot.png")
    reporter.report_end()

    assert reporter.attachments == ["/private/path/screenshot.png"]
    assert "Requesting attachment upload link failed" in caplog.text
    assert "screenshot.png" in caplog.text
    assert "/private/path" not in caplog.text


def http_response(status: int, payload: dict | None = None) -> Response:
    response = Response()
    response.status_code = status
    response.url = "https://secret.example.test/ingest/private-dsn/"
    response.reason = "private reason"
    response._content = json.dumps(payload or {"detail": "private body"}).encode()
    return response


@pytest.mark.parametrize(
    ("rejected_event", "expected_events"),
    [
        ("start", ["start"]),
        ("call", ["start", "call"]),
        ("end", ["start", "call", "end"]),
    ],
)
def test_ingest_404_disables_reporter_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    rejected_event: str,
    expected_events: list[str],
) -> None:
    events: list[str] = []
    cleanup_calls: list[None] = []

    def post(url: str, *, json: dict, **kwargs: Any) -> Response:
        events.append(json["event"])
        return http_response(
            404 if json["event"] == rejected_event else 200,
            {"run_web_url": "https://host/run/"},
        )

    monkeypatch.setattr(http_reporting_backend.requests, "post", post)
    reporter = ResultsReporter("https://secret.example.test/ingest/private-dsn/")
    monkeypatch.setattr(reporter.backend, "on_end", lambda: cleanup_calls.append(None))
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")

    reporter.report_start({})
    reporter.report_event("call", {})
    if rejected_event != "end":
        reporter.report_attachment("not-opened.png")
    final_url = reporter.report_end()
    reporter.report_start({})
    reporter.report_event("teardown", {})
    reporter.report_attachment("not-opened-either.png")

    assert events == expected_events
    assert final_url is None
    assert reporter.disabled_reason == INGEST_NOT_FOUND_WARNING
    assert reporter.attachments == []
    assert cleanup_calls == [None]
    assert not reporter._uploader.is_alive()
    assert reporter._upload_queue.unfinished_tasks == 0
    warnings = [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]
    assert [record.getMessage() for record in warnings] == [INGEST_NOT_FOUND_WARNING]
    assert "https://testinel.dev/projects/" in caplog.text
    assert "private" not in caplog.text
    assert "secret.example.test" not in caplog.text
    assert "Reporting ended." not in caplog.text
    assert f"Event '{rejected_event}' reported." not in caplog.text


def test_disabled_reporter_discards_queued_uploads_after_inflight_upload_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    upload_started = threading.Event()
    release_upload = threading.Event()
    uploaded: list[str] = []
    attachment = tmp_path / "screenshot.png"
    attachment.write_bytes(b"image")

    def post(url: str, **kwargs: Any) -> Response:
        if url.endswith("attachment/upload-link/"):
            return http_response(200, {"upload_url": "https://storage.example.test/"})
        return http_response(404)

    def upload(method: str, url: str, **kwargs: Any) -> Response:
        uploaded.append(url)
        upload_started.set()
        if not release_upload.wait(timeout=5):
            raise TimeoutError("Test did not release upload")
        return http_response(200)

    monkeypatch.setattr(http_reporting_backend.requests, "post", post)
    monkeypatch.setattr(http_reporting_backend.requests, "request", upload)
    reporter = ResultsReporter("https://example.test/ingest/")
    try:
        reporter.report_attachment(str(attachment))
        assert upload_started.wait(timeout=5)
        reporter.report_attachment(str(attachment))
        reporter.report_attachment(str(attachment))
        reporter.report_event("call", {})
        reporter.report_attachment(str(attachment))
    finally:
        release_upload.set()
        reporter.report_end()

    assert uploaded == ["https://storage.example.test/"]
    assert reporter.attachments == []
    assert not reporter._uploader.is_alive()
    assert reporter._upload_queue.unfinished_tasks == 0


@pytest.mark.parametrize("failure_source", ["upload-link", "storage"])
def test_attachment_404_does_not_disable_reporting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_source: str
) -> None:
    events: list[str] = []
    uploads: list[str] = []
    attachment = tmp_path / "screenshot.png"
    attachment.write_bytes(b"image")

    def post(url: str, *, json: dict, **kwargs: Any) -> Response:
        if url.endswith("attachment/upload-link/"):
            return http_response(
                404 if failure_source == "upload-link" else 200,
                {"upload_url": "https://storage.example.test/"},
            )
        events.append(json["event"])
        return http_response(200)

    def upload(method: str, url: str, **kwargs: Any) -> Response:
        uploads.append(url)
        return http_response(404)

    monkeypatch.setattr(http_reporting_backend.requests, "post", post)
    monkeypatch.setattr(http_reporting_backend.requests, "request", upload)
    reporter = ResultsReporter("https://example.test/ingest/")
    reporter.report_start({})
    reporter.report_attachment(str(attachment))
    reporter.report_event("call", {})
    reporter.report_end()

    assert reporter.disabled_reason is None
    assert events == ["start", "call", "end"]
    assert len(uploads) == (1 if failure_source == "storage" else 0)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, None])
def test_other_ingest_errors_do_not_disable_reporting(
    monkeypatch: pytest.MonkeyPatch, status: int | None
) -> None:
    events: list[str] = []

    def post(url: str, *, json: dict, **kwargs: Any) -> Response:
        events.append(json["event"])
        if status is None:
            raise requests.HTTPError("No response")
        return http_response(status)

    monkeypatch.setattr(http_reporting_backend.requests, "post", post)
    reporter = ResultsReporter("https://example.test/ingest/")
    reporter.report_start({})
    reporter.report_event("call", {})
    reporter.report_end()

    assert reporter.disabled_reason is None
    assert events == ["start", "call", "end"]


def test_new_reporter_tries_again_after_another_reporter_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter([http_response(404), http_response(200), http_response(200)])
    monkeypatch.setattr(
        http_reporting_backend.requests, "post", lambda *a, **k: next(responses)
    )
    first = ResultsReporter("https://example.test/ingest/")
    first.report_start({})
    first.report_end()
    second = ResultsReporter("https://example.test/ingest/")
    second.report_start({})
    second.report_end()

    assert first.disabled_reason == INGEST_NOT_FOUND_WARNING
    assert second.disabled_reason is None
