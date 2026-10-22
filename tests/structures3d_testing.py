"""Contract assertions and unit-aware comparisons for structures tests."""

import csv
import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.units import ureg


def validate(result):
    parsed = ToolResult.model_validate(result)
    json.dumps(parsed.model_dump(mode="json"),allow_nan=False)
    assert parsed.review_status == "unreviewed"
    assert parsed.provenance.engine == "eng_toolkit.structures3d"
    if parsed.ok:
        assert parsed.warnings
        assert all(parsed.data[key] for key in ("formulas","assumptions","validity"))
        assert parsed.value is not None
    else:
        assert parsed.error.family != "engine.failure",parsed.error
        assert parsed.error.details["param"].startswith("/")
        assert parsed.value is None and not parsed.warnings
    return parsed


def call(tool, **kwargs):
    return validate(getattr(tool,"fn",tool)(**kwargs))


def ok(tool, **kwargs):
    result = call(tool,**kwargs)
    assert result.ok,result.error
    return result


def quantity(out, unit=None):
    if not isinstance(out,dict):
        out = out.model_dump()
    return float(ureg.Quantity(out["quantity"],out["unit"]).to(unit).magnitude) if unit else out["quantity"]


def v(result,name,unit=None):
    return quantity(result.values[name],unit)


def close(actual,expected,rel=1e-10,abs=1e-10):
    """Default tolerance 1e-10 relative/absolute; discretized references override."""
    assert math.isfinite(actual)
    assert actual == pytest.approx(expected,rel=rel,abs=abs)


def table(result, index=0):
    path = [a.path for a in result.artifacts if a.format == "csv"][index]
    with Path(path).open(newline="",encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


FRAME = dict(nodes="a,0 m,0 m,0 m;b,2 m,0 m,0 m", members="e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4", supports="a,fixed")
