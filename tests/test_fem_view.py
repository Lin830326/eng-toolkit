"""fem_view: a fem_static result as a colour-mapped GLB, checked against fem_static's own numbers, against the mesh, and through an
independent reader of the GLB (tests/fem_view_reader.py: struct and json only).

Numbers derived by hand:

* One 10-node tetrahedron with corners (0,0,0), (10,0,0), (0,10,0), (0,0,10) mm (the synthetic job, no program needed): 4 boundary
  facets x 4 sub-triangles = 16 triangles; volume 10^3 / 6 = 166.6667 mm^3, and its facets are flat, so the drawn surface encloses
  exactly that.  Field von Mises = x + y + z MPa on the nodes: 0 at the origin, 10 at the other corners, 5 or 10 at the midside nodes,
  so the auto range is 0 .. 10 MPa.  Displacement u = (1e-3 x, 0, 0) mm: the largest is 0.01 mm (node (10,0,0)); the diagonal of the
  bounding box is sqrt(300) = 17.3205 mm, so 'auto' gives 0.05 * 17.3205 / 0.01 = 86.6025.
* Bar 10 x 10 x 100 mm, 10 kN along x, minimal supports (tests/test_fem_real.py): von Mises 100 MPa everywhere (99.999 as CalculiX
  prints it); the largest displacement 0.047629763 mm at a tip corner; volume 10000 mm^3, flat faces.
* Cantilever 100 x 10 x 10 mm, clamped at x = 0, 1 kN along -z at x = 100: s_xx at mid-span = M c / I = 50000 * 5 / 833.33 = +300
  MPa on the top fibre (z = 10, tension) and -300 MPa on the bottom fibre (z = 0); tolerance 3 % (test_fem_real.py).
* GLB axes: (x, y, z) mm -> (x, z, -y) / 1000 m, so a model displacement uz becomes glTF Y / 1000: the cantilever tip moves to -Y.
"""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastmcp import Client

from eng_toolkit import external as X
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.fem import tools as T
from eng_toolkit.domains.fem import view as viewer
from eng_toolkit.external_catalog import CCX, GMSH
from eng_toolkit.gateway.server import gateway
from tests.fem_view_reader import (
    boundary_facet_count,
    gltf_to_model_mm,
    invert,
    read_fem_glb,
    sequential_table,
    signed_volume,
    srgb_to_linear,
)

NON_FINITE = re.compile(r"\b(?:Infinity|NaN|-Infinity)\b")


def _no_constant(token):
    raise AssertionError(f"{token} reached a result")


def checked(result: ToolResult) -> ToolResult:
    text = result.model_dump_json()
    json.loads(text, parse_constant=_no_constant)
    if result.ok:
        assert not NON_FINITE.search(text)
    assert result.review_status == "unreviewed"
    if not result.ok:
        param = result.error.details.get("param")
        assert isinstance(param, str) and param.startswith("/"), result.error
        assert result.value is None and result.warnings == []
        assert len(result.error.message) < 1200
    return result


async def call(name: str, args: dict) -> ToolResult:
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args, raise_on_error=False)
    for block in raw.content:
        if hasattr(block, "text"):
            json.loads(block.text, parse_constant=_no_constant)
    data = raw.structured_content if raw.structured_content else json.loads(raw.content[0].text)
    return checked(ToolResult.model_validate(data))


def run(**kwargs) -> ToolResult:
    fn = getattr(T.view, "fn", T.view)
    return checked(fn(**kwargs))


def refused(result: ToolResult, family: str | None, param: str) -> ToolResult:
    assert result.ok is False, "expected a refusal"
    if family is not None:
        assert result.error.family == family, result.error
    assert result.error.details["param"] == param, result.error
    return result


def artifact(result: ToolResult, name: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == name]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


def surface(parsed):
    (s,) = [p for p in parsed["primitives"] if p["mode"] == 4]
    return s


def lines_of(parsed):
    return {p["extras"]["kind"]: p for p in parsed["primitives"] if p["mode"] == 1}


# --------------------------------------------------------------------------- a synthetic fem_static job (no program needed)

TET_CORNERS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0)]
GMSH_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3)]  # Gmsh type 11: nodes 5..10 on edges 12, 23, 31, 14, 34, 24


def tet_nodes():
    mids = [tuple((TET_CORNERS[a][k] + TET_CORNERS[b][k]) / 2 for k in range(3)) for a, b in GMSH_EDGES]
    return TET_CORNERS + mids


