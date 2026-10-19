"""run_isolated: untrusted parsers run in a child process with a hard time and memory limit."""

import os
import sys
from pathlib import Path

import pytest

from eng_toolkit.errors import ToolFailure
from eng_toolkit.isolated import run_isolated

HERE = str(Path(__file__).resolve().parent)
T = "isolated_targets"


def run(target, *args, timeout=20.0, memory_mb=1024):
    return run_isolated(f"{T}:{target}", list(args), timeout_s=timeout, memory_mb=memory_mb, python_path=[HERE])


def test_the_function_runs_in_another_process_and_its_value_comes_back():
    outcome = run("add", 2, 3)
    assert outcome.value["sum"] == 5
    assert outcome.value["pid"] != os.getpid()


def test_stdout_noise_of_the_child_does_not_reach_the_result():
    assert run("nothing_printed").value == {"ok": True}


def test_a_child_that_is_late_is_killed_and_reported_as_a_timeout():
    with pytest.raises(ToolFailure) as caught:
        run("sleeps", 30, timeout=1.5)
    assert caught.value.family == "engine.timeout"
    assert caught.value.details["timeout_s"] == 1.5


def test_a_child_that_dies_is_a_structured_failure_with_its_exit_code():
    with pytest.raises(ToolFailure) as caught:
        run("dies")
    assert caught.value.family == "engine.worker_died"
    assert caught.value.details["returncode"] == 3


def test_a_tool_failure_in_the_child_keeps_its_family_message_and_details():
    with pytest.raises(ToolFailure) as caught:
        run("raises_tool_failure")
    assert (caught.value.family, caught.value.message, caught.value.details) == ("drawing.corrupt", "not a drawing", {"offset": 12})


def test_any_other_exception_in_the_child_is_an_engine_failure_that_names_the_type():
    with pytest.raises(ToolFailure) as caught:
        run("raises_value_error")
    assert caught.value.family == "engine.failure"
    assert "ValueError: boom" in caught.value.message


def test_a_result_that_is_not_json_is_an_engine_failure():
    with pytest.raises(ToolFailure) as caught:
        run("returns_non_json")
    assert caught.value.family == "engine.failure"


def test_the_memory_limit_ends_a_child_that_allocates_too_much():
    with pytest.raises(ToolFailure) as caught:
        run("eats_memory", 3000, memory_mb=300, timeout=60.0)
    assert caught.value.family in ("engine.resource_limit", "engine.worker_died")
    assert caught.value.details.get("memory_mb") == 300


def test_the_memory_limit_is_reported_as_applied_where_the_platform_supports_it():
    outcome = run("add", 1, 1, memory_mb=1024)
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        assert outcome.memory_cap_applied is True


def test_the_parent_environment_is_not_mutated():
    before = dict(os.environ)
    run("add", 1, 2)
    assert dict(os.environ) == before


def test_the_readers_import_without_the_mcp_stack():
    """Every drawing_read call starts a child that imports a reader: the server framework must not come with it."""
    import subprocess

    code = "import sys, eng_toolkit.domains.drawing.dxf_read, eng_toolkit.domains.drawing.pdf_read; assert 'fastmcp' not in sys.modules and 'mcp' not in sys.modules, 'MCP stack imported'"
    done = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(Path(HERE).parent)})
    assert done.returncode == 0, done.stderr


def test_the_domain_and_its_server_are_still_importable_from_the_package():
    from eng_toolkit.domains.drawing import DOMAIN, server

    assert DOMAIN.name == "drawing" and DOMAIN.server is server and set(DOMAIN.tools) == {"make_views", "read"}


def test_a_lone_surrogate_in_the_result_becomes_the_replacement_character_and_pairs_survive():
    value = run("returns_surrogate").value
    assert value["text"] == "a�b and a valid pair \U0001F600"


def test_a_result_with_nan_is_an_engine_failure_not_invalid_json():
    with pytest.raises(ToolFailure) as caught:
        run("returns_nan")
    assert caught.value.family == "engine.failure"
