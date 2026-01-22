import os
import traceback
from dataclasses import asdict
from itertools import dropwhile
from typing import Callable, Generator, Any, Final, Set

import pytest
from _pytest._code.code import ExceptionChainRepr

from .results_reporter import NoopReportingBackend, ResultsReporter

ENV_VAR_WHITELIST: Final[Set] = {
    "CI",
    "BITBUCKET_BUILD_NUMBER",
    "BITBUCKET_COMMIT",
    "BITBUCKET_WORKSPACE",
    "BITBUCKET_REPO_SLUG",
    "BITBUCKET_REPO_UUID",
    "BITBUCKET_REPO_FULL_NAME",
    "BITBUCKET_BRANCH",
    "BITBUCKET_TAG",
    "BITBUCKET_BOOKMARK",
    "BITBUCKET_PARALLEL_STEP",
    "BITBUCKET_PARALLEL_STEP_COUNT",
    "BITBUCKET_PR_ID",
    "BITBUCKET_PR_DESTINATION_BRANCH",
    "BITBUCKET_GIT_HTTP_ORIGIN",
    "BITBUCKET_GIT_SSH_ORIGIN",
    "BITBUCKET_STEP_UUID",
    "BITBUCKET_PIPELINE_UUID",
    "BITBUCKET_PROJECT_KEY",
    "BITBUCKET_PROJECT_UUID",
    "BITBUCKET_STEP_RUN_NUMBER",
}

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
