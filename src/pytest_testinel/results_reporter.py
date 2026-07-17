import logging
import os
import queue
import threading
import uuid
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

logger = logging.getLogger(__name__)

REQUEST_ID_HEADERS = ("X-Request-ID", "Request-ID", "X-Correlation-ID")


def _response_metadata(
    response: Response | None,
) -> tuple[object, object, object, object]:
    if response is None:
        return None, None, None, None

    request_id = next(
        (
            response.headers[name]
            for name in REQUEST_ID_HEADERS
            if name in response.headers
        ),
        None,
    )
    return (
        response.status_code,
        response.reason,
        len(response.content or b""),
        request_id,
    )


def _log_failure(
    level: int,
    message: str,
    exc: Exception,
    filename: str | None = None,
    event: str | None = None,
) -> None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Response):
        response = None
    status, reason, response_size, request_id = _response_metadata(response)
    logger.log(
        level,
        "%s error_type=%s status=%r reason=%r response_size=%r request_id=%r "
        "filename=%r event=%r",
        message,
        type(exc).__name__,
        status,
        reason,
        response_size,
        request_id,
        os.path.basename(filename) if filename else None,
        event,
    )


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
    run_web_url: str | None
    dsn: str
    backend: ReportingBackend
    tests: list[dict]

    def __init__(self, dsn: str, backend: ReportingBackend | None = None):
        self.dsn = dsn
        self.run_id = str(uuid.uuid4())
        self.run_web_url = None
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

    def _capture_run_web_url(self, response: dict | None) -> str | None:
        if not isinstance(response, dict):
            return None
        run_web_url = response.get("run_web_url")
        if not isinstance(run_web_url, str) or not run_web_url:
            return None
        self.run_web_url = run_web_url
        return run_web_url

    def report_start(self, payload: dict) -> str | None:
        try:
            self.backend.on_start()
            sdk_info = _build_sdk_info()
            response = self.backend.record_event(
                {
                    "run_id": self.run_id,
                    "event": "start",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sdk": sdk_info,
                    "payload": payload,
                    "tests": self.tests,
                }
            )
        except Exception as exc:
            _log_failure(logging.WARNING, "Reporting start failed.", exc)
            return None
        logger.info("Reporting started. Testinel info: %s", sdk_info)
        return self._capture_run_web_url(response)

    def report_end(self) -> str | None:
        response = None
        failed = False
        try:
            self._upload_queue.put(None)
            self._uploader.join()
        except Exception as exc:
            failed = True
            _log_failure(logging.WARNING, "Stopping attachment uploader failed.", exc)

        try:
            response = self.backend.record_event(
                {
                    "run_id": self.run_id,
                    "event": "end",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:
            failed = True
            _log_failure(logging.WARNING, "Reporting end event failed.", exc)
        finally:
            try:
                self.backend.on_end()
            except Exception as exc:
                failed = True
                _log_failure(logging.WARNING, "Reporting backend cleanup failed.", exc)

        if not failed:
            logger.info("Reporting ended.")
        return self._capture_run_web_url(response) or self.run_web_url

    def report_event(self, event: str, payload: dict) -> None:
        try:
            response = self.backend.record_event(
                {
                    "run_id": self.run_id,
                    "event": event,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                    "screenshots": self.attachments,
                }
            )
        except Exception as exc:
            _log_failure(
                logging.WARNING,
                "Reporting event failed.",
                exc,
                event=event,
            )
            return
        self._capture_run_web_url(response)
        self.attachments = []
        logger.debug("Event %r reported.", event)

    def report_attachment(self, filename: str) -> None:
        upload_info = None
        try:
            upload_info = self.backend.request_upload_link(filename)
            if upload_info is not None and not isinstance(upload_info, dict):
                raise ValueError("Upload-link response must be a dictionary.")
        except Exception as exc:
            _log_failure(
                logging.WARNING,
                "Requesting attachment upload link failed.",
                exc,
                filename,
            )
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
        logger.debug(
            "Attachment %r put in the upload queue.", os.path.basename(filename)
        )

    def _upload_loop(self) -> None:
        logger.debug("Upload loop started.")
        while True:
            item = self._upload_queue.get()
            if item is None:
                self._upload_queue.task_done()
                logger.debug("Upload loop stopped.")
                return
            upload_url, method, headers, filename = item
            safe_filename = os.path.basename(filename)
            logger.debug("Received upload task for %r.", safe_filename)
            try:
                resp = self.backend.upload_file(
                    upload_url=upload_url,
                    method=method,
                    headers=headers,
                    filename=filename,
                )
                if isinstance(resp, Response):
                    if resp.ok:
                        logger.debug("File %r uploaded.", safe_filename)
                    else:
                        status, reason, response_size, request_id = _response_metadata(
                            resp
                        )
                        logger.error(
                            "Upload failed. status=%r reason=%r response_size=%r "
                            "request_id=%r filename=%r",
                            status,
                            reason,
                            response_size,
                            request_id,
                            safe_filename,
                        )
            except Exception as exc:
                _log_failure(
                    logging.ERROR,
                    "Attachment upload failed.",
                    exc,
                    filename,
                )
            finally:
                self._upload_queue.task_done()
