"""Contract checks and unit-aware independent comparisons for electric tests."""
import csv
import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.units import ureg


def validate(result):
    r = ToolResult.model_validate(result)
    json.dumps(r.model_dump(mode="json"), allow_nan=False)
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.electric"
    if r.ok:
        assert r.warnings and r.value is not None
        assert all(r.data[k] for k in ("formulas", "assumptions", "validity"))
    else:
        assert r.error.family != "engine.failure", r.error
        assert r.error.details["param"].startswith("/")
        assert r.value is None and not r.warnings
    return r


def call(tool, **kwargs):
    return validate(getattr(tool, "fn", tool)(**kwargs))


def ok(tool, **kwargs):
    r = call(tool, **kwargs)
    assert r.ok, r.error
    return r


def v(result, name, unit=None):
    out = result.values[name]
    source = {"VA": "W", "var": "W"}.get(out.unit, out.unit)
    target = {"VA": "W", "var": "W"}.get(unit, unit)
    return float(ureg.Quantity(out.quantity, source).to(target).magnitude) if unit else out.quantity


def close(actual, expected, rel=1e-9, abs=1e-10):
    """Default tolerance is 1e-9 relative and 1e-10 absolute; integration tests override."""
    assert math.isfinite(actual)
    assert actual == pytest.approx(expected, rel=rel, abs=abs)


def table(result, index=0):
    path = [a.path for a in result.artifacts if a.format == "csv"][index]
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))
