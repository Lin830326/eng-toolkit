"""ToolFailure is the one structured error carrier; UnitsError is a specialisation of it."""

from eng_toolkit.errors import ToolFailure
from eng_toolkit.runner import run_tool
from eng_toolkit.units import UnitsError


def test_units_error_is_a_tool_failure_and_a_value_error():
    err = UnitsError("units.parse_error", "bad", {"param": "x"})
    assert isinstance(err, ToolFailure)
    assert isinstance(err, ValueError)
    assert (err.family, err.message, err.details) == ("units.parse_error", "bad", {"param": "x"})
    assert err.to_dict() == {"family": "units.parse_error", "message": "bad", "details": {"param": "x"}}
    assert str(err) == "bad"


def test_tool_failure_defaults_to_empty_details_and_is_a_value_error():
    err = ToolFailure("geometry.x", "nope")
    assert isinstance(err, ValueError)
    assert err.details == {}
    assert err.to_dict() == {"family": "geometry.x", "message": "nope", "details": {}}


def test_run_tool_converts_a_tool_failure_into_a_failed_result_without_warnings():
    def compute(_):
        raise ToolFailure("geometry.x", "the part is empty", {"where": "body"})

    r = run_tool(
        tool_name="test_failure",
        engine="test",
        version="0",
        inputs={"x": "1 m"},
        compute=compute,
        standing_warnings=("scope warning that only applies to a result",),
    )
    assert r.ok is False
    assert r.error.family == "geometry.x"
    assert r.error.message == "the part is empty"
    assert r.error.details == {"where": "body"}
    assert r.warnings == []


def test_run_tool_still_converts_a_units_error():
    def compute(_):
        raise UnitsError("units.bare_number", "no unit", {"param": "p"})

    r = run_tool(tool_name="test_failure", engine="test", version="0", inputs={}, compute=compute)
    assert r.ok is False
    assert r.error.family == "units.bare_number"
    assert r.error.details == {"param": "p"}
