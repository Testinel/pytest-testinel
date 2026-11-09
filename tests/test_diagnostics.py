from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import requests

from pytest_testinel.diagnostics import (
    MARKER,
    PayloadTooLarge,
    all_frames,
    bound_diagnostic,
    encode,
    legacy_projection,
    normalize,
    truncate,
)
from pytest_testinel.http_reporting_backend import HttpReportingBackend
from pytest_testinel.payload import Limits, bound_event, negotiate
from pytest_testinel.results_reporter import ResultsReporter


@pytest.fixture
def diagnostic() -> dict[str, Any]:
    return json.loads(Path("tests/data/diagnostics/v2.json").read_text())


def phase(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "call",
        "run_id": "run",
        "event_id": "event",
        "payload": {
            "test": {"test_id": "tests/test_a.py::test_a", "description": ""},
            "outcome": "failed",
            "duration": 1.2,
            "error_info": error,
        },
        "screenshots": ["image.png"],
    }


@pytest.mark.parametrize("version", [1, 2])
def test_golden_normalization_is_pure_and_idempotent(version: int) -> None:
    original = json.loads(Path(f"tests/data/diagnostics/v{version}.json").read_text())
    snapshot = copy.deepcopy(original)
    canonical = normalize(original)
    assert original == snapshot
    assert normalize(canonical) == canonical
    assert canonical == json.loads(Path("tests/data/diagnostics/v2.json").read_text())
    assert len(canonical["causes"]) == 1
    assert canonical["exception"] is not None
    assert canonical["exception"]["type"] == "builtins.RuntimeError"


@pytest.mark.parametrize("text", ["😀" * 5000, '\\"\x00\n' * 5000, "Ж" * 5000])
def test_individual_utf8_limit_includes_marker(text: str) -> None:
    result = truncate(text, 101)
    assert len(result.encode()) <= 101
    assert MARKER in result
    assert truncate(result, 101) == result


@pytest.mark.parametrize("schema", [1, 2])
def test_cumulative_pressure_removes_all_values_and_opaque_text(
    diagnostic: dict[str, Any], schema: int
) -> None:
    frame = diagnostic["frames"][0]
    frame["locals"] = [{"name": "body", "value": "x" * 3000}]
    frame["args"] = [{"name": "other", "value": "y" * 3000}]
    diagnostic["frames"] = [copy.deepcopy(frame) for _ in range(100)]
    diagnostic["text"] = "opaque local dump"
    event = phase(diagnostic)
    original = copy.deepcopy(event)
    bounded, body = bound_event(event, Limits(event=16384), schema)
    canonical = normalize(bounded["payload"]["error_info"])
    assert len(body) <= 16384
    assert encode(bounded) == body
    assert all(not f["locals"] and not f["args"] for f in all_frames(canonical))
    assert canonical["reduction"]["locals_omitted"]
    assert canonical["text"] is None
    assert bounded["payload"]["outcome"] == "failed"
    assert bounded["payload"]["duration"] == 1.2
    assert bounded["screenshots"] == ["image.png"]
    assert event == original


def test_final_prepared_body_includes_request_identity(
    monkeypatch: pytest.MonkeyPatch, diagnostic: dict[str, Any]
) -> None:
    captured: list[requests.PreparedRequest] = []

    def send(
        self: requests.Session, request: requests.PreparedRequest, **kwargs: Any
    ) -> requests.Response:
        captured.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    diagnostic["frames"] *= 100
    backend = HttpReportingBackend(
        "https://example.test/ingest/", limits=Limits(event=16384)
    )
    backend.diagnostic_schema_version = 2
    backend.record_event(phase(diagnostic))
    request = captured[0]
    assert isinstance(request.body, bytes)
    body = json.loads(request.body)
    assert len(request.body) <= 16384
    assert request.headers["X-Request-ID"] == body["request_id"]
    assert request.headers["Content-Type"] == "application/json"
    assert "repr_info" not in body["payload"]["error_info"]


def test_exact_encoded_boundary(diagnostic: dict[str, Any]) -> None:
    event, body = bound_event(phase(diagnostic), Limits(), 2)
    assert bound_event(event, Limits(event=len(body)), 2)[1] == body
    oversized = {"event": "end", "run_id": "x" * 16384}
    with pytest.raises(PayloadTooLarge):
        bound_event(oversized, Limits(event=16384))


def test_start_inventory_reduction_and_explicit_failure() -> None:
    tests = [
        {"test_id": f"test_{i}", "description": "doc" * 100000, "location": []}
        for i in range(20)
    ]
    event = {
        "event": "start",
        "tests": tests,
        "payload": {"options": {"verbose": True, "html": "x" * 30000}},
    }
    bounded, body = bound_event(event, Limits(event=16384))
    assert [t["test_id"] for t in bounded["tests"]] == [t["test_id"] for t in tests]
    assert len(body) <= 16384
    assert bounded["reduction"]["metadata_omitted"]
    assert all(t["description"] == "" for t in bounded["tests"])
    event["tests"] = [
        {"test_id": f"test_{i}" + "a" * 1000, "description": ""} for i in range(1000)
    ]
    with pytest.raises(PayloadTooLarge):
        bound_event(event, Limits(event=16384))
    with pytest.raises(ValueError, match="identity"):
        bound_event({"event": "start", "tests": [{"test_id": "a" * 1025}]}, Limits())


