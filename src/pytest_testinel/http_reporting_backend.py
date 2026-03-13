import logging
from typing import Any

import requests

from pytest_testinel.reporting_backend import ReportingBackend

logger = logging.getLogger("testinel")


class HttpReportingBackend(ReportingBackend):
    url: str

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}

    def record_event(self, event: dict) -> None:
        requests.post(self.url, json=event, headers=self.headers, verify=False)

    def request_upload_link(self, filename: str) -> dict | None:
        upload_url = f"{self.url.rstrip('/')}/attachment/upload-link/"
        response = requests.post(
            upload_url,
            json={"filename": filename},
            headers=self.headers,
            verify=False,
        )
        response.raise_for_status()
        return response.json()

    def upload_file(
        self,
        upload_url: str,
        method: str,
        headers: dict,
        filename: str,
    ) -> Any | None:
        with open(filename, "rb") as f:
            return requests.request(method, upload_url, data=f, headers=headers)
