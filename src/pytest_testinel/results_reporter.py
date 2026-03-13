import os, queue, threading, uuid, logging
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import unquote, urlparse

from requests import Response

from pytest_testinel.file_reporting_backend import FileReportingBackend
from pytest_testinel.http_reporting_backend import HttpReportingBackend
from pytest_testinel.reporting_backend import ReportingBackend

SDK_NAME = "testinel.pytest"
PACKAGE_NAME = "pytest-testinel"
UNKNOWN_VERSION = "unknown"

logger = logging.getLogger("testinel")


def _get_sdk_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def _build_sdk_info() -> dict:
    sdk_version = _get_sdk_version()
    return {
        "name": SDK_NAME,
        "version": sdk_version,
        "packages": [{"name": f"pypi:{PACKAGE_NAME}", "version": sdk_version}],
    }


def _build_http_headers() -> dict[str, str]:
    client = f"{SDK_NAME}/{_get_sdk_version()}"
    return {
        "User-Agent": client,
        "X-Testinel-Client": client,
    }


class ResultsReporter:
    run_id: str
    dsn: str
    backend: ReportingBackend
    tests: list[dict]

    def __init__(self, dsn: str, backend: ReportingBackend | None = None):
        self.dsn = dsn
        self.run_id = str(uuid.uuid4())
        self.tests = []
        self.attachments: list[str] = []
        self._upload_queue: queue.Queue = queue.Queue()
        self._uploader: threading.Thread = threading.Thread(
            target=self._upload_loop,
            name="testinel-file-uploader",
            daemon=True,
        )
        self._uploader.start()
        if backend:
            self.backend = backend
        else:
            self.backend = self._backend_from_dsn(dsn)

    def _backend_from_dsn(self, dsn: str) -> ReportingBackend:
        parsed = urlparse(dsn)
        if parsed.scheme in {"http", "https"}:
            return HttpReportingBackend(url=dsn, headers=_build_http_headers())
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
        sdk_info = _build_sdk_info()
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": "start",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sdk": sdk_info,
                "payload": payload,
                "tests": self.tests,
            }
        )
        logger.info(f"Reporting started. Testinel info: {sdk_info}")

    def report_end(self) -> None:
        self._upload_queue.put(None)
        self._uploader.join()
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": "end",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.backend.on_end()
        logger.info("Reporting ended.")

    def report_event(self, event: str, payload: dict) -> None:
        self.backend.record_event(
            {
                "run_id": self.run_id,
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
                "screenshots": self.attachments,
            }
        )
        self.attachments = []
        logger.info(f"Event '{event}' reported.")

    def report_attachment(self, filename: str) -> None:
        upload_info = None
        try:
            upload_info = self.backend.request_upload_link(filename)
        except Exception as exc:
            logger.exception(f"Requesting upload link failed for '{filename}'.")
            upload_info = None

        if not upload_info:
            self.attachments.append(filename)
            return

        object_key = upload_info.get("object_key")
        if object_key:
            self.attachments.append(object_key)
        else:
            self.attachments.append(filename)

        upload_url = upload_info.get("upload_url")
        method = upload_info.get("method", "PUT")
        headers = upload_info.get("headers", {})
        self._upload_queue.put((upload_url, method, headers, filename))
        logger.info(f"Attachment '{filename}' put to the uploading queue.")

    def _upload_loop(self) -> None:
        logger.info("Upload loop started.")
        while True:
            item = self._upload_queue.get()
            if item is None:
                self._upload_queue.task_done()
                logger.info("Received 'None'. Upload loop exited.")
                return
            upload_url, method, headers, filename = item
            logger.info(f"Received upload task: '{filename}'.")
            try:
                resp = self.backend.upload_file(
                    upload_url=upload_url,
                    method=method,
                    headers=headers,
                    filename=filename,
                )
                if isinstance(resp, Response):
                    if resp.ok:
                        logger.info(f"File '{filename}' uploaded.")
                    else:
                        logger.error(
                            f"Upload failed for '{filename}'. Status: {resp.status_code}, content: {resp.content!r}"
                        )
            except Exception:
                logger.exception(f"Upload for '{filename}' failed.")
            finally:
                self._upload_queue.task_done()