def test_inventory_failure_disables_run_without_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TESTINEL_MAX_EVENT_BYTES", "16384")
    reporter = ResultsReporter(str(tmp_path / "events.json"))
    reporter.tests = [
        {"test_id": "a" * 1000 + str(i), "description": ""} for i in range(100)
    ]
    reporter.report_start({})
    reporter.report_event("call", {"outcome": "passed"})
    reporter.report_end()
    assert reporter.disabled_reason is not None
    assert json.loads((tmp_path / "events.json").read_text()) == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"ingest_capabilities": None},
        {
            "ingest_capabilities": {
                "diagnostic_schema_versions": ["2"],
                "max_event_bytes": 100,
            }
        },
        {
            "ingest_capabilities": {
                "diagnostic_schema_versions": [2],
                "max_event_bytes": True,
            }
        },
    ],
)
def test_malformed_capabilities_never_enable_v2(response: Any) -> None:
    assert negotiate(response, 50000) == (1, 50000)


def test_capability_lower_limit_and_unusable_limit() -> None:
    assert negotiate(
        {
            "ingest_capabilities": {
                "diagnostic_schema_versions": [1, 2],
                "max_event_bytes": 20000,
            }
        },
        50000,
    ) == (2, 20000)
    assert negotiate(
        {
            "ingest_capabilities": {
                "diagnostic_schema_versions": [1, 2],
                "max_event_bytes": 50000,
            }
        },
        20000,
    ) == (2, 20000)
    with pytest.raises(PayloadTooLarge):
        bound_event({"event": "end", "run_id": "uuid"}, Limits(event=1))


@pytest.mark.parametrize("raw", ["0", "-1", "oops", "16383", "１２３", "1.1"])
def test_invalid_environment_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("TESTINEL_MAX_EVENT_BYTES", raw)
    monkeypatch.setenv("TESTINEL_MAX_VALUE_BYTES", "-1")
    assert Limits.from_environment() == Limits()
    assert caplog.text.count("Invalid Testinel") == 1
    assert raw not in caplog.text


def test_per_frame_budget_and_unnamed_multiline_locals() -> None:
    error = {
        "repr_info": {
            "reprtraceback": {
                "reprentries": [
                    {
                        "reprfileloc": {"path": "a.py", "lineno": 1},
                        "reprlocals": {
                            "lines": [
                                "data = {'a':",
                                "    'multiline'}",
                                "unparseable block",
                            ]
                        },
                        "reprfuncargs": None,
                    }
                ]
            }
        }
    }
    canonical = normalize(error)
    assert canonical["frames"][0]["locals"] == [
        {"name": "data", "value": "{'a':\n    'multiline'}"},
        {"name": None, "value": "unparseable block"},
    ]
    bounded = normalize(error, frame_limit=64)
    assert (
        len(encode([bounded["frames"][0]["locals"], bounded["frames"][0]["args"]]))
        <= 64
    )


def test_all_budgets_and_grouping_skeletons(diagnostic: dict[str, Any]) -> None:
    diagnostic["exception"]["message"] = "x" * 600000
    diagnostic["exception"]["notes"] = ["note" * 20000] * 100
    diagnostic["details"] = ["detail" * 20000] * 100
    diagnostic["captures"] = [["stdout", "capture" * 20000]] * 100
    diagnostic["frames"][0]["source"] = ["source" * 20000]
    diagnostic["frames"] *= 600
    diagnostic["frames"][-2]["path"] = "deepest_user.py"
    diagnostic["frames"][-1] = dict(
        diagnostic["frames"][-1], path="site-packages/driver.py"
    )
    bounded = bound_diagnostic(diagnostic, 16384)
    assert len(encode(bounded)) <= 16384
    assert [f["path"] for f in bounded["frames"]][-2:] == [
        "deepest_user.py",
        "site-packages/driver.py",
    ]
    assert normalize(bounded) == bounded


def test_null_exception_and_unknown_schema() -> None:
    diagnostic = normalize(
        {"exception": None, "repr_info": {"message": "[XPASS(strict)] reason"}}
    )
    assert diagnostic["exception"] is None
    assert legacy_projection(diagnostic)["exception"] is None
    assert diagnostic["text"] == "[XPASS(strict)] reason"
    with pytest.raises(ValueError, match="schema_version"):
        normalize({"schema_version": 3})


@pytest.mark.parametrize("schema", [1, 2])
def test_offline_backend_selection(
    tmp_path: Path, diagnostic: dict[str, Any], schema: int
) -> None:
    filename = tmp_path / "events.json"
    reporter = ResultsReporter(str(filename), diagnostic_schema_version=schema)
    reporter.report_start({})
    reporter.report_event("call", phase(diagnostic)["payload"])
    reporter.report_end()
    error = json.loads(filename.read_text())[1]["payload"]["error_info"]
    assert error.get("schema_version", 1) == schema
    assert ("repr_info" in error) == (schema == 1)


def test_irreducible_upload_filename_is_never_dropped() -> None:
    with pytest.raises(PayloadTooLarge, match="upload_link"):
        bound_event(
            {"filename": "x" * 20000, "request_id": "uuid"}, Limits(event=16384)
        )


@pytest.mark.parametrize("limit", [1, 2, 3, 8])
def test_tiny_positive_value_limits_have_visible_markers(limit: int) -> None:
    value = truncate("x" * 100, limit)
    assert value
    assert len(value.encode()) <= limit
    assert value != "x" * limit
