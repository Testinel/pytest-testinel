import abc
from typing import Any


class ReportingBackend(abc.ABC):
    @abc.abstractmethod
    def record_event(self, event: dict) -> None: ...

    def on_start(self) -> None:
        return

    def on_end(self) -> None:
        return

    def request_upload_link(self, filename: str) -> dict | None:
        return None

    def upload_file(
        self,
        upload_url: str,
        method: str,
        headers: dict,
        filename: str,
    ) -> Any | None:
        return None