def write_msh(path: Path, nodes) -> Path:
    rows = ["$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(nodes))]
    rows += [f"{i + 1} {x:.12g} {y:.12g} {z:.12g}" for i, (x, y, z) in enumerate(nodes)]
    rows += ["$EndNodes", "$Elements", "1", "1 11 2 0 1 " + " ".join(str(i) for i in range(1, 11)), "$EndElements"]
    path.write_text("\n".join(rows) + "\n")
    return path


def write_csv(path: Path, nodes, columns=T.CSV_COLUMNS, value=None) -> Path:
    rows = [",".join(columns)]
    for i, (x, y, z) in enumerate(nodes):
        ux = 1e-3 * x
        full = {"node": i + 1, "x_mm": x, "y_mm": y, "z_mm": z, "ux_mm": ux, "uy_mm": 0.0, "uz_mm": 0.0, "u_mag_mm": abs(ux)}
        full.update({c: 0.0 for c in viewer.STRESS_COLUMNS})
        full["sxx_mpa"] = x + y + z
        full["von_mises_mpa"] = x + y + z if value is None else value(i, x, y, z)
        rows.append(",".join(f"{full[c]:.9g}" if c != "node" else str(full[c]) for c in columns))
    path.write_text("\n".join(rows) + "\n")
    return path


def fake_job(root: Path, tool="fem_static") -> Path:
    """A directory laid out as fem_static leaves it, with a manifest of sha256s (a forgery a test can make; see the design note)."""
    root.mkdir(parents=True, exist_ok=True)
    nodes = tet_nodes()
    write_msh(root / "mesh.msh", nodes)
    write_csv(root / "results.csv", nodes)
    for name in ("model.inp", "result.frd", "result.dat"):
        (root / name).write_text("placeholder\n")
    (root / "result.json").write_text(
        json.dumps({"ok": True, "values": {"max_von_mises": {"quantity": 10.0, "unit": "megapascal"}, "max_displacement": {"quantity": 0.01, "unit": "millimeter"}}})
    )
    sign(root, tool)
    return root


def sign(root: Path, tool="fem_static") -> None:
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir()) if p.name != "manifest.json"}
    (root / "manifest.json").write_text(json.dumps({"job_id": root.name, "tool": tool, "inputs_hash": None, "files": files}))


@pytest.fixture
def job(tmp_path):
    return fake_job(tmp_path / "jobs" / "20261023T000000Z-00000000-fem_static")


# --------------------------------------------------------------------------- colour maps (pure)


