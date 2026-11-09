from collections.abc import Generator

import pytest

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True, scope="session")
def testinel_dsn() -> Generator[None, None, None]:
    mp = pytest.MonkeyPatch()
    mp.setenv("TESTINEL_DSN", "https://example.test/ingest")
    yield
    mp.undo()
