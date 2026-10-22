"""A lone UTF-16 surrogate in any caller string must not crash a tool (it used to raise before a ToolResult existed)."""

import asyncio
import json

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway
from eng_toolkit.runner import clean_text

BAD = "a\udfffb"


def test_clean_text_replaces_only_lone_surrogates():
    assert clean_text(BAD) == "a�b"
    assert clean_text("ok \U0001F600 中") == "ok \U0001F600 中"  # a valid pair and CJK are untouched
    assert clean_text("😀") == "😀".encode("utf-16", "surrogatepass").decode("utf-16")


@pytest.mark.parametrize("tool,kwargs", [
    ("dsp_window", {"window": BAD, "length": 64}),
    ("fluids_open_channel", {"shape": "rect" + BAD, "width": "3 m"}),
    ("structures_section", {"shape": BAD}),
    ("machine_key", {"joint_type": BAD, "length": "50 mm", "torque": "500 N*m", "shaft_diameter": "40 mm", "width": "12 mm", "height": "8 mm", "yield_strength": "400 MPa"}),
])
def test_lone_surrogate_gives_a_structured_result_and_clean_files(tool, kwargs):
    async def run():
        async with Client(gateway) as client:
            return await client.call_tool(tool, kwargs, raise_on_error=False)

    reply = asyncio.run(run())
    result = reply.structured_content
    assert result is not None, reply.content
    assert result["review_status"] == "unreviewed"
    text = json.dumps(result)  # strict: raises if a lone surrogate is still in the result
    assert "\udfff" not in text
    for artifact in result["artifacts"]:
        with open(artifact["path"], encoding="utf-8") as handle:
            handle.read()  # every artifact is valid UTF-8
