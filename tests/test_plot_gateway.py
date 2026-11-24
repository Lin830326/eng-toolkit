"""The plot domain through a real MCP client against the gateway: registry, schemas, CSVs from real tools, refusals, strict JSON."""

import json
import re
import time
from pathlib import Path

import pytest
from fastmcp import Client

import eng_toolkit.domains.plot as plot_package
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains import all_domains
from eng_toolkit.gateway.server import gateway
from tests.plot_testing import assert_finite, assert_safe_svg, polylines
from timing import budget

PLOT = {"plot_xy", "plot_panels", "plot_bar"}
LINE = json.dumps([{"name": "a", "x": [0, 1, 2], "y": [0, 1, 4]}])


def _no_constants(name: str):
    raise AssertionError(f"the response contains the JSON constant {name}")


async def _call(client: Client, tool: str, args: dict, *, expect_ok: bool | None = True) -> ToolResult | None:
    result = await client.call_tool(tool, args, raise_on_error=False)
    if result.is_error:
        assert expect_ok is not True, (tool, result.content)
        return None
    for block in result.content:
        json.loads(block.text, parse_constant=_no_constants)
    parsed = ToolResult.model_validate(result.structured_content)
    assert_finite(result.structured_content)
    assert parsed.review_status == "unreviewed" and parsed.provenance.engine == "eng_toolkit.plot"
    if expect_ok is True:
        assert parsed.ok, parsed.error
        assert_safe_svg(Path(next(a.path for a in parsed.artifacts if a.path.endswith("plot.svg"))).read_text(encoding="utf-8"))
    return parsed


def _artifact(result, suffix: str) -> str:
    return next(a.path for a in result.artifacts if a.path.endswith(suffix))


def test_registry_has_three_plot_tools_and_only_numpy():
    domain = next(d for d in all_domains() if d.name == "plot")
    assert set(domain.tools) == {"xy", "panels", "bar"} and domain.engines == ("numpy",)


@pytest.mark.asyncio
async def test_schemas_are_basic_types_with_distinct_specific_descriptions():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert PLOT <= set(tools)
    for name in PLOT:
        assert 80 < len(tools[name].description) < 650, name
        props = tools[name].input_schema.get("properties", {})
        seen: dict[str, str] = {}
        for pname, prop in props.items():
            assert prop.get("type") in {"string", "number", "integer", "boolean"}, (name, pname)
            assert "anyOf" not in prop and "oneOf" not in prop
            desc = prop.get("description", "")
            assert 20 < len(desc) < 140, (name, pname, len(desc))
            assert desc not in seen, f"{name}: '{pname}' and '{seen.get(desc)}' share the description {desc!r}"
            seen[desc] = pname


@pytest.mark.asyncio
async def test_toolkit_lists_the_plot_domain():
    async with Client(gateway) as client:
        r = await client.call_tool("toolkit_list_tools", {})
    assert all(name in json.dumps(r.structured_content) for name in PLOT)


