"""Contract and artifact assertions for optimize tools."""

import csv
import io
import json
from pathlib import Path

import pytest

from eng_toolkit.contract import ToolResult


def validate(raw):
    result = ToolResult.model_validate(raw)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert result.review_status == "unreviewed"
    assert result.provenance.engine == "eng_toolkit.optimize"
    if result.ok:
        assert result.value is not None
        assert result.warnings
        assert all(result.data.get(k) for k in ("formulas", "assumptions", "validity"))
    else:
        assert result.error.family != "engine.failure", result.error
        assert result.error.details["param"].startswith("/")
        assert not result.warnings
    return result


def call(tool, **kwargs):
    return validate(getattr(tool, "fn", tool)(**kwargs))


def ok(tool, **kwargs):
    result = call(tool, **kwargs)
    assert result.ok, result.error
    return result


def v(result, name=None):
    return (result.value if name is None else result.values[name]).quantity


def close(actual, expected, rel=1e-8, abs=1e-8):
    assert actual == pytest.approx(expected, rel=rel, abs=abs)


def table(result, index=0):
    path = [a.path for a in result.artifacts if a.format == "csv"][index]
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8"))))
