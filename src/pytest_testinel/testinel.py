import os
import traceback
from dataclasses import asdict
from itertools import dropwhile
from typing import Callable, Generator, Any

import pytest
from _pytest._code.code import ExceptionChainRepr

from .env_vars import ENV_VAR_WHITELIST
from .results_reporter import NoopReportingBackend, ResultsReporter

_test_reporter: ResultsReporter | None = None


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


def _safe_path(value: object) -> str:
    try:
        path = os.fspath(value)
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
        from selenium.webdriver.remote.webdriver import WebDriver
    except Exception:
        return

    original = getattr(WebDriver, "save_screenshot", None)
    if original is None or getattr(original, "_testinel_patched", False):
        return

    def patched(self, filename, *args, **kwargs):
        result = original(self, filename, *args, **kwargs)
        try:
            _get_test_reporter().report_screenshot(_safe_path(filename))
        except Exception:
            return result
        return result

    patched._testinel_patched = True
    patched._testinel_original = original
    WebDriver.save_screenshot = patched


def serialize_repr(long_repr: ExceptionChainRepr) -> dict:
    return asdict(long_repr)


def to_test_dict(item: Callable) -> dict:
    test_cls_docstring = item.parent.obj.__doc__ or ""
    test_fn_docstring = item.obj.__doc__ or ""
    return {
        "test_id": item.nodeid,
        "location": item.location,
        "description": test_fn_docstring or test_cls_docstring,
    }


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(
    item: Callable,
    call,
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
            lambda t: not item.location[0] in t.filename, tb_frames
        )
        ss = traceback.StackSummary.from_list(filtered_frames)

        exc_info = {
            "type": f"{exc.__class__.__module__}.{exc.__class__.__name__}",
            "message": str(exc),
            "notes": list(getattr(exc, "__notes__", []) or []),
        }

        repr_info = serialize_repr(report.longrepr)

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
                    for f in ss
                ],
                "exception": exc_info,
            }
            if report.outcome == "failed"
            else None,
        },
    )


@pytest.fixture(scope="session", autouse=True)
def reporter(request):
    config = request.config
    _get_test_reporter().report_start(
        payload={
            "args": config.args,
            "options": vars(config.option),
            "environment": {
                key: os.environ[key] for key in os.environ if key in ENV_VAR_WHITELIST
            },
        }
    )
    yield
    _get_test_reporter().report_end()


def pytest_collection_finish(session):
    tests = [to_test_dict(item) for item in session.items]
    _get_test_reporter().tests = tests


_patch_selenium_save_screenshot()
