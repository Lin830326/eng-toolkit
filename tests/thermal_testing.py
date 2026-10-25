"""Helpers shared by the ``test_thermal_*`` files: call a tool function directly and check the contract."""

from __future__ import annotations
from eng_toolkit.errors import json_pointer

import math
from typing import Any

from eng_toolkit.contract import ToolResult


def assert_finite(obj: Any, path: str = "result") -> None:
    """No NaN / infinity anywhere in a result (MCP clients reject such JSON)."""
    if isinstance(obj, float):
        assert math.isfinite(obj), f"{path} is {obj}"
    elif isinstance(obj, dict):
        for key, item in obj.items():
            assert_finite(item, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for index, item in enumerate(obj):
            assert_finite(item, f"{path}[{index}]")


def call(tool, **kwargs) -> ToolResult:
    fn = getattr(tool, "fn", tool)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(result.model_dump(mode="json"))
    assert_finite(result.model_dump(mode="python"))
    assert result.review_status == "unreviewed"
    return result


def ok(tool, **kwargs) -> ToolResult:
    result = call(tool, **kwargs)
    assert result.ok, result.error
    return result


def fails(tool, family: str, param: str | None = None, **kwargs) -> ToolResult:
    result = call(tool, **kwargs)
    assert result.ok is False and result.value is None, result.values
    assert result.error.family == family, result.error
    if param is not None:
        assert result.error.details.get("param") == json_pointer(param), result.error
    assert result.warnings == []
    return result


def v(result: ToolResult, key: str) -> float:
    return result.values[key].quantity
