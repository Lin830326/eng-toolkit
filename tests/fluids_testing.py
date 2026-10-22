"""Contract and independent reference arithmetic for fluids tests."""

import csv
import json
import math
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.units import ureg

G = 9.80665
NETWORK = dict(nodes="r,0 m,head,20 m;j,0 m,demand,1 L/s", pipes="p,r,j,10 m,50 mm,0 mm,0")
PUMP = dict(pump_points="p,0 m^3/s,20 m,0.6;p,0.1 m^3/s,0 m,0.8", static_head="5 m", system_coefficient="1000 s^2/m^5")
CHANNEL = dict(width="2 m", discharge="2 m^3/s")
SURGE = dict(length="1000 m", diameter="0.5 m", wall_thickness="10 mm", bulk_modulus="2.2 GPa", youngs_modulus="200 GPa", initial_velocity="1 m/s", closure_time="0 s")
EXTERNAL = dict(length="10 mm", velocity="1 m/s")
NOZZLE = dict(mode="nozzle", area="1 cm^2", stagnation_pressure="2 bar", stagnation_temperature="300 K", back_pressure="1 bar")


def validate(result):
    result = ToolResult.model_validate(result)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert result.review_status == "unreviewed"
    assert result.provenance.engine == "eng_toolkit.fluids"
    if result.ok:
        assert result.warnings and result.value is not None
        assert all(result.data[k] for k in ("formulas", "assumptions", "validity"))
    else:
        assert result.error.family != "engine.failure", result.error
        assert result.error.details["param"].startswith("/")
        assert result.value is None and not result.warnings
    return result


def call(tool, **kwargs):
    return validate(getattr(tool, "fn", tool)(**kwargs))


def ok(tool, **kwargs):
    result = call(tool, **kwargs)
    assert result.ok, result.error
    return result


def v(result, name, unit=None):
    out = result.values[name]
    return float(ureg.Quantity(out.quantity, out.unit).to(unit).magnitude) if unit else out.quantity


def close(actual, expected, rel=1e-9, abs=1e-12):
    """Default tolerance 1e-9 relative and 1e-12 absolute; tests override as noted."""
    assert math.isfinite(actual)
    assert actual == pytest.approx(expected, rel=rel, abs=abs)


def table(result):
    path = next(a.path for a in result.artifacts if a.format == "csv")
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def colebrook_bisection(re, relative):
    """Independent factor-space bisection; solver under test uses Newton on 1/sqrt(f)."""
    low, high = .001, .2
    for _ in range(100):
        f = (low+high)/2
        residual = 1/math.sqrt(f)+2*math.log10(relative/3.7+2.51/(re*math.sqrt(f)))
        if residual > 0:
            low = f
        else:
            high = f
    return (low+high)/2