def luminance(rgb):
    r, g, b = (srgb_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_the_sequential_table_is_cubehelix_with_the_documented_parameters():
    ours = viewer.TABLES["sequential"]
    for row, ref in zip(ours, sequential_table()):
        assert row.tolist() == pytest.approx(ref, abs=1e-6)
    lum = [luminance(row) for row in ours]
    assert all(b > a for a, b in zip(lum, lum[1:])), "intensity rises monotonically"
    assert ours.min() >= 0.0 and ours.max() <= 1.0 and len(ours) == 65


def test_the_diverging_table_has_morelands_endpoints_and_a_neutral_middle():
    table = viewer.TABLES["diverging"]
    assert [round(c * 255) for c in table[0]] == [59, 76, 192]
    assert [round(c * 255) for c in table[-1]] == [180, 4, 38]
    middle = table[32]
    assert max(middle) - min(middle) < 0.01 and 0.8 < middle[0] < 0.9  # Moreland's neutral grey, about (221, 221, 221)
    lum = [luminance(row) for row in table]
    assert all(b > a for a, b in zip(lum[:33], lum[1:33])) and all(b < a for a, b in zip(lum[32:], lum[33:]))
    assert all(row[2] > row[0] for row in table[:32]) and all(row[0] > row[2] for row in table[33:])  # blue below, red above


def test_parsers_refuse_garbage_with_a_pointer():
    field = viewer.FIELDS["von_mises"]
    assert viewer.parse_range("auto", field) is None
    assert viewer.parse_range("0 MPa, 0.25 GPa", field) == (0.0, 250.0)
    assert viewer.parse_range("1 ksi, 2 ksi", field) == pytest.approx((6.894757, 13.789515), rel=1e-6)
    assert viewer.parse_range("-0.1 mm, 0.1 mm", viewer.FIELDS["uz"]) == (-0.1, 0.1)
    assert viewer.parse_deformation("auto") == "auto" and viewer.parse_deformation("1e3") == 1000.0
    assert viewer.parse_output_unit("psi", field)[1] == pytest.approx(145.0377, rel=1e-6)
    for raw, family in (("250 MPa", "input.invalid"), ("10 MPa, 5 MPa", "input.invalid"), ("0 mm, 5 mm", "units.dimension_mismatch"), ("0, 5", "units.bare_number")):
        with pytest.raises(Exception) as caught:
            viewer.parse_range(raw, field)
        assert caught.value.family == family and caught.value.details["param"] == "/range"


def test_the_legend_svg_is_plain_escaped_and_deterministic():
    hostile = '<script>alert("x")</script>&\' ]]>'
    svg = viewer.legend_svg(name=hostile, label="von Mises stress", unit="MPa", table=viewer.TABLES["sequential"], lo=0.0, hi=250.0, notes=["Shape exaggerated: x20 <b>"])
    assert svg == viewer.legend_svg(name=hostile, label="von Mises stress", unit="MPa", table=viewer.TABLES["sequential"], lo=0.0, hi=250.0, notes=["Shape exaggerated: x20 <b>"])
    assert_strict_svg(svg)
    assert "<script" not in svg and "&lt;script&gt;" in svg
    root = ET.fromstring(svg.encode("utf-8"))
    assert hostile in [el.text for el in root.iter() if el.text]  # the text survives the round trip unchanged


ELEMENTS = {"svg", "title", "rect", "text", "defs", "linearGradient", "stop", "line"}
ATTRIBUTES = {
    "svg": {"width", "height", "viewBox"},
    "title": set(),
    "rect": {"x", "y", "width", "height", "fill", "stroke", "stroke-width"},
    "text": {"x", "y", "font-family", "font-size", "font-weight", "fill"},
    "defs": set(),
    "linearGradient": {"id", "x1", "y1", "x2", "y2"},
    "stop": {"offset", "stop-color"},
    "line": {"x1", "y1", "x2", "y2", "stroke", "stroke-width"},
}


def assert_strict_svg(svg: str) -> ET.Element:
    root = ET.fromstring(svg.encode("utf-8"))
    for el in root.iter():
        tag = el.tag.split("}", 1)[1]
        assert el.tag.startswith("{http://www.w3.org/2000/svg}") and tag in ELEMENTS, el.tag
        assert set(el.attrib) <= ATTRIBUTES[tag], (tag, el.attrib)
        for key, value in el.attrib.items():
            if key in ("fill", "stroke", "stop-color"):
                assert re.fullmatch(r"#[0-9A-F]{6}|url\(#ramp\)", value), value
            elif key not in ("font-family", "font-weight", "id", "viewBox"):
                assert re.fullmatch(r"-?[0-9.]+", value), (key, value)
    assert "href" not in svg and "<script" not in svg.lower() and "javascript" not in svg.lower()
    return root


# --------------------------------------------------------------------------- the synthetic job: structure and numbers


def test_one_tetrahedron_gives_sixteen_triangles_and_the_exact_numbers(job):
    r = run(result_dir=str(job))
    assert r.ok, r.error
    assert r.value.quantity == 16 and r.values["triangle_count"].unit == "dimensionless"
    assert r.values["field_max"].quantity == 30.0 / 3 and r.values["field_min"].quantity == 0.0  # x + y + z: 10 at a far corner, 0 at the origin
    assert r.values["field_max"].unit == "megapascal" and r.values["deformation_factor"].quantity == 0.0
    parsed = read_fem_glb(artifact(r, "fem_view.glb"))
    s = surface(parsed)
    assert len(s["triangles"]) == 4 * boundary_facet_count(job / "mesh.msh") == 16
    model = [gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]]
    assert signed_volume(model, s["triangles"]) == pytest.approx(1000.0 / 6.0, rel=1e-6)  # outward and closed
    nodes = tet_nodes()
    for p, node, value in zip(model, s["attributes"]["_NODE"], s["attributes"]["_FIELD"]):
        x, y, z = nodes[int(node[0]) - 1]
        assert p == pytest.approx((x, y, z), abs=1e-5) and value[0] == pytest.approx(x + y + z, abs=1e-6)
    # the colour of every vertex is the sequential map at value / 10
    for rgb, value in zip(s["attributes"]["COLOR_0"], s["attributes"]["_FIELD"]):
        t, gap = invert(rgb, sequential_table())
        assert gap < 1e-5 and t == pytest.approx(value[0] / 10.0, abs=1e-5)
    # normals point out of the tetrahedron
    centre = (2.5, 2.5, 2.5)
    for p, n in zip(model, s["attributes"]["NORMAL"]):
        nm = gltf_to_model_mm(n)
        assert sum((p[k] - centre[k]) * nm[k] for k in range(3)) > 0.0
    extras = parsed["node"]["extras"]
    assert extras["field"] == "von_mises" and extras["unit"] == "megapascal" and extras["unit_symbol"] == "MPa"
    assert extras["range"]["low"] == 0.0 and extras["range"]["high"] == 10.0 and extras["colormap"] == "sequential"
    assert len(extras["legend"]) == 11 and extras["legend"][5]["value"] == 5.0
    assert extras["units"]["conversion"] == "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"
    assert extras["deformation"]["factor"] == 0.0 and not extras["deformation"]["exaggerated"]
    assert s["material"]["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
    view_doc = json.loads(artifact(r, "view.json").read_text())
    assert view_doc["glb_sha256"] == hashlib.sha256(artifact(r, "fem_view.glb").read_bytes()).hexdigest()
    assert view_doc["range"] == extras["range"] and view_doc["legend"] == extras["legend"]
    assert {loss["kind"] for loss in r.data["losses"]} >= {"nodal_values", "surface_only", "singular_points", "interpolation"}
    assert r.data["fem_static_comparison"]["max_von_mises"]["agrees_with_all_nodes_max"] is True
    assert_strict_svg(artifact(r, "legend.svg").read_text(encoding="utf-8"))


def test_deformation_explicit_and_auto(job):
    base = surface(read_fem_glb(artifact(run(result_dir=str(job)), "fem_view.glb")))
    r = run(result_dir=str(job), deformation_scale="200", field="displacement")
    assert r.ok and r.values["deformation_factor"].quantity == 200.0
    s = surface(read_fem_glb(artifact(r, "fem_view.glb")))
    nodes = tet_nodes()
    for p0, p1, node in zip(base["attributes"]["POSITION"], s["attributes"]["POSITION"], s["attributes"]["_NODE"]):
        x = nodes[int(node[0]) - 1][0]
        moved = [(b - a) * 1000.0 for a, b in zip(p0, p1)]  # metres -> mm, glTF axes
        assert moved == pytest.approx([200.0 * 1e-3 * x, 0.0, 0.0], abs=1e-4)  # model x stays glTF x
    parsed = read_fem_glb(artifact(r, "fem_view.glb"))
    assert "undeformed_outline" in lines_of(parsed)  # a tetrahedron's six edges are all feature edges
    assert len(lines_of(parsed)["undeformed_outline"]["indices"]) == 6 * 2 * 2
    assert parsed["node"]["extras"]["deformation"]["exaggerated"] is True
    assert any("exaggerated" in n for n in parsed["node"]["extras"]["notes"])
    assert "exaggerated" in artifact(r, "legend.svg").read_text(encoding="utf-8")
    auto = run(result_dir=str(job), deformation_scale="auto")
    assert auto.values["deformation_factor"].quantity == pytest.approx(0.05 * math.sqrt(300.0) / 0.01, rel=1e-5)  # 86.6025


def test_range_clamping_is_counted_and_drawn_in_the_end_colours(job):
    r = run(result_dir=str(job), range="2 MPa, 8 MPa")
    assert r.ok
    values = [v for v in (sum(p) for p in tet_nodes())]  # x + y + z of the 10 nodes, all on the surface
    assert r.data["range"]["clamped_below"] == sum(v < 2 for v in values) == 1  # the origin
    assert r.data["range"]["clamped_above"] == sum(v > 8 for v in values) == 6  # three corners (10) and three midside nodes (10)
    s = surface(read_fem_glb(artifact(r, "fem_view.glb")))
    table = viewer.TABLES["sequential"]
    for rgb, value in zip(s["attributes"]["COLOR_0"], s["attributes"]["_FIELD"]):
        if value[0] < 2 or value[0] > 8:
            end = table[0] if value[0] < 2 else table[-1]
            assert list(rgb) == pytest.approx([srgb_to_linear(c) for c in end], abs=1e-6)
    assert any("outside the range" in w for w in r.warnings)
    assert "below the range" in artifact(r, "legend.svg").read_text(encoding="utf-8")


def test_mesh_edges_and_files_mode(job):
    r = run(mesh_file=str(job / "mesh.msh"), results_file=str(job / "results.csv"), mesh_edges=True, field="s_xx")
    assert r.ok, r.error
    parsed = read_fem_glb(artifact(r, "fem_view.glb"))
    assert len(lines_of(parsed)["mesh_edges"]["indices"]) == 6 * 2 * 2  # six curved edges, two segments each
    assert parsed["node"]["extras"]["colormap"] == "diverging" and r.data["range"]["low"] == -10.0  # signed: symmetric about 0
    assert r.data["source"]["mode"] == "files" and r.data["source"]["fem_static_job_id"] is None


def test_principal_stresses_are_the_eigenvalues(job):
    r = run(result_dir=str(job), field="principal_min")
    assert r.ok and r.values["field_min"].quantity == 0.0  # uniaxial s_xx >= 0: the smallest principal stress is 0
    r = run(result_dir=str(job), field="principal_max")
    assert r.values["field_max"].quantity == 10.0


def test_identical_inputs_give_identical_bytes(job):
    a, b = run(result_dir=str(job), deformation_scale="50", mesh_edges=True), run(result_dir=str(job), deformation_scale="50", mesh_edges=True)
    for name in ("fem_view.glb", "legend.svg", "view.json"):
        assert artifact(a, name).read_bytes() == artifact(b, name).read_bytes(), name


def test_a_hostile_name_is_escaped_in_the_svg_and_kept_as_data_in_the_glb(job):
    name = '<svg onload="x">&amp;\'"'
    r = run(result_dir=str(job), name=name)
    assert r.ok, r.error
    svg = artifact(r, "legend.svg").read_text(encoding="utf-8")
    root = assert_strict_svg(svg)
    assert "onload" not in [a for el in root.iter() for a in el.attrib]
    assert read_fem_glb(artifact(r, "fem_view.glb"))["node"]["name"] == name


def test_the_triangle_cap_is_stated_with_the_estimate(job, monkeypatch):
    monkeypatch.setattr(viewer, "MAX_TRIANGLES", 10)
    r = refused(run(result_dir=str(job)), "input.too_large", "/result_dir")
    assert "16 triangles" in r.error.message and "larger element_size" in r.error.message


def test_the_time_budget_stops_between_steps(job, monkeypatch):
    class Clock:
        now = 0.0

        @classmethod
        def monotonic(cls):
            cls.now += 0.4
            return cls.now

    monkeypatch.setattr(viewer, "time", Clock)
    refused(run(result_dir=str(job), max_seconds=1), "fem.time_budget_exceeded", "/max_seconds")


# --------------------------------------------------------------------------- refusals through the gateway client


@pytest.mark.asyncio
async def test_the_gateway_lists_the_tool_with_compact_descriptions():
    async with Client(gateway) as client:
        tools = {t.name: t for t in await client.list_tools()}
    tool = tools["fem_view"]
    assert tool.description == T.VIEW_DESCRIPTION and len(T.VIEW_DESCRIPTION) < 1000
    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
    assert set(schema["properties"]) == {
        "result_dir", "mesh_file", "results_file", "field", "colormap", "range", "deformation_scale",
        "show_undeformed_edges", "mesh_edges", "name", "output_unit", "max_seconds",
    }
    descriptions = []
    for pname, prop in schema["properties"].items():
        assert prop["type"] in ("string", "integer", "boolean") and prop["description"] and len(prop["description"]) < 140, pname
        descriptions.append(prop["description"])
    assert len(set(descriptions)) == len(descriptions)
    assert len([n for n in tools if n.startswith("fem_")]) == 3


@pytest.mark.asyncio
async def test_missing_foreign_and_tampered_job_directories_are_refused(job, tmp_path):
    refused(await call("fem_view", {"result_dir": str(tmp_path / "nowhere")}), "input.file_not_found", "/result_dir")
    foreign = fake_job(tmp_path / "foreign", tool="geometry_view")
    r = refused(await call("fem_view", {"result_dir": str(foreign)}), "fem.not_a_static_result", "/result_dir")
    assert "fem_static" in r.error.message
    broken = fake_job(tmp_path / "broken")
    (broken / "manifest.json").write_text("{not json")
    refused(await call("fem_view", {"result_dir": str(broken)}), "fem.not_a_static_result", "/result_dir")
    unlisted = fake_job(tmp_path / "unlisted")
    manifest = json.loads((unlisted / "manifest.json").read_text())
    del manifest["files"]["result.frd"]
    (unlisted / "manifest.json").write_text(json.dumps(manifest))
    refused(await call("fem_view", {"result_dir": str(unlisted)}), "fem.not_a_static_result", "/result_dir")
    # one byte of results.csv changed after the manifest was written
    data = bytearray((job / "results.csv").read_bytes())
    data[-3] = ord("7") if data[-3] != ord("7") else ord("8")
    (job / "results.csv").write_bytes(bytes(data))
    r = refused(await call("fem_view", {"result_dir": str(job)}), "fem.integrity_mismatch", "/result_dir")
    assert "results.csv" in r.error.message and "sha256" in r.error.message


@pytest.mark.asyncio
async def test_a_path_outside_the_allowed_directories_is_refused(job, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    refused(await call("fem_view", {"result_dir": str(job)}), "input.path_not_allowed", "/result_dir")
    refused(await call("fem_view", {"mesh_file": str(job / "mesh.msh"), "results_file": str(job / "results.csv")}), "input.path_not_allowed", "/mesh_file")
    inside = allowed / job.name
    shutil.copytree(job, inside)
    assert (await call("fem_view", {"result_dir": str(inside)})).ok


@pytest.mark.asyncio
async def test_results_that_do_not_belong_to_the_mesh_or_hold_bad_numbers_are_refused(job, tmp_path):
    nodes = tet_nodes()
    msh = str(job / "mesh.msh")
    shifted = write_csv(tmp_path / "shifted.csv", [(x + 1.0, y, z) for x, y, z in nodes])
    refused(await call("fem_view", {"mesh_file": msh, "results_file": str(shifted)}), "fem.integrity_mismatch", "/results_file")
    short = write_csv(tmp_path / "short.csv", nodes[:9])
    refused(await call("fem_view", {"mesh_file": msh, "results_file": str(short)}), "fem.integrity_mismatch", "/results_file")
    nan = write_csv(tmp_path / "nan.csv", nodes, value=lambda i, *_: float("nan") if i == 3 else 1.0)
    refused(await call("fem_view", {"mesh_file": msh, "results_file": str(nan)}), "input.invalid", "/results_file")
    huge = write_csv(tmp_path / "huge.csv", nodes, value=lambda i, *_: 1e308 if i == 3 else 1.0)
    refused(await call("fem_view", {"mesh_file": msh, "results_file": str(huge)}), "input.out_of_range", "/results_file")
    partial = write_csv(tmp_path / "partial.csv", nodes, columns=("node", "x_mm", "y_mm", "z_mm", "ux_mm", "uy_mm", "uz_mm", "u_mag_mm"))
    r = refused(await call("fem_view", {"mesh_file": msh, "results_file": str(partial)}), "fem.field_not_available", "/field")
    assert "displacement" in r.error.message
    assert (await call("fem_view", {"mesh_file": msh, "results_file": str(partial), "field": "displacement"})).ok
    garbage = tmp_path / "garbage.msh"
    garbage.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\nlots\n")
    refused(await call("fem_view", {"mesh_file": str(garbage), "results_file": str(job / "results.csv")}), "input.invalid", "/mesh_file")
    refused(await call("fem_view", {"mesh_file": msh, "results_file": str(job / "mesh.msh")}), "input.unsupported_format", "/results_file")


HOSTILE = [
    ({"field": "temperature"}, "input.invalid", "/field"),
    ({"colormap": "jet"}, "input.invalid", "/colormap"),
    ({"range": "0 mm, 5 mm"}, "units.dimension_mismatch", "/range"),
    ({"field": "uz", "range": "0 MPa, 5 MPa"}, "units.dimension_mismatch", "/range"),
    ({"range": "0 MPa, 1e308 MPa"}, "input.out_of_range", "/range"),
    ({"range": "0 MPa, 1e308 GPa"}, "input.out_of_range", "/range"),
    ({"range": "nan MPa, 1 MPa"}, None, "/range"),
    ({"range": "NaN"}, None, "/range"),
    ({"range": "5 MPa"}, "input.invalid", "/range"),
    ({"range": "0, 5"}, "units.bare_number", "/range"),
    ({"range": "9 MPa, 1 MPa"}, "input.invalid", "/range"),
    ({"deformation_scale": "1e9"}, "input.out_of_range", "/deformation_scale"),
    ({"deformation_scale": "1e308"}, "input.out_of_range", "/deformation_scale"),
    ({"deformation_scale": "-5"}, "input.out_of_range", "/deformation_scale"),
    ({"deformation_scale": "nan"}, "input.invalid", "/deformation_scale"),
    ({"deformation_scale": "Infinity"}, "input.invalid", "/deformation_scale"),
    ({"deformation_scale": "5 mm"}, "input.invalid", "/deformation_scale"),
    ({"output_unit": "mm"}, "units.dimension_mismatch", "/output_unit"),
    ({"output_unit": "1e300 MPa"}, None, "/output_unit"),
    ({"output_unit": "MPa; rm"}, "input.invalid", "/output_unit"),
    ({"name": "a\x00b"}, "input.invalid", "/name"),
    ({"name": "evil‮txt"}, "input.invalid", "/name"),
    ({"name": ""}, "input.invalid", "/name"),
    ({"max_seconds": 0}, "input.out_of_range", "/max_seconds"),
    ({"max_seconds": 601}, "input.out_of_range", "/max_seconds"),
    ({"mesh_file": "x.msh"}, "input.invalid", "/result_dir"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("args", "family", "param"), HOSTILE, ids=[str(i) for i in range(len(HOSTILE))])
async def test_hostile_values_through_the_gateway(job, args, family, param):
    r = refused(await call("fem_view", {"result_dir": str(job), **args}), family, param)
    assert r.provenance.input_files == [], "refused before any file was opened"


@pytest.mark.asyncio
async def test_neither_source_is_missing_and_half_a_pair_is_refused(job):
    refused(await call("fem_view", {}), "input.missing", "/result_dir")
    refused(await call("fem_view", {"mesh_file": str(job / "mesh.msh")}), "input.missing", "/results_file")


LONG = "x" * 6000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "param", ["result_dir", "mesh_file", "results_file", "field", "colormap", "range", "deformation_scale", "name", "output_unit"]
)
async def test_6000_character_strings_are_refused_quickly_and_not_echoed(job, param):
    args = {"result_dir": str(job), param: LONG} if param not in ("result_dir", "mesh_file", "results_file") else {param: LONG}
    r = refused(await call("fem_view", args), "input.invalid", f"/{param}")
    assert "x" * 100 not in r.error.message and "x" * 100 not in json.dumps(r.error.details)


# --------------------------------------------------------------------------- the real programs: fem_static, then fem_view

HAVE_PROGRAMS = X.locate(GMSH)[0] is not None and X.locate(CCX)[0] is not None
try:
    import OCP  # noqa: F401

    HAVE_OCP = True
except ImportError:
    HAVE_OCP = False
real = pytest.mark.skipif(not (HAVE_PROGRAMS and HAVE_OCP), reason="needs OCP (uv sync --extra cad), Gmsh and CalculiX installed")

BAR_SUPPORTS = "x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 5 mm, 10 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz"


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    """fem_static through the gateway on the bar, the clamped cantilever and a cylinder, once for the module."""
    if not (HAVE_PROGRAMS and HAVE_OCP):
        pytest.skip("needs OCP, Gmsh and CalculiX")
    from tests import cad_fixtures as cad

    root = tmp_path_factory.mktemp("fem_view_real")
    cases = {
        "bar": (cad.box(0, 0, 0, 100, 10, 10), dict(element_size="5 mm", supports=BAR_SUPPORTS, loads="force (10 kN, 0 N, 0 N) on x = 100 mm")),
        "cantilever": (cad.box(0, 0, 0, 100, 10, 10), dict(element_size="2.5 mm", supports="x = 0 mm", loads="force (0 N, 0 N, -1 kN) on x = 100 mm")),
        "cylinder": (cad.cylinder(0, 0, 0, 10.0, 30.0), dict(element_size="3 mm", supports="z = 0 mm", loads="force (1 kN, 0 N, 0 N) on z = 30 mm")),
    }
    out = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ENG_TOOLKIT_WS", str(root / "ws"))
        for name, (shape, args) in cases.items():
            model = str(cad.write_step(shape, root / f"{name}.step"))
            r = asyncio.run(call("fem_static", {"model_path": model, "youngs_modulus": "210000 MPa", "poisson_ratio": 0.3, **args}))
            assert r.ok, r.error
            out[name] = r
    return out


def csv_table(job_dir) -> list[dict]:
    lines = Path(job_dir, "results.csv").read_text().splitlines()
    head = lines[0].split(",")
    return [dict(zip(head, map(float, row.split(",")))) for row in lines[1:]]


def view_of(result, **args) -> tuple[ToolResult, dict]:
    r = asyncio.run(call("fem_view", {"result_dir": result.data["job_dir"], **args}))
    assert r.ok, r.error
    return r, read_fem_glb(artifact(r, "fem_view.glb"))


@real
def test_bar_von_mises_on_the_glb_is_fem_statics_maximum_and_uniform(solved):
    static = solved["bar"]
    r, parsed = view_of(static)
    s = surface(parsed)
    expected = static.values["max_von_mises"].quantity
    assert expected == pytest.approx(100.0, rel=1e-4)
    field = [v[0] for v in s["attributes"]["_FIELD"]]
    assert max(field) == pytest.approx(expected, rel=1e-6)  # float32: seven digits
    assert r.values["field_max"].quantity == pytest.approx(expected, rel=1e-6)
    assert parsed["node"]["extras"]["range"]["widened"] is True  # 99.999 everywhere: the auto range is widened to 0.1 %
    low, high = parsed["node"]["extras"]["range"]["low"], parsed["node"]["extras"]["range"]["high"]
    ts = []
    for rgb in s["attributes"]["COLOR_0"]:
        t, gap = invert(rgb, sequential_table())
        assert gap < 1e-4
        ts.append(t)
    assert max(ts) - min(ts) < 0.02  # one colour: the uniform field
    inverted = [low + t * (high - low) for t in ts]
    assert max(inverted) == pytest.approx(expected, rel=1e-5)
    # the same field in a fixed range: every vertex is at the middle of 0 .. 200 MPa
    fixed, parsed = view_of(static, range="0 MPa, 200 MPa")
    for rgb in surface(parsed)["attributes"]["COLOR_0"]:
        assert invert(rgb, sequential_table())[0] == pytest.approx(0.5, abs=1e-4)
    assert fixed.data["range"]["clamped_below"] == fixed.data["range"]["clamped_above"] == 0


@real
def test_bar_displacement_maximum_and_structure(solved):
    static = solved["bar"]
    r, parsed = view_of(static, field="displacement")
    assert r.values["field_max"].quantity == pytest.approx(static.values["max_displacement"].quantity, rel=1e-6)  # 0.047629763 mm
    assert r.values["field_max"].unit == "millimeter"
    s = surface(parsed)
    msh = Path(static.data["job_dir"], "mesh.msh")
    assert len(s["triangles"]) == 4 * boundary_facet_count(msh) == r.value.quantity
    model = [gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]]
    assert signed_volume(model, s["triangles"]) == pytest.approx(10000.0, rel=1e-5)  # closed, outward, flat faces: exact
    table = csv_table(static.data["job_dir"])
    for p, node in zip(model, s["attributes"]["_NODE"]):  # undeformed: the vertices are the mesh's surface nodes
        row = table[int(node[0]) - 1]
        assert p == pytest.approx((row["x_mm"], row["y_mm"], row["z_mm"]), abs=2e-5)
    accessor = parsed["document"]["accessors"][parsed["document"]["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    # float32 of metres: 0.1 is stored as 0.10000000149
    assert accessor["min"] == pytest.approx([0.0, 0.0, -0.01], abs=1e-8) and accessor["max"] == pytest.approx([0.1, 0.01, 0.0], abs=1e-8)
    assert r.data["fem_static_comparison"]["max_displacement"]["agrees_with_all_nodes_max"]


@real
def test_cantilever_s_xx_is_tension_on_top_and_compression_below_in_the_diverging_map(solved):
    static = solved["cantilever"]
    r, parsed = view_of(static, field="s_xx")
    s = surface(parsed)
    extras = parsed["node"]["extras"]
    assert extras["colormap"] == "diverging" and extras["range"]["low"] == -extras["range"]["high"]
    table = [tuple(row) for row in extras["colormap_table_srgb"]]
    assert table == [tuple(row) for row in viewer.TABLES["diverging"].tolist()]
    model = [gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]]
    top, bottom = [], []
    for p, rgb, value in zip(model, s["attributes"]["COLOR_0"], s["attributes"]["_FIELD"]):
        if abs(p[0] - 50.0) < 1e-3 and abs(p[2] - 10.0) < 1e-3:
            top.append((rgb, value[0]))
        elif abs(p[0] - 50.0) < 1e-3 and abs(p[2]) < 1e-3:
            bottom.append((rgb, value[0]))
    assert top and bottom
    assert sum(v for _, v in top) / len(top) == pytest.approx(300.0, rel=0.03)  # M c / I, tension
    assert sum(v for _, v in bottom) / len(bottom) == pytest.approx(-300.0, rel=0.03)  # compression
    low, high = extras["range"]["low"], extras["range"]["high"]
    for rgb, value in top + bottom:
        t, gap = invert(rgb, table)
        assert gap < 1e-4 and low + t * (high - low) == pytest.approx(value, abs=1e-3 * (high - low))
        assert (rgb[0] > rgb[2]) == (value > 0)  # red side for tension, blue side for compression


@real
def test_cantilever_deformation_moves_the_tip_by_factor_times_uz_downward_in_gltf_y(solved):
    static = solved["cantilever"]
    _, flat = view_of(static)
    r, bent = view_of(static, deformation_scale="50")
    assert r.values["deformation_factor"].quantity == 50.0
    table = csv_table(static.data["job_dir"])
    p0, p1 = surface(flat)["attributes"]["POSITION"], surface(bent)["attributes"]["POSITION"]
    for a, b, node in zip(p0, p1, surface(bent)["attributes"]["_NODE"]):
        row = table[int(node[0]) - 1]
        moved = [(y - x) * 1000.0 for x, y in zip(a, b)]
        assert moved == pytest.approx([50 * row["ux_mm"], 50 * row["uz_mm"], -50 * row["uy_mm"]], abs=2e-4)  # (x, z, -y)
    tip = max(table, key=lambda row: math.dist((0, 0, 0), (row["ux_mm"], row["uy_mm"], row["uz_mm"])))
    assert tip["x_mm"] == pytest.approx(100.0) and tip["uz_mm"] < -1.8  # the tip goes down about 1.9 mm
    outline = lines_of(bent)["undeformed_outline"]
    assert outline["extras"]["segments"] > 0
    for p in outline["attributes"]["POSITION"]:  # undeformed, and on the 12 edges of the box: two coordinates at a face of the box
        x, y, z = gltf_to_model_mm(p)
        on = [min(abs(x), abs(x - 100)) < 1e-4, min(abs(y), abs(y - 10)) < 1e-4, min(abs(z), abs(z - 10)) < 1e-4]
        assert sum(on) >= 2, (x, y, z)
    assert bent["node"]["extras"]["deformation"]["exaggerated"] is True


@real
def test_auto_deformation_is_five_percent_of_the_diagonal(solved):
    static = solved["cantilever"]
    r, parsed = view_of(static, deformation_scale="auto")
    table = csv_table(static.data["job_dir"])
    largest = max(math.sqrt(row["ux_mm"] ** 2 + row["uy_mm"] ** 2 + row["uz_mm"] ** 2) for row in table)
    xs, ys, zs = ([row[k] for row in table] for k in ("x_mm", "y_mm", "z_mm"))
    diagonal = math.dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))  # sqrt(100^2 + 10^2 + 10^2) = 100.995 mm
    assert r.values["deformation_factor"].quantity == pytest.approx(0.05 * diagonal / largest, rel=1e-5)
    assert largest == pytest.approx(static.values["max_displacement"].quantity, rel=1e-6)


