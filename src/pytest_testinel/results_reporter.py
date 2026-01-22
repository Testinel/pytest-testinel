import abc, datetime, json, os, uuid
from urllib.parse import unquote, urlparse

import requests


class ReportingBackend(abc.ABC):
    @abc.abstractmethod
    def record_event(self, event: dict) -> None: ...

    def on_start(self) -> None:
        return

    def on_end(self) -> None:
        return


class NoopReportingBackend(ReportingBackend):
    def record_event(self, event: dict) -> None:
        return


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


class HttpReportingBackend(ReportingBackend):
    url: str

    def __init__(self, url: str):
        self.url = url

    def record_event(self, event: dict) -> None:
        requests.post(self.url, json=event, verify=False)


class ResultsReporter:
    run_id: str
    dsn: str
    backend: ReportingBackend
    tests: list[dict]

    def __init__(self, dsn: str, backend: ReportingBackend | None = None):
        self.dsn = dsn
        self.run_id = str(uuid.uuid4())
        self.tests = []
        if backend:
            self.backend = backend
        else:
            self.backend = self._backend_from_dsn(dsn)

    def _backend_from_dsn(self, dsn: str) -> ReportingBackend:
        parsed = urlparse(dsn)
        if parsed.scheme in {"http", "https"}:
            return HttpReportingBackend(url=dsn)
        if parsed.scheme == "file":
            if parsed.netloc:
                raise ValueError(
                    "Unsupported file DSN with host component. Use file:///path/to/file."
                )
            filename = unquote(parsed.path)
            return FileReportingBackend(filename=filename)
        if parsed.scheme == "":
            return FileReportingBackend(filename=os.fspath(dsn))
        raise ValueError(
            f"Unsupported TESTINEL_DSN scheme '{parsed.scheme}'. "
            "Use https://... or file:///path/to/file."
        )

    def report_start(self, payload: dict) -> None:
        self.backend.on_start()
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": "start",
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "payload": payload,
                "tests": self.tests,
            }
        )

    def report_end(self) -> None:
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": "end",
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        self.backend.on_end()

    def report_event(self, event: str, payload: dict) -> None:
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": event,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "payload": payload,
            }
        )
