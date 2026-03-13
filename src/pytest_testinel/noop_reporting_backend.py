from pytest_testinel.reporting_backend import ReportingBackend


class NoopReportingBackend(ReportingBackend):
    def record_event(self, event: dict) -> None:
        return
