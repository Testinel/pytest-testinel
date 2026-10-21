"""Run the installed plugin against real pytest objects in isolated processes."""

import json
from dataclasses import dataclass
from typing import Any

import pytest


RUN_URL = "https://dashboard.example.test/runs/compatibility/"

TRANSPORT_CONFTEST = """
import json
from pathlib import Path
from typing import Any

import requests


def send(
    self: requests.Session, request: requests.PreparedRequest, **kwargs: Any
) -> requests.Response:
    if request.method != "POST" or request.url != "https://ingest.example.test/events":
        raise RuntimeError("Unexpected HTTP request in compatibility test")
    event = json.loads(request.body)
    with Path("events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event) + "\\n")
    response = requests.Response()
    response.status_code = 200
    response.request = request
    response.url = request.url
    response._content = b'{"run_web_url": "https://dashboard.example.test/runs/compatibility/"}'
    return response


requests.Session.send = send


def pytest_terminal_summary(terminalreporter: Any) -> None:
    terminalreporter.write_line("Other plugin summary")
"""


@dataclass
class PluginRun:
    result: pytest.RunResult
    events: list[dict[str, Any]]

    @property
    def output(self) -> str:
        return self.result.stdout.str() + "\n" + self.result.stderr.str()

    @property
    def phases(self) -> list[dict[str, Any]]:
        return [
            event for event in self.events if event["event"] not in {"start", "end"}
        ]

    def check_lifecycle(self, exit_code: pytest.ExitCode) -> None:
        assert self.result.ret == exit_code, self.output
        assert "INTERNALERROR" not in self.output
        assert "PluggyTeardownRaisedWarning" not in self.output
        assert "Reporting event failed" not in self.output
        assert "Reporting start failed" not in self.output
        assert "Reporting end event failed" not in self.output
        names = [event["event"] for event in self.events]
        assert names[0] == "start"
        assert names[-1] == "end"
        assert names.count("start") == names.count("end") == 1
        assert len({event["run_id"] for event in self.events}) == 1


class PluginRunner:
    def __init__(self, pytester: pytest.Pytester) -> None:
        self.pytester = pytester

    def run(self, source: str, *args: str) -> PluginRun:
        self.pytester.makeconftest(TRANSPORT_CONFTEST)
        self.pytester.makepyfile(test_sample=source)
        result = self.pytester.runpytest_subprocess(
            "-p", "pytest_testinel.testinel", *args, timeout=30
        )
        path = self.pytester.path / "events.jsonl"
        events = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        return PluginRun(result, events)
