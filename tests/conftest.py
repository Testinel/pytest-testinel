import pytest


@pytest.fixture(autouse=True, scope="session")
def testinel_dsn() -> None:
    mp = pytest.MonkeyPatch()
    mp.setenv("TESTINEL_DSN", "https://example.test/ingest")
    yield
    mp.undo()
