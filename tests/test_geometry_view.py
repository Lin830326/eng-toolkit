"""geometry_view checked against hand counts, against geometry_measure and geometry_check_dfm through the gateway, and against an
independent reader of the GLB (tests/view_readers.py: struct and json only).

Numbers derived by hand:

* box [0,100] x [0,50] x [0,20] mm: glTF (x, y, z) = (x, z, -y) / 1000 m, so X 0..0.1, Y 0..0.02, Z -0.05..0: accessor min
  [0, 0, -0.05], max [0.1, 0.02, 0].  6 planar faces, 12 edges.
* blind_part (the box with a diameter-6 blind hole): 6 box faces + the hole's flat bottom = 7 planes, 1 cylinder (one periodic
  face with a seam).
* probe_part (box, through hole, four R2 fillets): 6 planes, 5 cylinders (4 fillets + the bore), 27 edges of which 1 is the bore's
  seam.
* a 1 inch cube written in inches reads as 25.4 mm: accessor max [0.0254, 0.0254, 0].
* tessellation bound of a cylinder face of radius r with chord error d: the inscribed polygon loses a fraction about d / (3 r) of the
  area, so |area_mesh - area| <= area * d / r is a safe bound; a plane is exact up to float32 rounding (relative 1e-6 here).
Engine tests skip without OCP.
"""

import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from view_readers import face_areas_mm2, groups_of_faces, read_view_glb

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry import view as viewer
from eng_toolkit.domains.geometry.tools import TOOL_NAMES, VIEW_DESCRIPTION, view
from eng_toolkit.errors import ToolFailure
from eng_toolkit.gateway.server import gateway

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
NON_FINITE = re.compile(r"\b(?:Infinity|NaN|-Infinity)\b")


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


@pytest.fixture()
def cad(ocp):
    import cad_fixtures

    return cad_fixtures


@pytest.fixture()
def fx(ocp):
    import dfm_fixtures

    return dfm_fixtures


def run(path, **kwargs) -> ToolResult:
    fn = getattr(view, "fn", view)
    result = fn(model_path=str(path), **kwargs)
    return checked(result)


def _reject_constant(token):
    raise AssertionError(f"a non-finite number ({token}) reached the result")


def _finite_numbers(node) -> None:
    if isinstance(node, float):
        assert math.isfinite(node)
    elif isinstance(node, dict):
        for value in node.values():
            _finite_numbers(value)
    elif isinstance(node, list):
        for value in node:
            _finite_numbers(value)


def checked(result: ToolResult) -> ToolResult:
    """No NaN or Infinity as a number anywhere (an echoed input string such as 'NaN mm' is text, not a number)."""
    assert isinstance(result, ToolResult)
    text = result.model_dump_json()
    parsed = json.loads(text, parse_constant=_reject_constant)
    _finite_numbers(parsed)
    if result.ok:
        assert not NON_FINITE.search(text), "a successful result names a non-finite value"
    ToolResult.model_validate(parsed)
    assert result.review_status == "unreviewed"
    if not result.ok:
        param = result.error.details.get("param")
        assert isinstance(param, str) and param.startswith("/"), result.error
        assert result.value is None and result.values == {} and result.warnings == []
    return result


def refused(result: ToolResult, family: str, param: str | None = None) -> ToolResult:
    assert result.ok is False, "expected a refusal"
    assert result.error.family == family, result.error
    if param is not None:
        assert result.error.details["param"] == param, result.error
    return result


def artifact(result: ToolResult, name: str) -> Path:
    (found,) = [a for a in result.artifacts if Path(a.path).name == name]
    assert hashlib.sha256(Path(found.path).read_bytes()).hexdigest() == found.sha256
    return Path(found.path)


async def call(name: str, args: dict) -> ToolResult:
    async with Client(gateway) as client:
        raw = await client.call_tool(name, args)
    data = raw.structured_content if getattr(raw, "structured_content", None) else json.loads(raw.content[0].text)
    return checked(ToolResult.model_validate(data))


def triangle_primitives(parsed):
    return [p for p in parsed["primitives"] if p["mode"] == 4]