@real
def test_cantilever_range_clamping_counts_the_surface_nodes_outside(solved):
    static = solved["cantilever"]
    r, parsed = view_of(static, field="s_xx", range="-100 MPa, 100 MPa")
    s = surface(parsed)
    by_node = {int(n[0]): v[0] for n, v in zip(s["attributes"]["_NODE"], s["attributes"]["_FIELD"])}
    assert r.data["range"]["clamped_below"] == sum(v < -100 for v in by_node.values()) > 0
    assert r.data["range"]["clamped_above"] == sum(v > 100 for v in by_node.values()) > 0
    legend = artifact(r, "legend.svg").read_text(encoding="utf-8")
    assert f"{r.data['range']['clamped_above']} node(s) above the range" in legend
    assert_strict_svg(legend)


@real
def test_a_curved_surface_is_closed_within_the_sub_triangulation_error(solved):
    static = solved["cylinder"]
    r, parsed = view_of(static, mesh_edges=True)
    s = surface(parsed)
    model = [gltf_to_model_mm(p) for p in s["attributes"]["POSITION"]]
    volume = signed_volume(model, s["triangles"])
    mesh_volume = r.data["closed_surface"]["mesh_volume_mm3"]
    assert mesh_volume == pytest.approx(math.pi * 100 * 30, rel=1e-4)
    # each flat sub-triangle cuts a chord of length l of the radius-10 cylinder: sagitta l^2 / (8 r); the volume lost is at most
    # the curved area times the largest sagitta
    longest = max(math.dist(model[t[i]], model[t[(i + 1) % 3]]) for t in s["triangles"] for i in range(3))
    bound = 2 * math.pi * 10 * 30 * longest**2 / (8 * 10)
    assert 0 < mesh_volume - volume < bound, (mesh_volume, volume, bound)
    assert len(s["triangles"]) == 4 * boundary_facet_count(Path(static.data["job_dir"], "mesh.msh"))
    for p, n in zip(model, s["attributes"]["NORMAL"]):  # outward normals on the curved side
        if 1e-3 < p[2] < 30 - 1e-3:
            nm = gltf_to_model_mm(n)
            assert (p[0] * nm[0] + p[1] * nm[1]) > 0.0
    assert r.values["field_max"].quantity == pytest.approx(static.values["max_von_mises"].quantity, rel=1e-6) or any("inside the part" in w for w in r.warnings)


