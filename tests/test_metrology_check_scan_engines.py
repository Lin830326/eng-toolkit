"""metrology_check_scan without its optional engines: runs whether or not the scan extra is installed."""

import json
import sys

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology.tools import check_scan


@pytest.mark.parametrize("module", ["open3d", "OCP"])
def test_missing_engine_gives_an_install_hint(tmp_path, monkeypatch, module):
    if module == "OCP":  # open3d is checked first, so OCP is only reported when open3d imports
        pytest.importorskip("open3d", reason="the scan engines are not installed; run: uv sync --extra scan")
    scan = tmp_path / "scan.xyz"
    scan.write_text("1 2 3\n4 5 6\n")
    model = tmp_path / "model.step"
    model.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    monkeypatch.setitem(sys.modules, module, None)
    fn = getattr(check_scan, "fn", check_scan)
    r = fn(scan_path=str(scan), model_path=str(model), scan_unit="mm", tolerance="0.1 mm")
    assert isinstance(r, ToolResult)
    ToolResult.model_validate(json.loads(r.model_dump_json()))
    assert r.ok is False
    assert r.error.family == "engine.unavailable"
    assert "uv sync --extra scan" in r.error.message
    assert module in r.error.message
