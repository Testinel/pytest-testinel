import logging
from typing import Any
from uuid import uuid4

import requests

from pytest_testinel.payload import Limits, bound_event
from pytest_testinel.reporting_backend import ReportingBackend

EVENT_TIMEOUT = (5, 30)
UPLOAD_TIMEOUT = (5, 60)

logger = logging.getLogger(__name__)


class HttpReportingBackend(ReportingBackend):
    url: str

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        limits: Limits | None = None,
    ) -> None:
        self.limits = limits or Limits.from_environment()
        self.diagnostic_schema_version = 1
        self.url = url
        self.headers = headers or {}

    def _post_json(self, url: str, payload: dict) -> requests.Response:
        request_id = str(uuid4())
        logger.debug(
            "Sending Testinel request. request_id=%s event_id=%s",
            request_id,
            payload.get("event_id"),
        )
        _, body = bound_event(
            {**payload, "request_id": request_id},
            self.limits,
            self.diagnostic_schema_version,
        )
        response = requests.post(
            url,
            data=body,
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
            allow_redirects=True,
            timeout=EVENT_TIMEOUT,
        )
        response.raise_for_status()
        return response

    def record_event(self, event: dict) -> dict[str, Any] | None:
        response = self._post_json(self.url, event)
        try:
            response_json = response.json()
        except (AttributeError, ValueError):
            return None
        if not isinstance(response_json, dict):
            return None
        return response_json

    def request_upload_link(self, filename: str) -> dict | None:
        upload_url = f"{self.url.rstrip('/')}/attachment/upload-link/"
        response = self._post_json(upload_url, {"filename": filename})
        return response.json()

    def upload_file(
        self,
        upload_url: str,
        method: str,
        headers: dict,
        filename: str,
    ) -> Any | None:
        with open(filename, "rb") as f:
            return requests.request(
                method,
                upload_url,
                data=f,
                headers=headers,
                timeout=UPLOAD_TIMEOUT,
            )
