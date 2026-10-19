"""An error names the file the caller gave, never the copy in the job directory that the tool actually read."""

import json

from eng_toolkit.contract import ToolResult
from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import run_tool


def run(compute) -> ToolResult:
    result = run_tool(tool_name="t_paths", engine="test.engine", version="0", inputs={"a": 1}, compute=compute)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def test_a_tool_failure_names_the_callers_path_in_message_and_details(tmp_path):
    source = tmp_path / "scan.ply"
    source.write_bytes(b"data")
    seen = {}

    def compute(job):
        copy = job.input_file(str(source), param="scan_path", extensions=(".ply",))
        seen["copy"] = str(copy)
        raise ToolFailure("input.invalid", f"scan_path: cannot read {str(copy)!r}", {"path": str(copy), "nested": [{"p": str(copy)}]})

    r = run(compute)
    assert not r.ok and r.error.family == "input.invalid"
    assert seen["copy"] != str(source)
    text = json.dumps([r.error.message, r.error.details])
    assert seen["copy"].replace("\\", "\\\\") not in text and seen["copy"] not in text
    assert r.error.details["path"] == str(source) and r.error.details["nested"] == [{"p": str(source)}]
    assert repr(str(source)) in r.error.message


def test_an_engine_exception_message_names_the_callers_path(tmp_path):
    source = tmp_path / "model.step"
    source.write_bytes(b"data")
    seen = {}

    def compute(job):
        copy = job.input_file(str(source), param="model_path", extensions=(".step",))
        seen["copy"] = str(copy)
        raise RuntimeError(f"cannot open {copy}")

    r = run(compute)
    assert r.error.family == "engine.failure"
    assert str(source) in r.error.message and seen["copy"] not in r.error.message


def test_two_inputs_with_the_same_file_name_are_each_mapped_back(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = tmp_path / "a" / "x.ply", tmp_path / "b" / "x.ply"
    first.write_bytes(b"1")
    second.write_bytes(b"2")

    def compute(job):
        c1 = job.input_file(str(first), param="p1", extensions=(".ply",))
        c2 = job.input_file(str(second), param="p2", extensions=(".ply",))
        raise ToolFailure("input.invalid", f"{c2} and {c1}", {})

    r = run(compute)
    assert r.error.message == f"{second} and {first}"


def test_text_without_a_copy_path_is_left_alone(tmp_path):
    def compute(job):
        raise ToolFailure("input.invalid", r"nothing to rewrite C:\somewhere", {"n": 3, "flag": True, "none": None})

    r = run(compute)
    assert r.error.message == r"nothing to rewrite C:\somewhere" and r.error.details == {"n": 3, "flag": True, "none": None}