# --------------------------------------------------------------------------- pure parts


def test_srgb_to_linear_undoes_the_transfer_function():
    assert viewer.srgb_to_linear("#000000") == [0.0, 0.0, 0.0, 1.0]
    assert viewer.srgb_to_linear("#FFFFFF") == [1.0, 1.0, 1.0, 1.0]
    # 0x80 = 128/255 = 0.50196; ((0.50196 + 0.055) / 1.055) ** 2.4 = 0.21586
    assert viewer.srgb_to_linear("#808080")[0] == pytest.approx(0.215861, abs=1e-6)


def test_highlight_ids_parse_and_refuse_garbage():
    assert viewer.parse_highlight("F7, f3 F3;F1") == [1, 3, 7]
    for text in ("X1", "F0", "F-1", "F1.5", "3", "F", "F1,G2"):
        with pytest.raises(ToolFailure) as caught:
            viewer.parse_highlight(text)
        assert caught.value.family == "input.invalid" and caught.value.details["param"] == "/highlight", text
    with pytest.raises(ToolFailure) as caught:
        viewer.parse_highlight("  ,  ")
    assert caught.value.family == "input.missing"


def test_refusal_pointers():
    assert viewer.pointer({"param": "limits.min_wall"}, "input.invalid") == "/limits/min_wall"
    assert viewer.pointer({"param": "mesh_deflection"}, "input.invalid") == "/mesh_deflection"
    assert viewer.pointer({"param": "/highlight"}, "x") == "/highlight"
    assert viewer.pointer({}, "geometry.time_budget_exceeded") == "/max_seconds"
    assert viewer.pointer({}, "geometry.not_a_solid") == "/model_path"


def test_every_palette_colour_is_distinct_within_its_mode():
    for mode, palette in viewer.PALETTES.items():
        colours = [c for _, c, _ in palette]
        assert len(set(colours)) == len(colours), mode
        assert viewer.EDGE_COLOR not in colours


# --------------------------------------------------------------------------- the GLB structure


