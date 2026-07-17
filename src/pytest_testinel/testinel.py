import os
import logging
import sys
import traceback
from os import PathLike
from dataclasses import asdict
from itertools import dropwhile
from typing import Any, Generator, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from _pytest._code.code import ExceptionChainRepr
from _pytest.terminal import TerminalReporter

from .env_vars import ENV_VAR_WHITELIST
from .results_reporter import ResultsReporter
from .noop_reporting_backend import NoopReportingBackend

_test_reporter: ResultsReporter | None = None
_final_run_web_url: str | None = None

logger = logging.getLogger(__name__)
RUN_WEB_URL_UTM_PARAMS = {
    "utm_source": "pytest-testinel",
    "utm_medium": "cli",
}


def _get_test_reporter() -> ResultsReporter:
    global _test_reporter
    if _test_reporter is None:
        dsn = os.environ.get("TESTINEL_DSN")
        if not dsn:
            _test_reporter = ResultsReporter(
                dsn="",
                backend=NoopReportingBackend(),
            )
        else:
            _test_reporter = ResultsReporter(dsn=dsn)
    return _test_reporter


def _add_run_web_url_utm_params(run_web_url: str) -> str:
    parsed_url = urlsplit(run_web_url)
    query_params = [
        (key, value)
        for key, value in parse_qsl(parsed_url.query, keep_blank_values=True)
        if key not in RUN_WEB_URL_UTM_PARAMS
    ]
    query_params.extend(RUN_WEB_URL_UTM_PARAMS.items())

    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query_params),
            parsed_url.fragment,
        )
    )


def _get_terminal_reporter(config: pytest.Config) -> TerminalReporter | None:
    terminal_reporter = config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is None:
        return None
    return cast(TerminalReporter, terminal_reporter)


def _write_run_web_url(config: pytest.Config, run_web_url: str, phase: str) -> None:
    run_web_url = _add_run_web_url_utm_params(run_web_url)
    if phase == "start":
        title = "Testinel live run"
        message = f"View live report: {run_web_url}"
    else:
        title = "Testinel report"
        message = f"View run report: {run_web_url}"

    terminal_reporter = _get_terminal_reporter(config)
    if terminal_reporter is not None:
        terminal_reporter.write_sep("=", title)
        terminal_reporter.write_line(message)
        return

    print(f"=== {title} ===", file=sys.stderr)
    print(message, file=sys.stderr)


def _safe_path(value: object) -> str:
    try:
        if isinstance(value, (str, bytes, PathLike)):
            path = os.fspath(value)
        else:
            return str(value)
    except TypeError:
        return str(value)
    if isinstance(path, bytes):
        try:
            return path.decode()
        except Exception:
            return str(path)
    return path


def _patch_selenium_save_screenshot() -> None:
    try:
        from selenium.webdriver.remote.webdriver import WebDriver  # type: ignore[import-not-found]
    except Exception:
        return

    original = getattr(WebDriver, "save_screenshot", None)
    if original is None or getattr(original, "_testinel_patched", False):
        return

    def patched(self: Any, filename: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(self, filename, *args, **kwargs)
        try:
            _get_test_reporter().report_attachment(_safe_path(filename))
        except Exception:
            return result
        return result

    setattr(patched, "_testinel_patched", True)
    setattr(patched, "_testinel_original", original)
    WebDriver.save_screenshot = patched


def serialize_repr(long_repr: ExceptionChainRepr) -> dict:
    return asdict(long_repr)


def to_test_dict(item: Any) -> dict[str, Any]:
    test_cls_docstring = item.parent.obj.__doc__ or ""
    test_fn_docstring = item.obj.__doc__ or ""
    return {
        "test_id": item.nodeid,
        "location": item.location,
        "description": test_fn_docstring or test_cls_docstring,
    }


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(
    item: Any,
    call: Any,
) -> Generator[None, Any, None]:
    """Pytest hook that wraps the standard pytest_runtest_makereport
    function and grabs the results for the 'call' phase of each test.
    """
    outcome = yield
    report = outcome.get_result()

    report.exception = None
    ss = None
    exc_info = None
    repr_info = None
    if report.outcome == "failed":
        # driver = item.funcargs["driver"]
        # logs = driver.get_log('browser')
        # current_url = driver.current_url
        exc = call.excinfo.value

        tb_frames = traceback.extract_tb(call.excinfo.value.__traceback__)
        filtered_frames = dropwhile(
            lambda t: item.location[0] not in t.filename, tb_frames
        )
        ss = traceback.StackSummary.from_list(filtered_frames)

        exc_info = {
            "type": f"{exc.__class__.__module__}.{exc.__class__.__name__}",
            "message": str(exc),
            "notes": list(getattr(exc, "__notes__", []) or []),
        }

        repr_info = serialize_repr(report.longrepr)

    if output_path := item.funcargs.get("output_path"):
        # Probably, it is Playwright traces
        for dirpath, dirnames, filenames in os.walk(output_path):
            for filename in filenames:
                _get_test_reporter().report_attachment(os.path.join(dirpath, filename))

    _get_test_reporter().report_event(
        event=report.when,
        payload={
            "test": to_test_dict(item),
            "outcome": report.outcome,
            "duration": report.duration,
            "error_info": {
                "repr_info": repr_info,
                "traceback": [
                    {
                        "filename": f.filename,
                        "lineno": f.lineno,
                        "name": f.name,
                        "line": f.line,
                    }
                    for f in (ss or [])
                ],
                "exception": exc_info,
            }
            if report.outcome == "failed"
            else None,
        },
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    config = session.config
    test_reporter = _get_test_reporter()
    test_reporter.tests = [to_test_dict(item) for item in session.items]
    run_web_url = test_reporter.report_start(
        payload={
            "args": config.args,
            "options": vars(config.option),
            "environment": {
                key: os.environ[key] for key in os.environ if key in ENV_VAR_WHITELIST
            },
        }
    )
    if run_web_url:
        _write_run_web_url(config, run_web_url, "start")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _final_run_web_url

    _final_run_web_url = _get_test_reporter().report_end()
    terminal_reporter = _get_terminal_reporter(session.config)
    terminal_summary_expected = (
        terminal_reporter is not None
        and not terminal_reporter.no_summary
        and exitstatus
        in {
            pytest.ExitCode.OK,
            pytest.ExitCode.TESTS_FAILED,
            pytest.ExitCode.INTERRUPTED,
            pytest.ExitCode.USAGE_ERROR,
            pytest.ExitCode.NO_TESTS_COLLECTED,
        }
    )
    if _final_run_web_url and not terminal_summary_expected:
        _write_run_web_url(session.config, _final_run_web_url, "end")
        _final_run_web_url = None
    logger.info("Testinel completed.")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_terminal_summary(
    config: pytest.Config,
) -> Generator[None, Any, None]:
    try:
        yield
    finally:
        global _final_run_web_url

        if _final_run_web_url:
            _write_run_web_url(config, _final_run_web_url, "end")
            _final_run_web_url = None


_patch_selenium_save_screenshot()