@real
def test_real_determinism_files_mode_and_a_tampered_copy(solved, tmp_path):
    static = solved["cantilever"]
    a, _ = view_of(static, field="von_mises", deformation_scale="auto", mesh_edges=True)
    b, _ = view_of(static, field="von_mises", deformation_scale="auto", mesh_edges=True)
    for name in ("fem_view.glb", "legend.svg", "view.json"):
        assert artifact(a, name).read_bytes() == artifact(b, name).read_bytes()
    job_dir = Path(static.data["job_dir"])
    files = asyncio.run(call("fem_view", {"mesh_file": str(job_dir / "mesh.msh"), "results_file": str(job_dir / "results.csv")}))
    assert files.ok and files.value.quantity == a.value.quantity
    assert files.values["field_max"].quantity == pytest.approx(static.values["max_von_mises"].quantity, rel=1e-6)
    copy = tmp_path / job_dir.name
    shutil.copytree(job_dir, copy)
    data = bytearray((copy / "mesh.msh").read_bytes())
    data[200] = ord(" ") if data[200] != ord(" ") else ord("\t")
    (copy / "mesh.msh").write_bytes(bytes(data))
    r = asyncio.run(call("fem_view", {"result_dir": str(copy)}))
    refused(r, "fem.integrity_mismatch", "/result_dir")


@real
def test_example_paths_are_written(solved):
    """Not a check: leaves two example GLBs and a legend where FEM_VIEW_EXAMPLES points (used for the report)."""
    target = os.environ.get("FEM_VIEW_EXAMPLES")
    if not target:
        pytest.skip("set FEM_VIEW_EXAMPLES to keep example files")
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    for tag, static, args in (
        ("cantilever-sxx-deformed", solved["cantilever"], dict(field="s_xx", deformation_scale="auto", name="cantilever s_xx")),
        ("bar-displacement", solved["bar"], dict(field="displacement", mesh_edges=True, name="bar |u|")),
    ):
        r, _ = view_of(static, **args)
        shutil.copy(artifact(r, "fem_view.glb"), out / f"{tag}.glb")
        shutil.copy(artifact(r, "legend.svg"), out / f"{tag}.svg")
