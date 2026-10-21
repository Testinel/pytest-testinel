import pytest

from .support import PluginRunner, RUN_URL


def test_collected_test_metadata(plugin_runner: PluginRunner) -> None:
    run = plugin_runner.run(
        '''
        """Module description."""
        import pytest

        def test_function() -> None:
            """Function description."""

        class TestGroup:
            """Class description."""
            def test_inherited_description(self) -> None: pass
            def test_own_description(self) -> None:
                """Method description."""

        @pytest.mark.parametrize("value", [1, 2], ids=["one", "two"])
        def test_parameter(value: int) -> None:
            """Parameter description."""
            assert value > 0
        '''
    )

    run.check_lifecycle(pytest.ExitCode.OK)
    tests = run.events[0]["tests"]
    expected = {
        "test_sample.py::test_function": "Function description.",
        "test_sample.py::TestGroup::test_inherited_description": "Class description.",
        "test_sample.py::TestGroup::test_own_description": "Method description.",
        "test_sample.py::test_parameter[one]": "Parameter description.",
        "test_sample.py::test_parameter[two]": "Parameter description.",
    }
    assert {test["test_id"]: test["description"] for test in tests} == expected
    for test in tests:
        assert test["location"][0] == "test_sample.py"
        assert isinstance(test["location"][1], int)
        assert test["location"][1] >= 0
        assert test["location"][2] == test["test_id"].split("::", 1)[1].replace(
            "::", "."
        )
    assert len(run.phases) == 3 * len(tests)
    for index, test in enumerate(tests):
        phases = run.phases[index * 3 : (index + 1) * 3]
        assert [event["event"] for event in phases] == ["setup", "call", "teardown"]
        assert all(event["payload"]["test"] == test for event in phases)


@pytest.mark.parametrize(
    "args",
    [(), ("--no-summary",), ("-p", "no:terminal")],
    ids=["terminal", "no-summary", "no-terminal"],
)
def test_run_urls(plugin_runner: PluginRunner, args: tuple[str, ...]) -> None:
    run = plugin_runner.run("def test_example() -> None: pass", *args)

    run.check_lifecycle(pytest.ExitCode.OK)
    output = (
        run.result.stderr.str() if "no:terminal" in args else run.result.stdout.str()
    )
    url = RUN_URL + "?utm_source=pytest-testinel&utm_medium=cli"
    assert output.count(f"View live report: {url}") == 1
    assert output.count(f"View run report: {url}") == 1
    assert output.index("View live report:") < output.index("View run report:")
    if not args:
        assert output.index("Other plugin summary") < output.index("View run report:")


@pytest.mark.parametrize(
    ("source", "args", "exit_code", "collected", "phases"),
    [
        pytest.param(
            "# No tests", (), pytest.ExitCode.NO_TESTS_COLLECTED, 0, [], id="empty"
        ),
        pytest.param(
            'raise RuntimeError("collection failure")',
            (),
            pytest.ExitCode.INTERRUPTED,
            0,
            [],
            id="collection-error",
        ),
        pytest.param(
            """
            def test_first() -> None: assert False, "stop here"
            def test_second() -> None: pass
            """,
            ("-x",),
            pytest.ExitCode.TESTS_FAILED,
            2,
            ["setup", "call", "teardown"],
            id="exitfirst",
        ),
    ],
)
def test_session_finalization(
    plugin_runner: PluginRunner,
    source: str,
    args: tuple[str, ...],
    exit_code: pytest.ExitCode,
    collected: int,
    phases: list[str],
) -> None:
    run = plugin_runner.run(source, *args)

    run.check_lifecycle(exit_code)
    assert len(run.events[0]["tests"]) == collected
    assert [event["event"] for event in run.phases] == phases
    assert run.output.count("View run report:") == 1
    if phases:
        assert all(
            event["payload"]["test"]["test_id"] == "test_sample.py::test_first"
            for event in run.phases
        )
        assert run.phases[1]["payload"]["outcome"] == "failed"
    if exit_code == pytest.ExitCode.INTERRUPTED:
        assert "collection failure" in run.output
