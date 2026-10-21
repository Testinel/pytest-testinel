from typing import Any

import pytest

from .support import PluginRunner


@pytest.mark.parametrize(
    ("source", "outcomes", "exit_code", "error_phase", "exception_type", "message"),
    [
        pytest.param(
            "def test_example() -> None: pass",
            ["passed", "passed", "passed"],
            pytest.ExitCode.OK,
            None,
            None,
            None,
            id="pass",
        ),
        pytest.param(
            'def test_example() -> None: assert False, "assertion failure"',
            ["passed", "failed", "passed"],
            pytest.ExitCode.TESTS_FAILED,
            "call",
            "builtins.AssertionError",
            "assertion failure",
            id="assertion-failure",
        ),
        pytest.param(
            """
            import pytest
            @pytest.fixture
            def broken() -> None:
                raise ValueError("setup failure")
            def test_example(broken: None) -> None: pass
            """,
            ["failed", "passed"],
            pytest.ExitCode.TESTS_FAILED,
            "setup",
            "builtins.ValueError",
            "setup failure",
            id="setup-error",
        ),
        pytest.param(
            """
            from collections.abc import Iterator
            import pytest
            @pytest.fixture
            def broken() -> Iterator[None]:
                yield
                raise ValueError("teardown failure")
            def test_example(broken: None) -> None: pass
            """,
            ["passed", "passed", "failed"],
            pytest.ExitCode.TESTS_FAILED,
            "teardown",
            "builtins.ValueError",
            "teardown failure",
            id="teardown-error",
        ),
        pytest.param(
            """
            import pytest
            @pytest.mark.skip(reason="marked skip")
            def test_example() -> None: pass
            """,
            ["skipped", "passed"],
            pytest.ExitCode.OK,
            None,
            None,
            None,
            id="marked-skip",
        ),
        pytest.param(
            """
            import pytest
            def test_example() -> None: pytest.skip("runtime skip")
            """,
            ["passed", "skipped", "passed"],
            pytest.ExitCode.OK,
            None,
            None,
            None,
            id="runtime-skip",
        ),
        pytest.param(
            """
            import pytest
            @pytest.mark.xfail(reason="expected failure", strict=True)
            def test_example() -> None: assert False
            """,
            ["passed", "skipped", "passed"],
            pytest.ExitCode.OK,
            None,
            None,
            None,
            id="xfail",
        ),
        pytest.param(
            """
            import pytest
            @pytest.mark.xfail(reason="unexpected pass", strict=False)
            def test_example() -> None: pass
            """,
            ["passed", "passed", "passed"],
            pytest.ExitCode.OK,
            None,
            None,
            None,
            id="non-strict-xpass",
        ),
        pytest.param(
            """
            import pytest
            @pytest.mark.xfail(reason="unexpected pass", strict=True)
            def test_example() -> None: pass
            """,
            ["passed", "failed", "passed"],
            pytest.ExitCode.TESTS_FAILED,
            "call",
            None,
            "XPASS(strict)",
            id="strict-xpass",
        ),
    ],
)
def test_phase_outcomes(
    plugin_runner: PluginRunner,
    source: str,
    outcomes: list[str],
    exit_code: pytest.ExitCode,
    error_phase: str | None,
    exception_type: str | None,
    message: str | None,
) -> None:
    run = plugin_runner.run(source)

    run.check_lifecycle(exit_code)
    phases = run.phases
    assert [event["event"] for event in phases] == (
        ["setup", "teardown"] if len(outcomes) == 2 else ["setup", "call", "teardown"]
    )
    assert [event["payload"]["outcome"] for event in phases] == outcomes
    collected_test = run.events[0]["tests"][0]
    assert collected_test["test_id"] == "test_sample.py::test_example"
    for event in phases:
        payload = event["payload"]
        assert payload["test"] == collected_test
        assert payload["duration"] >= 0
        if event["event"] != error_phase:
            assert payload["error_info"] is None
            continue
        error = payload["error_info"]
        assert message is not None
        assert message in str(error["repr_info"])
        if exception_type is None:
            assert error["exception"] is None
            assert error["traceback"] == []
        else:
            assert error["exception"]["type"] == exception_type
            assert message in error["exception"]["message"]
            assert any(
                frame["filename"].endswith("test_sample.py")
                for frame in error["traceback"]
            )


@pytest.mark.parametrize(
    "args",
    [
        ("--tb=long",),
        ("--tb=short",),
        ("--tb=line",),
        ("--tb=no",),
        ("--tb=long", "--showlocals"),
    ],
)
def test_traceback_modes(plugin_runner: PluginRunner, args: tuple[str, ...]) -> None:
    run = plugin_runner.run(
        """
        def test_example() -> None:
            local_context = "useful local context"
            try:
                raise KeyError("inner failure")
            except KeyError as cause:
                error = ValueError("outer failure")
                error.add_note("compatibility note")
                raise error from cause
        """,
        *args,
    )

    run.check_lifecycle(pytest.ExitCode.TESTS_FAILED)
    assert [event["event"] for event in run.phases] == ["setup", "call", "teardown"]
    error: dict[str, Any] = run.phases[1]["payload"]["error_info"]
    assert error["exception"] == {
        "type": "builtins.ValueError",
        "message": "outer failure",
        "notes": ["compatibility note"],
    }
    assert isinstance(error["repr_info"], dict)
    assert any(frame["name"] == "test_example" for frame in error["traceback"])
    if "--tb=no" not in args:
        assert "outer failure" in str(error["repr_info"])
    if "--tb=long" in args:
        assert "inner failure" in str(error["repr_info"])
    if "--showlocals" in args:
        assert "useful local context" in str(error["repr_info"])
