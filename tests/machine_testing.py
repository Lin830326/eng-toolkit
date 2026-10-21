"""Contract assertions and physical-unit comparisons shared by machine tests."""

import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.units import ureg


def assert_finite(obj):
    if isinstance(obj, float):
        assert math.isfinite(obj)
    elif isinstance(obj, dict):
        for x in obj.values():
            assert_finite(x)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            assert_finite(x)


def validate(result):
    parsed = ToolResult.model_validate(result)
    payload = parsed.model_dump(mode="json")
    assert_finite(payload)
    json.dumps(payload, allow_nan=False)
    assert parsed.review_status == "unreviewed"
    assert parsed.provenance.engine == "eng_toolkit.machine"
    if parsed.ok:
        assert parsed.data["formulas"] and parsed.data["assumptions"] and parsed.data["validity"]
        assert parsed.warnings
    else:
        assert parsed.error.family != "engine.failure", parsed.error
        assert parsed.error.details["param"].startswith("/")
        assert parsed.value is None and not parsed.warnings
    return parsed


def call(tool, **kwargs):
    return validate(getattr(tool, "fn", tool)(**kwargs))


def ok(tool, **kwargs):
    r = call(tool, **kwargs)
    assert r.ok, r.error
    return r


def v(result, name, unit=None):
    out = result.values[name]
    return float(ureg.Quantity(out.quantity, out.unit).to(unit).magnitude) if unit else out.quantity


def close(actual, expected, rel=1e-10, abs=1e-10):
    """Default tolerance is 1e-10 relative and absolute, overridden for numerical references."""
    assert actual == pytest.approx(expected, rel=rel, abs=abs)