@pytest.mark.asyncio
async def test_csvs_written_by_real_tools_plot(monkeypatch, tmp_path):
    # the read allow-list is on: a tool artifact in the workspace is still readable, nothing else is
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    async with Client(gateway) as client:
        fd = await client.call_tool("dsp_filter_design", {"family": "butterworth", "response": "lowpass", "sample_rate": "8 kHz", "order": 4, "cutoff": "1 kHz"})
        csv_path = _artifact(ToolResult.model_validate(fd.structured_content), "filter_response.csv")
        bode = await _call(client, "plot_panels", {"data_file": csv_path, "panels": "Magnitude|magnitude_db|dB;Phase|phase_deg|deg", "x_label": "Frequency (Hz)"})
        assert bode.values["points_drawn"].quantity == 2 * 512 and bode.data["svg_inline"].startswith("<svg")
        # a log x axis on this table is refused, pointing at the 0 Hz row (line 2) of the frequency column
        refused = await _call(client, "plot_xy", {"data_file": csv_path, "x_scale": "log"}, expect_ok=False)
        assert refused.error.details["column"] == "frequency_hz" and refused.error.details["line"] == 2

        frame = await client.call_tool("structures_frame", {"nodes": "a,0 m,0 m;b,4 m,0 m", "members": "e,a,b,200 GPa,1000 mm^2,1e6 mm^4",
                                                           "supports": "a,pinned;b,roller_y", "member_loads": "e,uniform,local,-10 kN/m,-10 kN/m"})
        stations = _artifact(ToolResult.model_validate(frame.structured_content), "member_1_stations.csv")
        shear = await _call(client, "plot_xy", {"data_file": stations, "x_column": "x_m", "y_columns": "V_N", "fill": "zero", "y_label": "V (N)"})
        assert shear.data["series"][0]["y_max"] == pytest.approx(20000.0) and shear.data["series"][0]["y_min"] == pytest.approx(-20000.0)  # wL/2 = 10 kN/m x 4 m / 2
        moment = await _call(client, "plot_xy", {"data_file": stations, "x_column": "x_m", "y_columns": "M_N_m", "fill": "zero", "theme": "print"})
        assert moment.data["series"][0]["y_max"] == pytest.approx(20000.0)  # wL^2/8 = 10 x 16 / 8 kN m

        channel = await client.call_tool("fluids_open_channel", {"width": "2 m", "discharge": "2 m^3/s", "mode": "profile", "boundary_depth": "1.2 m",
                                                                 "profile_length": "5 m", "profile_steps": 20})
        profile = _artifact(ToolResult.model_validate(channel.structured_content), "channel_profile.csv")
        water = await _call(client, "plot_xy", {"data_file": profile, "x_column": "x_m", "y_columns": "bed_m,water_surface_m,total_head_m", "x_label": "x (m)"})
        assert water.values["series"].quantity == 3

        rain = await client.call_tool("fatigue_rainflow", {"history": "[0,100,-100,50,-20,80,-60,0]", "sample_scale": "1 MPa"})
        cycles = _artifact(ToolResult.model_validate(rain.structured_content), "rainflow_cycles.csv")
        hist = await _call(client, "plot_bar", {"mode": "histogram", "data_file": cycles, "y_columns": "range_MPa", "bins": 4})
        assert sum(hist.data["histogram"]["counts"]) == hist.data["histogram"]["values"]

        outside = tmp_path / "outside.csv"
        outside.write_text("x,y\n1,2\n", encoding="utf-8")
        r = await _call(client, "plot_xy", {"data_file": str(outside)}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed" and r.error.details["param"] == "/data_file"
        r = await _call(client, "plot_bar", {"mode": "histogram", "data_file": str(allowed / ".." / "outside.csv")}, expect_ok=False)
        assert r.error.family == "input.path_not_allowed"


@pytest.mark.asyncio
async def test_equal_values_are_padded_through_the_client():
    async with Client(gateway) as client:
        r = await _call(client, "plot_xy", {"series": json.dumps([{"name": "c", "x": [1, 2, 3], "y": [7, 7, 7]}])})
    assert any("padded" in w for w in r.warnings) and polylines(r.data["svg_inline"])


def _csv(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_hostile_requests_are_refused_cleanly(tmp_path):
    good = _csv(tmp_path, "good.csv", "x,y,z\n1,2,3\n2,3,4\n")
    cases = [
        ("plot_xy", {"data_file": good, "y_columns": "nope"}, "input.invalid", "/y_columns"),
        ("plot_xy", {"data_file": _csv(tmp_path, "ragged.csv", "x,y\n1,2\n3\n")}, "input.invalid", "/data_file/3"),
        ("plot_xy", {"data_file": _csv(tmp_path, "text.csv", "x,y\n1,2\n2,abc\n"), "y_columns": "y"}, "input.invalid", "/data_file/3/y"),
        ("plot_xy", {"data_file": _csv(tmp_path, "neg.csv", "x,y\n1,2\n2,-3\n"), "y_scale": "log"}, "input.invalid", "/data_file/3/y"),
        ("plot_xy", {"data_file": _csv(tmp_path, "huge.csv", "x,y\n1,2\n2,1e308\n"), "y_columns": "y"}, "input.out_of_range", "/data_file/3/y"),
        ("plot_xy", {"data_file": _csv(tmp_path, "nan.csv", "x,y\n1,NaN\n2,3\n"), "y_columns": "y"}, "input.invalid", "/data_file/2/y"),
        ("plot_xy", {"series": json.dumps([{"name": f"s{i}", "y": [1, 2]} for i in range(21)])}, "input.out_of_range", "/series"),
        ("plot_xy", {"series": '[{"y": [1, NaN, 3]}]'}, "input.invalid", "/series"),
        ("plot_xy", {"series": '[{"y": [1, 1e308, 3]}]'}, "input.out_of_range", "/series/0/y/1"),
        ("plot_xy", {"series": LINE, "theme": "<script>"}, "input.invalid", "/theme"),
        ("plot_xy", {"series": LINE, "kind": "pie"}, "input.invalid", "/kind"),
        ("plot_xy", {"series": LINE, "width": 100000}, "input.out_of_range", "/width"),
        ("plot_xy", {"series": LINE, "height": 10 ** 12}, "input.out_of_range", "/height"),
        ("plot_xy", {"series": LINE, "x_range": "3,3"}, "input.invalid", "/x_range"),
        ("plot_xy", {"series": LINE, "title": "<" * 6000}, "input.out_of_range", "/title"),
        ("plot_xy", {"series": LINE, "hlines": "1," + "x" * 6000}, "input.out_of_range", "/hlines"),
        ("plot_xy", {"data_file": "C:/" + "a" * 6000 + ".csv"}, "input.out_of_range", "/data_file"),
        ("plot_panels", {"series": LINE, "panels": "(" * 6000}, "input.out_of_range", "/panels"),
        ("plot_panels", {"series": LINE, "panels": "a|a;b|a", "x_label": "x" * 6000}, "input.out_of_range", "/x_label"),
        ("plot_bar", {"series": LINE, "y_label": "y" * 6000}, "input.out_of_range", "/y_label"),
        ("plot_bar", {"mode": "histogram", "series": LINE, "bin_edges": "(" * 6000}, "input.out_of_range", "/bin_edges"),
        ("plot_bar", {"series": json.dumps([{"name": "a", "x": ["q"], "y": [1]}]), "stacking": "x" * 6000}, "input.invalid", "/stacking"),
    ]
    async with Client(gateway) as client:
        await client.list_tools()
        for tool, args, family, param in cases:
            start = time.perf_counter()
            parsed = await _call(client, tool, args, expect_ok=False)
            elapsed = time.perf_counter() - start
            assert parsed is not None, (tool, args)
            assert parsed.ok is False and parsed.error.family == family and parsed.error.details["param"] == param, (tool, param, parsed.error)
            assert len(parsed.error.message) < 1500 and parsed.warnings == []
            assert elapsed < budget(1.0), (tool, param, elapsed)


@pytest.mark.asyncio
async def test_non_finite_number_parameters_never_succeed():
    async with Client(gateway) as client:
        for bad in (float("nan"), float("inf")):
            for tool, key, extra in (("plot_xy", "width", {"series": LINE}), ("plot_bar", "bins", {"mode": "histogram", "series": LINE})):
                try:
                    result = await client.call_tool(tool, {**extra, key: bad}, raise_on_error=False)
                except Exception:  # the transport may refuse a non-JSON number
                    continue
                if result.is_error:
                    continue
                assert ToolResult.model_validate(result.structured_content).ok is False


SOURCE_DIR = Path(plot_package.__file__).parent


def test_the_package_never_executes_text_or_imports_a_plotting_library():
    forbidden = [r"\beval\s*\(", r"\bexec\s*\(", r"(?<!re\.)\bcompile\s*\(", r"__import__", r"importlib", r"\bsubprocess\b", r"\bos\.system\b", r"\bpickle\b",
                 r"(?<!\.)\bopen\s*\(", r"matplotlib", r"\bplotly\b", r"\bsvgwrite\b", r"\blxml\b", r"\bjinja", r"<script", r"<foreignObject", r"xlink:", r"href="]
    for path in SOURCE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), (path.name, pattern)
