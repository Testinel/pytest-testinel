import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import requests

from pytest_testinel.http_reporting_backend import (
    EVENT_TIMEOUT,
    UPLOAD_TIMEOUT,
    HttpReportingBackend,
)
from pytest_testinel.results_reporter import ResultsReporter


@pytest.fixture
def sent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[requests.PreparedRequest, dict[str, Any]]]:
    calls: list[tuple[requests.PreparedRequest, dict[str, Any]]] = []

    def send(
        self: requests.Session, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        calls.append((request, kwargs))
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response._content = b"{}"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return calls


def request_json(request: requests.PreparedRequest) -> dict[str, Any]:
    body = request.body
    assert isinstance(body, (str, bytes))
    return json.loads(body)


def test_reporter_sends_unique_event_and_request_ids(
    sent_requests: list[tuple[requests.PreparedRequest, dict[str, Any]]],
) -> None:
    reporter = ResultsReporter("https://example.test/ingest/")
    reporter.report_start({})
    for phase in ("setup", "call", "teardown"):
        reporter.report_event(phase, {"test": {"test_id": "example::test"}})
    reporter.report_end()

    events = []
    event_ids = set()
    request_ids = set()
    for request, options in sent_requests:
        body = request_json(request)
        events.append(body["event"])
        event_ids.add(body["event_id"])
        request_ids.add(body["request_id"])
        assert UUID(body["event_id"]).version == 4
        assert UUID(body["request_id"]).version == 4
        assert request.headers["X-Request-ID"] == body["request_id"]
        assert "event_id" not in request.headers
        assert "X-Event-ID" not in request.headers
        assert body["run_id"] == reporter.run_id
        assert request.headers["X-Testinel-Client"].startswith("testinel.pytest/")
        assert options["timeout"] == EVENT_TIMEOUT

    assert events == ["start", "setup", "call", "teardown", "end"]
    assert len(event_ids) == len(request_ids) == 5
    assert event_ids.isdisjoint(request_ids)


def test_resending_event_keeps_event_id_and_refreshes_request_id(
    sent_requests: list[tuple[requests.PreparedRequest, dict[str, Any]]],
) -> None:
    headers = {"X-Testinel-Client": "test-client", "X-Request-ID": "stale-header"}
    event = {
        "event": "call",
        "event_id": str(uuid4()),
        "request_id": "stale-body",
        "payload": {"outcome": "passed"},
    }
    backend = HttpReportingBackend("https://example.test/ingest/", headers)

    backend.record_event(event)
    backend.record_event(event)

    bodies = [request_json(request) for request, _ in sent_requests]
    assert bodies[0]["event_id"] == bodies[1]["event_id"] == event["event_id"]
    assert bodies[0]["request_id"] != bodies[1]["request_id"]
    for (request, _), body in zip(sent_requests, bodies):
        assert UUID(body["request_id"]).version == 4
        assert request.headers["X-Request-ID"] == body["request_id"]
        assert body["payload"] == event["payload"]
    assert event["request_id"] == "stale-body"
    assert headers == {
        "X-Testinel-Client": "test-client",
        "X-Request-ID": "stale-header",
    }


def test_upload_link_requests_have_matching_header_and_body_ids(
    sent_requests: list[tuple[requests.PreparedRequest, dict[str, Any]]],
) -> None:
    backend = HttpReportingBackend("https://example.test/ingest/")

    backend.request_upload_link("screenshot.png")
    backend.request_upload_link("screenshot.png")

    request_ids = set()
    for request, options in sent_requests:
        body = request_json(request)
        request_id = body["request_id"]
        request_ids.add(request_id)
        assert UUID(request_id).version == 4
        assert request.headers["X-Request-ID"] == request_id
        assert body == {"filename": "screenshot.png", "request_id": request_id}
        assert request.url == "https://example.test/ingest/attachment/upload-link/"
        assert options["timeout"] == EVENT_TIMEOUT
    assert len(request_ids) == 2


@pytest.mark.parametrize("error_type", [requests.Timeout, requests.ConnectionError])
def test_request_ids_are_logged_before_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[requests.RequestException],
) -> None:
    event_id = str(uuid4())
    captured: list[requests.PreparedRequest] = []

    def send(
        self: requests.Session, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        captured.append(request)
        raise error_type("private transport details", request=request)

    monkeypatch.setattr(requests.Session, "send", send)
    caplog.set_level(logging.DEBUG, logger="pytest_testinel")
    backend = HttpReportingBackend("https://example.test/ingest/private-dsn/")

    with pytest.raises(error_type):
        backend.record_event({"event_id": event_id, "payload": "private payload"})

    assert captured[0].headers["X-Request-ID"] in caplog.text
    assert event_id in caplog.text
    assert "private" not in caplog.text
    assert "example.test" not in caplog.text


def test_binary_upload_preserves_provider_headers_and_body(
    sent_requests: list[tuple[requests.PreparedRequest, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    attachment = tmp_path / "screenshot.png"
    attachment.write_bytes(b"image")
    headers = {"X-Request-ID": "provider-id", "Content-Type": "image/png"}
    backend = HttpReportingBackend("https://example.test/ingest/")

    backend.upload_file(
        "https://storage.example.test/signed", "PUT", headers, str(attachment)
    )

    request, options = sent_requests[0]
    assert request.method == "PUT"
    assert request.headers["X-Request-ID"] == "provider-id"
    assert request.headers["Content-Type"] == "image/png"
    assert request.headers["Content-Length"] == "5"
    assert "X-Event-ID" not in request.headers
    assert options["timeout"] == UPLOAD_TIMEOUT
