import json

from pytest_testinel.reporting_backend import ReportingBackend


class FileReportingBackend(ReportingBackend):
    events: list[dict]
    filename: str
    indent: int | None

    def __init__(self, filename: str, indent: int | None = None):
        self.events = []
        self.filename = filename
        self.indent = indent

    def record_event(self, event: dict) -> None:
        self.events.append(event)

    def on_end(self) -> None:
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(self.events, f, indent=self.indent)
