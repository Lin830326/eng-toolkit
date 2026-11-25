"""The PNG of plot_*, geometry_view and fem_view comes back as an MCP image block next to the unchanged structured result.

Through an in-process FastMCP client on the gateway: ``content`` holds the TextContent and one ImageContent (base64, image/png,
the bytes of the PNG artifact), ``structured_content`` is the same unified ToolResult with and without the block, the PNG path
stays in ``data.png_path``, a PNG above 600 kB is skipped with a warning, and ``inline_image=false`` returns the path only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit.contract import ToolResult
from eng_toolkit.gateway import images
from eng_toolkit.gateway.server import gateway
from eng_toolkit.inline_image import INLINE_IMAGE_MAX_BYTES
from tests.plot_testing import assert_finite

LINE = json.dumps([{"name": "a", "x": [0, 1, 2], "y": [0, 1, 4]}])
TWO = json.dumps([{"name": "a", "x": [1, 2], "y": [1, 2]}, {"name": "b", "x": [1, 2], "y": [2, 1]}])
BARS = json.dumps([{"name": "a", "x": ["p", "q"], "y": [1, 2]}])
PNG = b"\x89PNG\r\n\x1a\n"


async def gw(tool: str, args: dict):
    async with Client(gateway) as client:
        return await client.call_tool(tool, args, raise_on_error=False)


def call(tool: str, args: dict):
    return asyncio.run(gw(tool, args))


def kinds(reply) -> list[str]:
    return [block.type for block in reply.content]


def png_artifact(result: ToolResult) -> Path:
    return Path(next(a.path for a in result.artifacts if a.format == "png"))


def normalised(structured: dict) -> str:
    """The structured result with the per-call job id and directory taken out."""
    text = json.dumps(structured, sort_keys=True)
    job_id, job_dir = structured["data"]["job_id"], structured["data"]["job_dir"].replace("\\", "\\\\")
    return text.replace(job_dir, "<job_dir>").replace(job_id, "<job_id>")


def assert_image_block(reply) -> ToolResult:
    assert kinds(reply) == ["text", "image"], kinds(reply)
    text, image = reply.content
    assert image.mimeType == "image/png"
    payload = base64.b64decode(image.data, validate=True)
    result = ToolResult.model_validate(reply.structured_content)
    assert result.ok, result.error
    assert_finite(reply.structured_content)
    assert json.loads(text.text) == reply.structured_content  # the text block is the structured result, unchanged
    assert payload[:8] == PNG and payload == png_artifact(result).read_bytes()
    assert result.data["png_path"] == str(png_artifact(result))  # the path stays
    return result


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("plot_xy", {"series": LINE}),
        ("plot_panels", {"panels": "A|a;B|b", "series": TWO}),
        ("plot_bar", {"series": BARS}),
        ("plot_xy", {"series": LINE, "format": "png"}),
    ],
)
def test_the_png_comes_back_as_an_image_block_next_to_the_text(tool, args):
    assert_image_block(call(tool, args))


def test_the_structured_result_is_the_same_with_and_without_the_block():
    with_image = call("plot_xy", {"series": LINE, "title": "T"})
    path_only = call("plot_xy", {"series": LINE, "title": "T", "inline_image": False})
    assert kinds(with_image) == ["text", "image"] and kinds(path_only) == ["text"]
    assert normalised(with_image.structured_content) == normalised(path_only.structured_content)
    ToolResult.model_validate(path_only.structured_content)
    assert path_only.structured_content["data"]["png_path"].endswith("plot.png")


def test_the_default_is_on_and_a_truthy_string_counts_as_true():
    assert kinds(call("plot_xy", {"series": LINE})) == ["text", "image"]
    assert kinds(call("plot_xy", {"series": LINE, "inline_image": "true"})) == ["text", "image"]
    assert kinds(call("plot_xy", {"series": LINE, "inline_image": "false"})) == ["text"]


def test_format_svg_has_no_png_and_so_no_image():
    reply = call("plot_xy", {"series": LINE, "format": "svg"})
    assert kinds(reply) == ["text"] and reply.structured_content["ok"] is True and "png_path" not in reply.structured_content["data"]


def test_a_refusal_has_no_image_and_a_bad_flag_is_a_refusal():
    reply = call("plot_xy", {"series": LINE, "inline_image": "maybe"})
    assert kinds(reply) == ["text"] and reply.structured_content["ok"] is False
    assert reply.structured_content["error"]["family"] == "input.invalid" and reply.structured_content["error"]["details"]["param"] == "/inline_image"
    refused = call("plot_xy", {"series": "[]"})
    assert kinds(refused) == ["text"] and refused.structured_content["ok"] is False


def test_a_png_above_the_limit_is_skipped_with_a_warning_and_keeps_its_path(monkeypatch):
    monkeypatch.setattr(images, "INLINE_IMAGE_MAX_BYTES", 1000)
    reply = call("plot_xy", {"series": LINE})
    assert kinds(reply) == ["text"]
    result = ToolResult.model_validate(reply.structured_content)
    assert result.ok and png_artifact(result).stat().st_size > 1000 and result.data["png_path"] == str(png_artifact(result))
    note = result.warnings[-1]
    assert "above the 600 kB limit" in note and "data.png_path" in note and re.search(r"PNG is \d+ kB", note)
    assert json.loads(reply.content[0].text) == reply.structured_content  # the text block shows the same warning
    again = call("plot_xy", {"series": LINE, "inline_image": False})
    assert note not in again.structured_content["warnings"]


def test_a_real_picture_above_600_kB_is_skipped():
    points = [{"name": f"s{i}", "x": [(j * 7919 + i * 31) % 1009 / 10 for j in range(2500)], "y": [(j * 104729 + i * 17) % 997 / 10 for j in range(2500)]} for i in range(6)]
    reply = call("plot_xy", {"series": json.dumps(points), "kind": "scatter", "width": 2400, "height": 2400})
    result = ToolResult.model_validate(reply.structured_content)
    size = png_artifact(result).stat().st_size
    assert size > INLINE_IMAGE_MAX_BYTES, size
    assert kinds(reply) == ["text"] and "above the 600 kB limit" in result.warnings[-1]


def test_a_png_whose_bytes_changed_after_the_call_is_not_attached(tmp_path):
    reply = call("plot_xy", {"series": LINE})
    structured = dict(reply.structured_content)
    png_artifact(ToolResult.model_validate(structured)).write_bytes(PNG + b"tampered")
    assert images.attach_image(structured) == (None, None)


def test_only_a_png_inside_the_job_directory_is_read(tmp_path):
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(PNG + b"x")
    structured = {"ok": True, "data": {"job_dir": str(tmp_path / "job")}, "artifacts": [{"path": str(outside), "format": "png", "sha256": "0"}]}
    assert images.attach_image(structured) == (None, None)


def test_tools_without_the_parameter_are_left_alone():
    reply = call("toolkit_list_tools", {})
    assert kinds(reply) == ["text"]


def test_exactly_the_picture_tools_declare_the_parameter():
    async def read():
        async with Client(gateway) as client:
            return {t.name: t for t in await client.list_tools()}

    tools = asyncio.run(read())
    declared = {name for name, t in tools.items() if "inline_image" in t.input_schema["properties"]}
    assert declared == {"plot_xy", "plot_panels", "plot_bar", "geometry_view", "fem_view"}
    for name in declared:
        prop = tools[name].input_schema["properties"]["inline_image"]
        assert prop["type"] == "boolean" and prop["default"] is True and len(prop["description"]) < 140


# --------------------------------------------------------------------------- the other two picture tools


def test_fem_view_returns_its_png_as_an_image(tmp_path):
    from tests.test_fem_view_preview import fake_job

    job = fake_job(tmp_path / "jobs" / "20261023T000000Z-00000000-fem_static")
    reply = call("fem_view", {"result_dir": str(job)})
    assert kinds(reply) == ["text", "image"], reply.structured_content
    payload = base64.b64decode(reply.content[1].data)
    result = ToolResult.model_validate(reply.structured_content)
    assert result.ok and payload == png_artifact(result).read_bytes() and payload[:8] == PNG
    assert kinds(call("fem_view", {"result_dir": str(job), "inline_image": False})) == ["text"]
    assert kinds(call("fem_view", {"result_dir": str(job), "preview": "none"})) == ["text"]


def test_geometry_view_returns_its_png_as_an_image(tmp_path):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra cad")
    import cad_fixtures as cad

    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    reply = call("geometry_view", {"model_path": str(source), "name": "probe"})
    assert kinds(reply) == ["text", "image"], reply.structured_content
    result = ToolResult.model_validate(reply.structured_content)
    assert result.ok and base64.b64decode(reply.content[1].data) == png_artifact(result).read_bytes()
    assert kinds(call("geometry_view", {"model_path": str(source), "name": "probe", "inline_image": False})) == ["text"]
