import abc, datetime, json, os, queue, threading, uuid
from urllib.parse import unquote, urlparse

import requests


class ReportingBackend(abc.ABC):
    @abc.abstractmethod
    def record_event(self, event: dict) -> None: ...

    def on_start(self) -> None:
        return

    def on_end(self) -> None:
        return

    def request_upload_link(self, filename: str) -> dict | None:
        return None

    def upload_screenshot(
        self,
        upload_url: str,
        method: str,
        headers: dict,
        filename: str,
    ) -> None:
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

    def request_upload_link(self, filename: str) -> dict | None:
        upload_url = f"{self.url.rstrip('/')}/screenshots/upload-link/"
        response = requests.post(upload_url, json={"filename": filename}, verify=False)
        response.raise_for_status()
        return response.json()

    def upload_screenshot(
        self,
        upload_url: str,
        method: str,
        headers: dict,
        filename: str,
    ) -> None:
        with open(filename, "rb") as f:
            requests.request(method, upload_url, data=f, headers=headers)


class ResultsReporter:
    run_id: str
    dsn: str
    backend: ReportingBackend
    tests: list[dict]

    def __init__(self, dsn: str, backend: ReportingBackend | None = None):
        self.dsn = dsn
        self.run_id = str(uuid.uuid4())
        self.tests = []
        self.screenshots: list[str] = []
        self._upload_queue: queue.Queue | None = None
        self._uploader: threading.Thread | None = None
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
        if self._upload_queue is not None:
            self._upload_queue.put(None)
        if self._uploader is not None:
            self._uploader.join(timeout=10)
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
                "screenshots": self.screenshots,
            }
        )
        self.screenshots = []

    def report_screenshot(self, filename: str) -> None:
        upload_info = None
        try:
            upload_info = self.backend.request_upload_link(filename)
        except Exception:
            upload_info = None

        if not upload_info:
            self.screenshots.append(filename)
            return

        object_key = upload_info.get("object_key")
        if object_key:
            self.screenshots.append(object_key)
        else:
            self.screenshots.append(filename)

        self._ensure_uploader()
        upload_url = upload_info.get("upload_url")
        method = upload_info.get("method", "PUT")
        headers = upload_info.get("headers", {})
        if self._upload_queue is not None and upload_url:
            self._upload_queue.put((upload_url, method, headers, filename))

    def _ensure_uploader(self) -> None:
        if self._uploader is not None:
            return
        self._upload_queue = queue.Queue()
        self._uploader = threading.Thread(
            target=self._upload_loop,
            name="testinel-screenshot-uploader",
            daemon=True,
        )
        self._uploader.start()

    def _upload_loop(self) -> None:
        if self._upload_queue is None:
            return
        while True:
            item = self._upload_queue.get()
            if item is None:
                break
            upload_url, method, headers, filename = item
            try:
                self.backend.upload_screenshot(
                    upload_url=upload_url,
                    method=method,
                    headers=headers,
                    filename=filename,
                )
            except Exception:
                pass
            finally:
                self._upload_queue.task_done()
