"""Contract and quantity assertions for the fatigue domain."""

import csv
import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.units import ureg


def validate(result):
    parsed = ToolResult.model_validate(result)
    json.dumps(parsed.model_dump(mode="json"), allow_nan=False)
    assert parsed.review_status == "unreviewed"
    assert parsed.provenance.engine == "eng_toolkit.fatigue"
    if parsed.ok:
        assert parsed.warnings and parsed.value is not None
        assert all(parsed.data[k] for k in ("formulas", "assumptions", "validity"))
    else:
        assert parsed.error.family != "engine.failure", parsed.error
        assert parsed.error.details["param"].startswith("/")
        assert parsed.value is None and not parsed.warnings
    return parsed


def call(tool, **kwargs):
    return validate(getattr(tool, "fn", tool)(**kwargs))


def ok(tool, **kwargs):
    result = call(tool, **kwargs)
    assert result.ok, result.error
    return result


def v(result, name, unit=None):
    value = result.values[name]
    return float(ureg.Quantity(value.quantity, value.unit).to(unit).magnitude) if unit else value.quantity


def close(actual, expected, rel=1e-9, abs=1e-12):
    """Default tolerance 1e-9 relative / 1e-12 absolute; empirical references override."""
    assert math.isfinite(actual)
    assert actual == pytest.approx(expected, rel=rel, abs=abs)


def table(result):
    path = next(a.path for a in result.artifacts if a.format == "csv")
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


SN = dict(mode="fit", points="400 MPa,1000;200 MPa,1000000")
RF = dict(history="[0,100,-100,0]", sample_scale="1 MPa")
SL = dict(elastic_modulus="200 GPa", fatigue_strength="1000 MPa", fatigue_ductility="0.5 dimensionless", b=-.1, c=-.5)
FM = dict(crack_size="10 mm", stress="100 MPa")
MT = dict(stress="300 MPa", strain="0.1 dimensionless", uniform_strain="0.2 dimensionless")
