import logging
import os
import queue
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import unquote, urlparse

from requests import HTTPError, Response

from pytest_testinel.diagnostics import ReportingValidationError
from pytest_testinel.file_reporting_backend import FileReportingBackend
from pytest_testinel.http_reporting_backend import HttpReportingBackend
from pytest_testinel.payload import Limits, bound_event, negotiate
from pytest_testinel.reporting_backend import ReportingBackend

SDK_NAME = "testinel.pytest"
PACKAGE_NAME = "pytest-testinel"
UNKNOWN_VERSION = "unknown"

logger = logging.getLogger(__name__)

REQUEST_ID_HEADERS = ("X-Request-ID", "Request-ID", "X-Correlation-ID")
INGEST_NOT_FOUND_WARNING = (
    "Testinel reporting disabled for the rest of this run: ingest endpoint returned "
    "HTTP 404. Check TESTINEL_DSN and project availability at "
    "https://testinel.dev/projects/. Tests will continue normally."
)


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

    def __init__(
        self,
        dsn: str,
        backend: ReportingBackend | None = None,
        *,
        diagnostic_schema_version: int = 1,
    ) -> None:
        if diagnostic_schema_version not in (1, 2):
            raise ValueError("Unsupported diagnostic schema version")
        self.limits = Limits.from_environment()
        self.diagnostic_schema_version = diagnostic_schema_version
        self._disabled_message: str | None = None
        self.dsn = dsn
        self.run_id = str(uuid.uuid4())
        self.run_web_url = None
        self.tests = []
        self.attachments: list[str] = []
        self._reporting_disabled = threading.Event()
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
            return HttpReportingBackend(
                url=dsn, headers=_build_http_headers(), limits=self.limits
            )
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

    @property
    def disabled_reason(self) -> str | None:
        if self._reporting_disabled.is_set():
            return self._disabled_message or INGEST_NOT_FOUND_WARNING
        return None

    def _record_event(self, event: dict) -> dict | None:
        if self._reporting_disabled.is_set():
            return None
        event["event_id"] = str(uuid.uuid4())
        try:
            if isinstance(self.backend, HttpReportingBackend):
                self.backend.limits = self.limits
                self.backend.diagnostic_schema_version = self.diagnostic_schema_version
                return self.backend.record_event(event)
            bounded, _ = bound_event(event, self.limits, self.diagnostic_schema_version)
            return self.backend.record_event(bounded)
        except ReportingValidationError as exc:
            if event.get("event") == "start":
                self._disabled_message = "Testinel reporting disabled for this run: complete inventory cannot be reported within the configured size/identity limits. Tests will continue normally."
                self._reporting_disabled.set()
                self.attachments.clear()
            if event.get("event") != "start":
                raise
            logger.warning("%s", exc)
            return None
        except HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 404:
                raise
            self._reporting_disabled.set()
            self.attachments.clear()
            logger.warning(INGEST_NOT_FOUND_WARNING)
            return None

    def report_start(self, payload: dict) -> str | None:
        if self._reporting_disabled.is_set():
            return None
        try:
            self.backend.on_start()
            sdk_info = _build_sdk_info()
            response = self._record_event(
                {
                    "run_id": self.run_id,
                    "event": "start",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "sdk": sdk_info,
                    "payload": payload,
                    "tests": self.tests,
                }
            )
        except Exception as exc:  # noqa: BLE001 -- Backend failures must not interrupt pytest.
            _log_failure(logging.WARNING, "Reporting start failed.", exc)
            return None
        if self._reporting_disabled.is_set():
            return None
        negotiated, event_limit = negotiate(response, self.limits.event)
        if isinstance(self.backend, HttpReportingBackend) or response is not None:
            self.diagnostic_schema_version = negotiated
        self.limits = replace(self.limits, event=event_limit)
        logger.info("Reporting started. Testinel info: %s", sdk_info)
        return self._capture_run_web_url(response)

    def report_end(self) -> str | None:
        response = None
        failed = False
        try:
            self._upload_queue.put(None)
            self._uploader.join()
        except Exception as exc:  # noqa: BLE001 -- Uploader shutdown must not prevent backend cleanup.
            failed = True
            _log_failure(logging.WARNING, "Stopping attachment uploader failed.", exc)

        try:
            response = self._record_event(
                {
                    "run_id": self.run_id,
                    "event": "end",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001 -- Event delivery failures must not interrupt pytest.
            failed = True
            _log_failure(logging.WARNING, "Reporting end event failed.", exc)
        finally:
            try:
                self.backend.on_end()
            except Exception as exc:  # noqa: BLE001 -- Backend cleanup failures must not interrupt pytest.
                failed = True
                _log_failure(logging.WARNING, "Reporting backend cleanup failed.", exc)

        if self._reporting_disabled.is_set():
            return None
        if not failed:
            logger.info("Reporting ended.")
        return self._capture_run_web_url(response) or self.run_web_url

    def report_event(self, event: str, payload: dict) -> None:
        if self._reporting_disabled.is_set():
            return
        try:
            response = self._record_event(
                {
                    "run_id": self.run_id,
                    "event": event,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "payload": payload,
                    "screenshots": self.attachments,
                }
            )
        except ReportingValidationError as exc:
            logger.warning("%s", exc)
            return
        except Exception as exc:  # noqa: BLE001 -- Event delivery failures must not interrupt pytest.
            _log_failure(
                logging.WARNING,
                "Reporting event failed.",
                exc,
                event=event,
            )
            return
        if self._reporting_disabled.is_set():
            return
        self._capture_run_web_url(response)
        self.attachments = []
        logger.debug("Event %r reported.", event)

    def report_attachment(self, filename: str) -> None:
        if self._reporting_disabled.is_set():
            return
        upload_info = None
        try:
            upload_info = self.backend.request_upload_link(filename)
            if upload_info is not None and not isinstance(upload_info, dict):
                raise ValueError("Upload-link response must be a dictionary.")
        except Exception as exc:  # noqa: BLE001 -- Attachment reporting is best effort for any backend.
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
                if self._reporting_disabled.is_set():
                    continue
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
            except Exception as exc:  # noqa: BLE001 -- Failed uploads must not stop processing queued attachments.
                _log_failure(
                    logging.ERROR,
                    "Attachment upload failed.",
                    exc,
                    filename,
                )
            finally:
                self._upload_queue.task_done()
