"""Unicode validation must run before the runner writes or hashes digital inputs."""

import json

import pytest
from digital_fixtures import AND2, fn, refused, strict_json, via_gateway

from eng_toolkit.domains.digital import tools

TEXT_PARAMS = [
    ("synthesize", param) for param in ("source", "files", "top", "target", "language")
] + [
    ("check", param) for param in ("source", "files", "top", "language")
] + [
    ("equivalent", param) for param in ("source_a", "source_b", "files_a", "files_b", "top_a", "top_b", "language")
]


def arguments(tool):
    return {
        "synthesize": {"top": "t", "source": AND2},
        "check": {"top": "t", "source": AND2},
        "equivalent": {"top_a": "t", "source_a": AND2, "source_b": AND2},
    }[tool]


def assert_invalid_text(result, param):
    refused(result, "input.invalid")
    assert result.error.message == f"{param}: the text is not valid Unicode text"
    assert result.error.details == {"param": "/" + param}
    strict_json(result)
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("tool,param", TEXT_PARAMS)
@pytest.mark.parametrize("bad", ["\udcff", "\ud800"], ids=["low-surrogate", "high-surrogate"])
def test_invalid_unicode_direct(tool, param, bad, isolated_workspace):
    args = {**arguments(tool), param: bad}
    result = fn(getattr(tools, tool))(**args)
    assert_invalid_text(result, param)
    assert not isolated_workspace.exists()  # no input serialization or program run


@pytest.mark.parametrize("tool,param", TEXT_PARAMS)
@pytest.mark.parametrize("bad", ["\udcff", "\ud800"], ids=["low-surrogate", "high-surrogate"])
async def test_invalid_unicode_through_gateway(tool, param, bad, isolated_workspace):
    # The in-process MCP transport can pass Python strings that a UTF-8 JSON transport cannot.
    result = await via_gateway(tool, {**arguments(tool), param: bad})
    assert_invalid_text(result, param)
    assert not isolated_workspace.exists()


@pytest.mark.parametrize("tool,param", [("synthesize", "files"), ("check", "files"), ("equivalent", "files_a"), ("equivalent", "files_b")])
@pytest.mark.parametrize("gateway", [False, True], ids=["direct", "gateway"])
async def test_surrogate_escaped_in_json_path_list(tool, param, gateway):
    args = {**arguments(tool), param: json.dumps(["bad\udcff.v"])}
    result = await via_gateway(tool, args) if gateway else fn(getattr(tools, tool))(**args)
    assert_invalid_text(result, param)
    assert result.provenance.input_files == []
