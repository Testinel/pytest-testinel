from __future__ import annotations

import json

import pytest

from .support import TRANSPORT_CONFTEST, PluginRunner


@pytest.mark.parametrize("relationship", ["direct_cause", "context", "suppressed"])
def test_negotiated_v2_causal_sequence(
    plugin_runner: PluginRunner, relationship: str
) -> None:
    response = json.dumps(
        {
            "run_web_url": "https://dashboard.example.test/run/",
            "ingest_capabilities": {
                "diagnostic_schema_versions": [1, 2],
                "max_event_bytes": 16384,
            },
        }
    ).encode()
    conftest = TRANSPORT_CONFTEST.replace(
        'response._content = b\'{"run_web_url": "https://dashboard.example.test/runs/compatibility/"}\'',
        "response._content = " + repr(response),
    )
    plugin_runner.pytester.makeconftest(conftest)
    suffix = (
        " from cause"
        if relationship == "direct_cause"
        else " from None"
        if relationship == "suppressed"
        else ""
    )
    plugin_runner.pytester.makepyfile(
        """def test_example() -> None:
    local = "x" * 600000
    try:
        raise ValueError("earlier")
    except ValueError as cause:
        raise RuntimeError("newest")"""
        + suffix
    )
    result = plugin_runner.pytester.runpytest_subprocess(
        "-p", "pytest_testinel.testinel", "--showlocals", "-vv", timeout=30
    )
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    events = [
        json.loads(line)
        for line in (plugin_runner.pytester.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    error = next(
        e["payload"]["error_info"]
        for e in events
        if e.get("payload", {}).get("error_info")
    )
    assert error["schema_version"] == 2
    assert "repr_info" not in error and "traceback" not in error
    assert error["exception"]["type"] == "builtins.RuntimeError"
    assert error["reduction"]["truncated_values"] > 0
    if relationship == "suppressed":
        assert error["causes"] == []
    else:
        assert len(error["causes"]) == 1
        assert error["causes"][0]["relationship"] == relationship
        assert error["causes"][0]["exception"]["type"] == "builtins.ValueError"
    assert all(
        len(json.dumps(e, ensure_ascii=True, separators=(",", ":")).encode()) <= 16384
        for e in events[1:]
    )