def test_the_glb_is_well_formed_and_carries_faces_legend_and_units(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    parsed = read_view_glb(artifact(r, "view.glb"))
    document = parsed["document"]
    assert document["asset"]["version"] == "2.0" and document["scene"] == 0 and document["scenes"] == [{"nodes": [0]}]
    assert len(document["meshes"]) == 1 and len(document["nodes"]) == 1 and document["nodes"][0]["mesh"] == 0
    tri = triangle_primitives(parsed)
    lines = [p for p in parsed["primitives"] if p["mode"] == 1]
    assert [p["extras"]["group"] for p in tri] == ["plane", "cylinder"] and len(lines) == 1
    assert len(document["materials"]) == len(parsed["primitives"])  # one material per primitive
    for p in parsed["primitives"]:
        material = p["material"]
        pbr = material["pbrMetallicRoughness"]
        assert pbr["metallicFactor"] == 0.1 and pbr["roughnessFactor"] == 0.8 and material["doubleSided"] is False
        assert len(pbr["baseColorFactor"]) == 4 and all(0.0 <= c <= 1.0 for c in pbr["baseColorFactor"]) and pbr["baseColorFactor"][3] == 1.0
        assert material["name"] == p["extras"]["group"]
    for p in tri:
        assert len(p["normals"]) == len(p["positions"])
        assert all(abs(math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) - 1.0) < 1e-6 for n in p["normals"])
        ranges = [(f["first_triangle"], f["triangle_count"]) for f in p["extras"]["faces"]]
        assert ranges[0][0] == 0 and all(a[0] + a[1] == b[0] for a, b in zip(ranges, ranges[1:]))
        assert ranges[-1][0] + ranges[-1][1] == len(p["triangles"])  # the face ranges tile the primitive
    extras = parsed["node"]["extras"]
    assert extras["tool"] == "geometry_view" and extras["tool_version"]
    assert extras["units"] == {"model_unit": "millimetre", "unit": "metre", "up_axis": "+Y", "conversion": "model (x, y, z) mm -> glTF (x, z, -y) / 1000 m"}
    assert extras["model_bbox_mm"]["min"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    assert extras["model_bbox_mm"]["max"] == pytest.approx([100.0, 50.0, 20.0], abs=1e-6)
    assert extras["face_ids_valid_for"] == r.provenance.input_files[0].sha256
    legend = {row["group"]: row for row in extras["legend"]}
    assert set(legend) == {"plane", "cylinder", "cone", "sphere", "torus", "bspline", "other", "edges"}
    assert legend["plane"]["faces"] == 6 and legend["cylinder"]["faces"] == 5 and legend["cone"]["faces"] == 0
    assert all(row["meaning"] for row in extras["legend"])
    # values and data agree with the file
    assert r.value.quantity == sum(len(p["triangles"]) for p in tri) == r.values["triangle_count"].quantity
    assert r.values["face_count"].quantity == 11 and r.values["highlighted_face_count"].quantity == 0
    assert {g["group"]: g["triangles"] for g in r.data["groups"]} == {p["extras"]["group"]: len(p["triangles"]) for p in tri}


def test_view_json_maps_every_face_to_its_colour(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="face_kind")
    table = json.loads(artifact(r, "view.json").read_text(encoding="utf-8"))
    parsed = read_view_glb(artifact(r, "view.glb"))
    groups = groups_of_faces(parsed)
    assert [row["id"] for row in table["faces"]] == [f"F{i}" for i in range(1, 12)]
    colour = {row["group"]: row["color"] for row in table["legend"]}
    for row in table["faces"]:
        assert row["group"] == groups[row["id"]] == row["kind"]
        assert row["color"] == colour[row["group"]]
        assert row["finding_ids"] == []
    assert table["glb_sha256"] == hashlib.sha256(artifact(r, "view.glb").read_bytes()).hexdigest()


# --------------------------------------------------------------------------- face ids against geometry_measure


@pytest.mark.asyncio
async def test_face_ids_kinds_and_areas_match_geometry_measure(cad, tmp_path):
    deflection = 0.01
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    measured = await call("geometry_measure", {"model_path": str(source), "inventory_limit": 500})
    shown = await call("geometry_view", {"model_path": str(source), "color_by": "face_kind", "mesh_deflection": f"{deflection} mm"})
    assert measured.ok and shown.ok, (measured.error, shown.error)
    assert measured.data["face_ids_valid_for"] == shown.data["face_ids_valid_for"]
    parsed = read_view_glb(artifact(shown, "view.glb"))
    groups = groups_of_faces(parsed)
    areas = face_areas_mm2(parsed)
    faces = {f"F{f['id']}": f for f in measured.data["faces"]}
    assert set(groups) == set(faces) and len(faces) == 11
    for face_id, face in faces.items():
        assert groups[face_id] == face["kind"], face_id
        exact = face["area_mm2"]
        if face["kind"] == "plane" and abs(face["normal"][2]) < 0.5:
            assert areas[face_id] == pytest.approx(exact, rel=1e-6), face_id  # the four sides are rectangles: exact
        elif face["kind"] == "plane":
            # top and bottom are bounded by the bore and the fillet arcs, whose chords cut off at most d per unit length of
            # the curved boundary: 10 pi (bore) + 4 * pi (four quarter circles of R2) = 14 pi mm
            assert abs(areas[face_id] - exact) <= 14 * math.pi * deflection, (face_id, areas[face_id], exact)
        else:
            assert face["kind"] == "cylinder"
            assert abs(areas[face_id] - exact) <= exact * deflection / face["radius_mm"], (face_id, areas[face_id], exact)
            assert areas[face_id] < exact  # an inscribed polygon has less area than the arc
    # every edge of the inventory is drawn or is the bore's seam
    assert shown.data["edges"]["drawn"] + shown.data["edges"]["seams_not_drawn"] + shown.data["edges"]["degenerate_not_drawn"] == measured.data["edges"] == 27
    assert shown.data["edges"]["seams_not_drawn"] == 1


def test_face_kind_on_a_box_with_a_cylindrical_hole(cad, tmp_path):
    source = cad.write_step(cad.blind_part(), tmp_path / "blind.step")
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    parsed = read_view_glb(artifact(r, "view.glb"))
    counts = {p["extras"]["group"]: len(p["extras"]["faces"]) for p in triangle_primitives(parsed)}
    assert counts == {"plane": 7, "cylinder": 1}


# --------------------------------------------------------------------------- dfm


@pytest.mark.asyncio
@pytest.mark.parametrize("limits", ["", '{"min_wall": "2 mm"}'])
async def test_dfm_colours_exactly_the_faces_geometry_check_dfm_reports(fx, cad, tmp_path, limits):
    source = cad.write_step(fx.wall_block(1.0), tmp_path / "wall.step")
    report = await call("geometry_check_dfm", {"model_path": str(source), "limits": limits, "max_findings": 500})
    shown = await call("geometry_view", {"model_path": str(source), "color_by": "dfm", "limits": limits})
    assert report.ok and shown.ok, (report.error, shown.error)
    findings = report.data["findings"]
    rules = {f["rule"] for f in findings}
    assert any("corner" in rule for rule in rules)
    if limits:
        assert any("wall" in rule for rule in rules)  # the 1 mm wall is thinner than 2 mm
    rank = {"error": 0, "warn": 1, "info": 2}
    worst: dict[str, str] = {}
    for f in findings:
        for face_id in f["face_ids"]:
            key = f"F{face_id}"
            if key not in worst or rank[f["severity"]] < rank[worst[key]]:
                worst[key] = f["severity"]
    parsed = read_view_glb(artifact(shown, "view.glb"))
    groups = groups_of_faces(parsed)
    coloured = {face_id: group for face_id, group in groups.items() if group != "neutral"}
    assert set(coloured) == set(worst) and worst
    assert all(coloured[face_id] == f"dfm_{severity}" for face_id, severity in worst.items())
    assert shown.values["finding_count"].quantity == report.data["summary"]["total"] == len(findings)
    assert shown.values["highlighted_face_count"].quantity == len(worst)
    # the finding ids follow geometry_check_dfm's order, and view.json links faces to them
    table = json.loads(artifact(shown, "view.json").read_text(encoding="utf-8"))
    assert [(row["rule"], row["severity"], row["face_ids"]) for row in table["findings"]] == [(f["rule"], f["severity"], f["face_ids"]) for f in findings]
    for row in table["faces"]:
        expected = [f"D{n}" for n, f in enumerate(findings, start=1) if int(row["id"][1:]) in f["face_ids"]]
        assert row["finding_ids"] == expected
    coloured_rows = shown.data["findings_coloured"] + shown.data["findings_not_coloured"]
    assert len(coloured_rows) == len(findings)
    assert all(row["coloured"] == bool(row["face_ids"]) for row in coloured_rows)


def test_dfm_on_a_clean_part_leaves_every_face_neutral(fx, cad, tmp_path):
    source = cad.write_step(fx.clean_part(), tmp_path / "clean.step")
    r = run(source, color_by="dfm", limits='{"min_access_directions": 1}')
    assert r.ok, r.error
    face_ids_with_findings = {i for f in r.data["findings_coloured"] for i in f["face_ids"]}
    parsed = read_view_glb(artifact(r, "view.glb"))
    coloured = {k for k, g in groups_of_faces(parsed).items() if g != "neutral"}
    assert coloured == {f"F{i}" for i in face_ids_with_findings}


# --------------------------------------------------------------------------- highlight, edges, determinism, units


def test_highlight_colours_only_the_named_face(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="highlight", highlight="F1")
    assert r.ok, r.error
    groups = groups_of_faces(read_view_glb(artifact(r, "view.glb")))
    assert {k for k, g in groups.items() if g == "highlight"} == {"F1"}
    assert all(g == "neutral" for k, g in groups.items() if k != "F1")
    assert r.values["highlighted_face_count"].quantity == 1 and r.data["highlighted_faces"] == ["F1"]


def test_edges_primitive_holds_every_polyline_point(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = run(source, color_by="none")
    parsed = read_view_glb(artifact(r, "view.glb"))
    (lines,) = [p for p in parsed["primitives"] if p["mode"] == 1]
    edges = lines["extras"]["edges"]
    assert len(lines["positions"]) == sum(e["point_count"] for e in edges) == r.data["edges"]["points"]
    assert len(lines["indices"]) == 2 * sum(e["point_count"] - 1 for e in edges)
    assert all(e["point_count"] >= 2 for e in edges) and len(edges) == r.data["edges"]["drawn"] == 26
    assert lines["normals"] is None and lines["material"]["name"] == "edges"
    first = [e["first_point"] for e in edges]
    assert first == sorted(first) and first[0] == 0
    # every polyline point lies on the part's bounding box surface or inside it
    for x, y, z in lines["positions"]:
        assert -1e-6 <= x <= 0.1 + 1e-6 and -1e-6 <= y <= 0.02 + 1e-6 and -0.05 - 1e-6 <= z <= 1e-6
    hidden = run(source, color_by="none", show_edges=False)
    assert all(p["mode"] == 4 for p in read_view_glb(artifact(hidden, "view.glb"))["primitives"])
    assert hidden.data["edges"] is None


def test_the_same_input_gives_the_same_bytes(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    first, second = run(source, color_by="face_kind"), run(source, color_by="face_kind")
    assert artifact(first, "view.glb").read_bytes() == artifact(second, "view.glb").read_bytes()
    assert artifact(first, "view.json").read_bytes() == artifact(second, "view.json").read_bytes()
    # the tessellation does not depend on the colouring (DFM meshes the shape for its own sampling first)
    dfm_run = run(source, color_by="dfm")
    assert dfm_run.value.quantity == first.value.quantity


def test_units_and_axes_of_the_box(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = run(source, color_by="none", name="crate")
    assert r.ok, r.error
    parsed = read_view_glb(artifact(r, "crate.glb"))
    (tri,) = triangle_primitives(parsed)
    accessor = parsed["document"]["accessors"][parsed["document"]["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert accessor["min"] == pytest.approx([0.0, 0.0, -0.05], abs=1e-8)  # float32: 0.1 is stored as 0.100000001490
    assert accessor["max"] == pytest.approx([0.1, 0.02, 0.0], abs=1e-8)
    assert r.data["output"]["glb"]["accessor_min_m"] == accessor["min"] and r.data["output"]["glb"]["accessor_max_m"] == accessor["max"]
    assert len(tri["triangles"]) == 12 and len(tri["extras"]["faces"]) == 6
    assert sum(face_areas_mm2(parsed).values()) == pytest.approx(16000.0, rel=1e-6)
    assert parsed["node"]["name"] == "crate" and r.data["edges"]["drawn"] == 12
    # the +Z face of the model (z = 20) has its normals along glTF +Y
    top = [n for n, p in zip(tri["normals"], tri["positions"]) if abs(p[1] - 0.02) < 1e-9]
    assert top and any(n == pytest.approx((0.0, 1.0, 0.0), abs=1e-6) for n in top)


def test_an_inch_step_is_read_in_millimetres(fx, cad, tmp_path):
    source = fx.write_step_inches(cad.box(length=25.4, width=25.4, height=25.4), tmp_path / "inch.step")
    r = run(source, color_by="face_kind")
    assert r.ok, r.error
    parsed = read_view_glb(artifact(r, "view.glb"))
    accessor = parsed["document"]["accessors"][parsed["document"]["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert accessor["max"] == pytest.approx([0.0254, 0.0254, 0.0], abs=1e-7)
    assert accessor["min"] == pytest.approx([0.0, 0.0, -0.0254], abs=1e-7)
    assert parsed["node"]["extras"]["model_bbox_mm"]["max"] == pytest.approx([25.4, 25.4, 25.4], abs=1e-6)
    assert any(loss["kind"] == "units" and "inch" in loss["detail"] for loss in r.data["losses"])


def test_the_loss_report_names_what_the_view_drops(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    kinds = {loss["kind"] for loss in run(source).data["losses"]}
    assert {"names_colours_structure", "exact_surfaces_to_triangles", "normals", "coordinate_precision", "glb_axes_and_unit", "glb_content", "face_ids", "edges"} <= kinds


# --------------------------------------------------------------------------- refusals


def test_a_shell_and_an_stl_are_refused(cad, tmp_path):
    shell = cad.write_step(cad.planar_shell(), tmp_path / "shell.step")
    refused(run(shell), "geometry.not_a_solid", "/model_path")
    stl = tmp_path / "part.stl"
    stl.write_bytes(b"solid x\nendsolid x\n")
    refused(run(stl), "input.unsupported_format", "/model_path")


def test_unknown_face_ids_are_refused(cad, tmp_path):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = refused(run(source, color_by="highlight", highlight="F2,F7"), "geometry.unknown_entity", "/highlight")
    assert r.error.details["unknown"] == ["F7"] and r.error.details["faces"] == 6
    refused(run(source, color_by="highlight", highlight=""), "input.missing", "/highlight")
    refused(run(source, color_by="face_kind", highlight="F1"), "input.invalid", "/highlight")


def test_the_triangle_cap_is_stated_with_a_hint(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    monkeypatch.setattr(viewer, "MAX_TRIANGLES", 100)
    r = refused(run(source), "input.out_of_range", "/mesh_deflection")  # refused by the estimate, before meshing
    assert "limit is 100" in r.error.message and "coarser" in r.error.message and r.error.details["estimated"] is True
    assert viewer.MAX_TRIANGLES == 100 and viewer.conversion.MAX_TESSELLATION_TRIANGLES == 600_000


def test_the_time_budget_stops_between_steps(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    original = dfm.Deadline

    def expiring(seconds):
        budget = original(seconds)
        expired = original(seconds, started=budget.started - 10_000)

        def check(phase):
            (expired if phase == "mesh" else budget)(phase)

        return check

    monkeypatch.setattr(dfm, "Deadline", expiring)
    r = refused(run(source, max_seconds=5), "geometry.time_budget_exceeded", "/max_seconds")
    assert r.error.details["phase"] == "mesh"


@pytest.mark.parametrize("seconds", [0, 601, -1])
def test_max_seconds_out_of_range(seconds):
    refused(run("unused.step", max_seconds=seconds), "input.out_of_range", "/max_seconds")


def test_a_missing_engine_gives_an_install_hint(cad, tmp_path, monkeypatch):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    monkeypatch.setitem(sys.modules, "OCP", None)
    r = refused(run(source), "engine.unavailable", "/model_path")
    assert "uv sync --extra cad" in r.error.message


# --------------------------------------------------------------------------- through the gateway


@pytest.mark.asyncio
async def test_the_gateway_lists_the_tool_with_compact_descriptions():
    async with Client(gateway) as client:
        tools = await client.list_tools()
    tool = next(t for t in tools if t.name == "geometry_view")
    assert tool.description == VIEW_DESCRIPTION and len(VIEW_DESCRIPTION) < 1000
    schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
    assert schema["required"] == ["model_path"]
    assert set(schema["properties"]) == {
        "model_path", "color_by", "highlight", "process", "tool_axis", "limits", "max_seconds", "mesh_deflection", "name", "show_edges",
        "preview", "inline_image",
    }
    for name, prop in schema["properties"].items():
        assert prop["description"] and len(prop["description"]) < 140, (name, len(prop["description"]))
    assert schema["properties"]["show_edges"]["type"] == "boolean" and schema["properties"]["show_edges"]["default"] is True
    geometry_tools = [t.name for t in tools if t.name.startswith("geometry_")]
    assert len(geometry_tools) == len(TOOL_NAMES) == 5 <= 6


@pytest.mark.asyncio
async def test_a_path_outside_the_allowed_directories_is_refused(cad, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    refused(await call("geometry_view", {"model_path": str(source)}), "input.path_not_allowed", "/model_path")


@pytest.mark.asyncio
async def test_a_shell_through_the_gateway_is_not_a_solid(cad, tmp_path):
    shell = cad.write_step(cad.planar_shell(), tmp_path / "shell.step")
    refused(await call("geometry_view", {"model_path": str(shell)}), "geometry.not_a_solid", "/model_path")


HOSTILE = [
    ({"mesh_deflection": "1e308 mm"}, "input.out_of_range", "/mesh_deflection"),
    ({"mesh_deflection": "1e308 km"}, "input.out_of_range", "/mesh_deflection"),
    ({"mesh_deflection": "nan mm"}, None, "/mesh_deflection"),
    ({"mesh_deflection": "NaN"}, None, "/mesh_deflection"),
    ({"mesh_deflection": "inf mm"}, None, "/mesh_deflection"),
    ({"mesh_deflection": "0.01"}, None, "/mesh_deflection"),
    ({"mesh_deflection": "5 kg"}, None, "/mesh_deflection"),
    ({"mesh_deflection": "0 mm"}, "input.out_of_range", "/mesh_deflection"),
    ({"color_by": "dfm", "limits": '{"min_wall": "1e308 mm"}'}, "input.out_of_range", "/limits/min_wall"),
    ({"color_by": "dfm", "limits": '{"min_wall": "NaN mm"}'}, None, "/limits/min_wall"),
    ({"color_by": "dfm", "limits": '{"min_wall": "2 kg"}'}, None, "/limits/min_wall"),
    ({"color_by": "dfm", "limits": '{"max_hole_depth_ratio": 1e308}'}, "input.out_of_range", "/limits/max_hole_depth_ratio"),
    ({"color_by": "dfm", "limits": '{"bogus": 1}'}, "input.invalid", "/limits/bogus"),  # an unknown key is named by its own pointer
    ({"color_by": "dfm", "limits": "not json"}, "input.invalid", "/limits"),
    ({"color_by": "texture"}, "input.invalid", "/color_by"),
    ({"color_by": "dfm", "process": "turning"}, "geometry.unsupported_process", "/process"),
    ({"color_by": "dfm", "tool_axis": "+w"}, "input.invalid", "/tool_axis"),
    ({"color_by": "highlight", "highlight": "F999"}, "geometry.unknown_entity", "/highlight"),
    ({"color_by": "highlight", "highlight": "F1, Fx"}, "input.invalid", "/highlight"),
    ({"name": "../evil"}, "input.invalid", "/name"),
    ({"max_seconds": 0}, "input.out_of_range", "/max_seconds"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("args", "family", "param"), HOSTILE)
async def test_hostile_values_through_the_gateway(cad, tmp_path, args, family, param):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    r = await call("geometry_view", {"model_path": str(source), **args})
    assert r.ok is False, args
    if family is not None:
        assert r.error.family == family, r.error
    assert r.error.details["param"] == param, r.error


LONG = "x" * 6000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "param"),
    [
        ("model_path", LONG, "/model_path"),
        ("color_by", LONG, "/color_by"),
        ("highlight", "F1," * 2000, "/highlight"),
        ("process", LONG, "/process"),
        ("tool_axis", LONG, "/tool_axis"),
        ("limits", LONG, "/limits"),
        ("mesh_deflection", LONG, "/mesh_deflection"),
        ("name", LONG, "/name"),
    ],
)
async def test_6000_character_strings_are_refused_cleanly(cad, tmp_path, field, value, param):
    source = cad.write_step(cad.box(), tmp_path / "box.step")
    args = {"model_path": str(source), field: value}
    if field == "highlight":
        args["color_by"] = "highlight"
    r = await call("geometry_view", args)
    assert r.ok is False and r.error.details["param"] == param, r.error
    assert len(r.error.message) < 1000


@pytest.mark.asyncio
async def test_a_real_mcp_call_writes_both_artifacts(cad, tmp_path):
    source = cad.write_step(cad.probe_part(), tmp_path / "probe.step")
    r = await call("geometry_view", {"model_path": str(source), "color_by": "highlight", "highlight": "F3, F7"})
    assert r.ok, r.error
    assert r.value.unit == "dimensionless" and r.values["highlighted_face_count"].quantity == 2
    assert {Path(a.path).name for a in r.artifacts} >= {"view.glb", "view.json"}
    groups = groups_of_faces(read_view_glb(artifact(r, "view.glb")))
    assert {k for k, g in groups.items() if g == "highlight"} == {"F3", "F7"}
