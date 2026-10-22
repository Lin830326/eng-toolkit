"""Helpers shared by the ``test_plot_*`` files: call a tool directly, check the contract, read and vet the SVG."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from eng_toolkit.contract import ToolResult

SVG_NS = "{http://www.w3.org/2000/svg}"
ALLOWED_ELEMENTS = {"svg", "g", "path", "polyline", "line", "rect", "circle", "text", "title", "desc", "style", "clipPath", "defs"}
ALLOWED_ATTRIBUTES = {
    "id", "viewBox", "width", "height", "role", "aria-labelledby", "font-family", "font-size", "font-weight", "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "d", "points", "fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linejoin", "stroke-linecap",
    "fill-opacity", "class", "clip-path", "text-anchor", "transform",
}


def assert_finite(obj: Any, path: str = "result") -> None:
    if isinstance(obj, float):
        assert math.isfinite(obj), f"{path} is {obj}"
    elif isinstance(obj, dict):
        for key, item in obj.items():
            assert_finite(item, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for index, item in enumerate(obj):
            assert_finite(item, f"{path}[{index}]")


def call(tool, **kwargs) -> ToolResult:
    result = getattr(tool, "fn", tool)(**kwargs)
    assert isinstance(result, ToolResult)
    dumped = result.model_dump(mode="json")
    json.dumps(dumped, allow_nan=False)  # strict JSON: no NaN / Infinity anywhere
    ToolResult.model_validate(dumped)
    assert_finite(result.model_dump(mode="python"))
    assert result.review_status == "unreviewed"
    assert result.provenance.engine == "eng_toolkit.plot"
    return result


def ok(tool, **kwargs) -> ToolResult:
    result = call(tool, **kwargs)
    assert result.ok, result.error
    assert result.warnings and "Chart rendering only" in result.warnings[0]
    svg = svg_text(result)
    assert_safe_svg(svg)
    return result


def fails(tool, family: str, param: str | None = None, **kwargs) -> ToolResult:
    result = call(tool, **kwargs)
    assert result.ok is False and result.value is None, result.values
    assert result.error.family == family, result.error
    assert str(result.error.details.get("param", "")).startswith("/"), result.error
    if param is not None:
        assert result.error.details["param"] == "/" + param.lstrip("/"), result.error
    assert result.warnings == []
    assert len(result.error.message) < 1500
    return result


def artifact(result: ToolResult, name: str) -> Path:
    return Path(next(a.path for a in result.artifacts if a.path.endswith(name)))


def svg_text(result: ToolResult) -> str:
    return artifact(result, "plot.svg").read_text(encoding="utf-8")


def plot_data(result: ToolResult) -> dict:
    return json.loads(artifact(result, "plot_data.json").read_text(encoding="utf-8"))


def root(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def local(tag: str) -> str:
    return tag[len(SVG_NS):] if tag.startswith(SVG_NS) else tag


def assert_safe_svg(svg: str) -> ET.Element:
    """Well-formed XML, only whitelisted elements and attributes, no script, no external reference, no event handler."""
    tree = ET.fromstring(svg)
    assert local(tree.tag) == "svg" and tree.get("viewBox") and tree.get("role") == "img"
    assert tree.find(f"{SVG_NS}title") is not None and tree.find(f"{SVG_NS}desc") is not None
    for el in tree.iter():
        name = local(el.tag)
        assert el.tag.startswith(SVG_NS), el.tag
        assert name in ALLOWED_ELEMENTS, name
        for attr, value in el.attrib.items():
            assert attr in ALLOWED_ATTRIBUTES, (name, attr)
            assert not attr.lower().startswith("on"), attr
            assert "href" not in attr.lower()
            if "url(" in value:
                assert re.fullmatch(r"url\(#[A-Za-z0-9-]+\)", value), value
    lowered = svg.lower()
    for bad in ("<script", "<foreignobject", "<image", "<use", "<a ", "<iframe", "<!entity", "<!doctype", "<?xml-stylesheet"):
        assert bad not in lowered, bad  # caller text can mention these words, but only escaped (&lt;script ...)
    for style in tree.iter(f"{SVG_NS}style"):
        css = (style.text or "").lower()
        assert "url(" not in css and "@import" not in css and "@font-face" not in css and "expression(" not in css
    return tree


def elements(svg: str, name: str) -> list[ET.Element]:
    return list(root(svg).iter(f"{SVG_NS}{name}"))


def polylines(svg: str) -> list[list[tuple[float, float]]]:
    out = []
    for el in elements(svg, "polyline"):
        pts = [tuple(float(v) for v in p.split(",")) for p in el.get("points").split()]
        out.append(pts)
    return out


def texts(svg: str) -> list[str]:
    return [el.text or "" for el in elements(svg, "text")]
