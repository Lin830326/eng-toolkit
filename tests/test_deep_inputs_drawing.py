"""A refusal about the content of a file argument names that argument (details.param): drawing_make_views and drawing_read."""

import pytest
from fastmcp import Client

from eng_toolkit.gateway.server import gateway

pytest.importorskip("ezdxf", reason="ezdxf is not installed; run: uv sync --extra drawing")
pytest.importorskip("pdfminer", reason="pdfminer.six is not installed; run: uv sync --extra drawing")

MODEL_SUFFIXES = [".step", ".stp", ".iges", ".igs", ".brep"]
DRAWING_SUFFIXES = [".dxf", ".pdf"]
GARBAGE = b"this is not a CAD or drawing file \x01\x02\x03" * 10


@pytest.fixture
def allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(tmp_path))
    return tmp_path


async def call(tool: str, args: dict) -> dict:
    async with Client(gateway) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return result.structured_content


@pytest.mark.parametrize("suffix", MODEL_SUFFIXES)
@pytest.mark.parametrize("content", [b"", GARBAGE], ids=["empty", "garbage"])
async def test_make_views_unreadable_model_names_model_path(allowed, suffix, content):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra drawing")
    path = allowed / f"model{suffix}"
    path.write_bytes(content)
    payload = await call("drawing_make_views", {"model_path": str(path)})
    assert payload["ok"] is False
    assert payload["error"]["family"] == "input.invalid", payload["error"]
    assert payload["error"]["details"]["param"] == "/model_path"


async def test_make_views_step_without_a_shape_names_model_path(allowed):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra drawing")
    path = allowed / "empty-data.step"
    path.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")
    payload = await call("drawing_make_views", {"model_path": str(path)})
    assert payload["error"]["family"] == "input.invalid" and payload["error"]["details"]["param"] == "/model_path"


async def test_make_views_large_model_file_names_model_path(allowed):
    pytest.importorskip("OCP", reason="OCP is not installed; run: uv sync --extra drawing")
    path = allowed / "big.step"
    path.write_bytes(b"ISO-10303-21;\n" + b"x" * (20 * 1024 * 1024))
    payload = await call("drawing_make_views", {"model_path": str(path)})
    assert payload["ok"] is False
    assert payload["error"]["details"]["param"] == "/model_path"


@pytest.mark.parametrize("suffix", DRAWING_SUFFIXES)
@pytest.mark.parametrize("content", [b"", GARBAGE], ids=["empty", "garbage"])
async def test_read_corrupt_drawing_names_path(allowed, suffix, content):
    path = allowed / f"drawing{suffix}"
    path.write_bytes(content)
    payload = await call("drawing_read", {"path": str(path)})
    assert payload["ok"] is False
    assert payload["error"]["family"] == "drawing.corrupt", payload["error"]
    assert payload["error"]["details"]["param"] == "/path"


async def test_read_directory_named_like_a_drawing_names_path(allowed):
    (allowed / "folder.dxf").mkdir()
    payload = await call("drawing_read", {"path": str(allowed / "folder.dxf")})
    assert payload["ok"] is False
    assert payload["error"]["details"]["param"] == "/path"


async def test_read_dwg_whose_converter_output_is_corrupt_names_path(allowed, monkeypatch):
    import dwg_fixtures as F

    F.install_fake(allowed, monkeypatch, mode="garbage")
    path = F.write_dwg(allowed / "part.dwg")
    payload = await call("drawing_read", {"path": str(path)})
    assert payload["error"]["family"] == "drawing.corrupt", payload["error"]
    assert payload["error"]["details"]["param"] == "/path"
