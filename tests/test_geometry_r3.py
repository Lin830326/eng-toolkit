"""Round-3 fixes of the geometry tools (user-acceptance judge items C4-1, C1-9/C1-10 and E1-2).

* C4-1: a sheet-metal bend drawn through 3-point arcs whose via points are written to 3 or 2 decimals has inner and outer
  cylinders 3e-3 mm (3 decimals) or 4e-2 mm (2 decimals) off one axis, beyond the 1e-3 x thickness concentric tolerance.
  ``geometry_check_dfm`` now pairs them with a relaxed axis tolerance of 5 x 0.01 mm, says so in a ``sheet.bend_tolerance``
  warning, and a cylinder that still pairs with nothing is a ``sheet.bend_unrecognised`` warning that the summary names
  ("4 bend-like faces not evaluated"), so "errors 0, warnings 0" can no longer hide a skipped bend.
* C1-10: ``tool_axis`` left empty for cnc_milling is +z with a warning and ``data.tool_axis_defaulted``.
* C1-9: ``geometry_view`` ``focus`` adds a close-up panel (same colours, scale bar in mm, face ids), draws a ring at the
  point of every DFM error and warning finding, and reports which share of a coloured face a finding concerns.

Expected numbers are construction dimensions: the bend radii 2 and 3 mm in 3 mm sheet, the wall block's faces (x = 0 is
40 x 20 mm, the pocket wall x = 1 is 20 x 15 mm), the heatsink's three holes at x = -12, 0, 12.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from png_reader import read_png

from eng_toolkit import brep
from eng_toolkit import preview as P
from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.geometry import dfm
from eng_toolkit.domains.geometry import view as viewer
from eng_toolkit.domains.geometry.tools import check_dfm, make_part, view

NEED_OCP = "OCP is not installed; run: uv sync --extra cad"
TOL = 1e-6
SHOP = json.dumps({"min_wall": "1 mm", "tool_radius": "1.5 mm"})
RING = viewer.MARKER_RGB

# The spec geometry_make_part received in scenario C4 (round 2, R3 / R6 bend, via points of 3 decimals), unchanged.
C4_SPEC = json.loads(
    '{"features":[{"distance":"40.0 mm","mode":"add","name":"l_body","offset":"0.0 mm","op":"extrude","plane":"xz","profile":'
    '{"path":{"segments":[{"to":["6.0 mm","0.0 mm"],"via":["1.757 mm","1.757 mm"]},{"to":["80.0 mm","0.0 mm"]},'
    '{"to":["80.0 mm","3.0 mm"]},{"to":["6.0 mm","3.0 mm"]},{"to":["3.0 mm","6.0 mm"],"via":["3.879 mm","3.879 mm"]},'
    '{"to":["3.0 mm","60.0 mm"]},{"to":["0.0 mm","60.0 mm"]},{"to":["0.0 mm","6.0 mm"]}],"start":["0.0 mm","6.0 mm"]}}},'
    '{"distance":"3.0 mm","mode":"add","name":"gusset_a","offset":"12.0 mm","op":"extrude","plane":"xz","profile":'
    '{"polygon":{"points":[["3.0 mm","3.0 mm"],["28.0 mm","3.0 mm"],["3.0 mm","28.0 mm"]]}}},'
    '{"distance":"3.0 mm","mode":"add","name":"gusset_b","offset":"25.0 mm","op":"extrude","plane":"xz","profile":'
    '{"polygon":{"points":[["3.0 mm","3.0 mm"],["28.0 mm","3.0 mm"],["3.0 mm","28.0 mm"]]}}},'
    '{"at":[["60.0 mm","10.0 mm","3.0 mm"],["60.0 mm","30.0 mm","3.0 mm"]],"axis":"-z","diameter":"5.5 mm","name":"base_holes","op":"hole"},'
    '{"at":[["0.0 mm","10.0 mm","45.0 mm"],["0.0 mm","30.0 mm","45.0 mm"]],"axis":"+x","diameter":"5.5 mm","name":"flange_holes","op":"hole"}],'
    '"params":{"r_in":"3.0 mm","r_out":"6.0 mm","t":"3.0 mm"}}'
)


@pytest.fixture(scope="module")
def ocp():
    return pytest.importorskip("OCP", reason=NEED_OCP)


def tool(fn, **kwargs) -> ToolResult:
    result = getattr(fn, "fn", fn)(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def rules(result: ToolResult, rule: str) -> list[dict]:
    return [f for f in result.data["findings"] if f["rule"] == rule]


def build(spec: dict, name: str) -> str:
    r = tool(make_part, spec=json.dumps(spec), name=name)
    assert r.ok, r.error
    return next(a.path for a in r.artifacts if a.path.endswith(".step"))


def bracket_via(inner: float, decimals: int, *, ribs: bool = True) -> dict:
    """The C4 bracket (3 mm sheet, 80 x 40 base, 60 high upright, ribs at y 12..15 and 25..28) whose bend is two 3-point arcs
    through via points rounded to ``decimals``: the inner and outer cylinders are then slightly off-axis."""
    outer = inner + 3.0
    k = 1.0 - math.sqrt(0.5)
    outer_arc = {"to": [f"{outer} mm", "0.0 mm"], "via": [f"{round(outer * k, decimals)} mm"] * 2}
    inner_arc = {"to": ["3.0 mm", f"{outer} mm"], "via": [f"{round(3.0 + inner * k, decimals)} mm"] * 2}
    segments = [
        {"to": ["0.0 mm", f"{outer} mm"]}, outer_arc, {"to": ["80.0 mm", "0.0 mm"]}, {"to": ["80.0 mm", "3.0 mm"]},
        {"to": [f"{outer} mm", "3.0 mm"]}, inner_arc, {"to": ["3.0 mm", "60.0 mm"]}, {"to": ["0.0 mm", "60.0 mm"]},
    ]
    features: list[dict] = [{"op": "extrude", "name": "l_body", "plane": "xz", "offset": "0.0 mm", "distance": "40.0 mm", "mode": "add",
                             "profile": {"path": {"start": ["0.0 mm", "60.0 mm"], "segments": segments}}}]
    if ribs:
        for name, offset in (("gusset_a", "12.0 mm"), ("gusset_b", "25.0 mm")):
            features.append({"op": "extrude", "name": name, "plane": "xz", "offset": offset, "distance": "3.0 mm", "mode": "add",
                             "profile": {"polygon": {"points": [["3.0 mm", "3.0 mm"], ["28.0 mm", "3.0 mm"], ["3.0 mm", "28.0 mm"]]}}})
    return {"features": features}


def axis_offset_mm(step_path: str) -> float:
    """The measured distance between the axes of the inner (concave) and outer (convex) bend cylinders."""
    from eng_toolkit import brep

    solid = brep.read_solid(Path(step_path), param="model_path")
    inv = brep.inventory(solid)
    bends = [c for c in brep.cylinder_facts(solid, inv) if c.span_rad < 6]
    inner = min((c for c in bends if c.sense == "concave"), key=lambda c: c.face_id)
    outer = next(c for c in bends if c.sense == "convex")
    offset = np.array(inner.start_mm) - np.array(outer.start_mm)
    axis = np.array(inner.axis)
    return float(np.linalg.norm(offset - (offset @ axis) * axis))


def sheet_check(path: str, **kwargs) -> ToolResult:
    r = tool(check_dfm, model_path=path, process="sheet_metal", max_findings=500,
             limits=json.dumps({"min_bend_radius_ratio": 1.0, "max_thickness": "6 mm"}), **kwargs)
    assert r.ok, r.error
    return r


# --------------------------------------------------------------------------- C4-1: bends from rounded via points


def test_the_exact_c4_spec_has_its_bend_recognised_and_the_tolerance_named(ocp):
    """The round-2 failure: bends 0, unrecognised_bend_faces [1, 13, 15, 21], errors 0, warnings 0."""
    path = build(C4_SPEC, "c4_exact")
    offset = axis_offset_mm(path)
    assert brep.coaxial_tolerance_mm(3.0) < offset < 4e-3  # ~3.3e-3 mm: beyond 1e-3 x t, the case that was missed
    r = sheet_check(path)
    m = r.data["measurements"]
    assert m["bends"] == 1 and m["bend_face_pairs"] == 3 and m["unrecognised_bend_faces"] == []
    assert rules(r, "sheet.min_bend_radius") == []  # R3 = 1.0 t passes (3.0011 mm measured)
    (note,) = rules(r, "sheet.bend_tolerance")
    assert note["severity"] == "warn" and note["check"] == "heuristic"
    assert note["measured"]["value"] == pytest.approx(offset, abs=1e-6)  # the offset that was actually used
    assert note["limit"]["value"] == pytest.approx(dfm.BEND_RELAXED_AXIS_MM, abs=TOL)
    assert "relaxed tolerance" in note["message"] and "0.05 mm" in note["message"] and "radius and turn" in note["message"]
    assert note["details"]["exact_tolerance_mm"] == pytest.approx(brep.coaxial_tolerance_mm(3.0), abs=1e-5)
    (pairing,) = m["bend_pairing"]["bends"]
    assert pairing["rule"] == "relaxed" and pairing["axis_offset_mm"] == pytest.approx(offset, abs=1e-6)
    s = r.data["summary"]
    assert (s["errors"], s["warnings"], s["not_evaluated"]) == (0, 1, [])
    assert r.data["tool_axis_defaulted"] is False  # sheet metal has no tool axis


@pytest.mark.parametrize("decimals, expected_offset", [(3, 3.3e-3), (2, None)])
@pytest.mark.parametrize("inner", [2.0, 3.0])
def test_via_points_to_3_and_2_decimals_still_give_the_bend_check(ocp, inner, decimals, expected_offset):
    path = build(bracket_via(inner, decimals), f"c4_via_{int(inner)}_{decimals}")
    offset = axis_offset_mm(path)
    assert brep.coaxial_tolerance_mm(3.0) < offset <= dfm.BEND_RELAXED_AXIS_MM
    if expected_offset is not None:
        assert offset == pytest.approx(expected_offset, rel=0.1)
    r = sheet_check(path)
    m = r.data["measurements"]
    assert m["bends"] == 1 and m["unrecognised_bend_faces"] == [] and m["sharp_bends"] == 0 and m["joints"] == 8
    assert m["thickness_mm"] == pytest.approx(3.0, abs=0.05)
    radius_findings = rules(r, "sheet.min_bend_radius")
    if inner == 2.0:  # 0.667 t, below the 1.0 t limit: the bend the judge said was not reported
        (bend,) = radius_findings
        assert bend["severity"] == "error" and bend["measured"]["value"] == pytest.approx(2.0, abs=0.05)
        assert bend["measured"]["value"] / m["thickness_mm"] < 0.7
        assert bend["details"]["pairing"] == "relaxed" and bend["details"]["axis_offset_mm"] == pytest.approx(offset, abs=1e-6)
    else:  # R3 = 1.0 t, inclusive: it passes although the rounded radius may be a few micrometres or hundredths below 3
        assert radius_findings == []
    (note,) = rules(r, "sheet.bend_tolerance")
    assert note["measured"]["value"] == pytest.approx(offset, abs=1e-6)
    s = r.data["summary"]
    assert (s["errors"], s["warnings"]) == (1 if inner == 2.0 else 0, 1)
    assert "bend-like faces not evaluated" not in s["headline"]


@pytest.mark.parametrize("inner", [2.0, 3.0])
def test_exact_arcs_and_4_decimal_via_points_are_paired_by_the_exact_rule_without_a_warning(ocp, inner):
    r = sheet_check(build(bracket_via(inner, 4), f"c4_via4_{int(inner)}"))
    assert r.data["measurements"]["bend_pairing"]["bends"][0]["rule"] == "strict"
    assert rules(r, "sheet.bend_tolerance") == [] and r.data["summary"]["warnings"] == 0


def test_a_bend_that_pairs_with_nothing_is_a_warning_finding_and_the_summary_says_so(ocp):
    # via points to 1 decimal: the cylinders are 0.1 mm off-axis, beyond the relaxed 0.05 mm
    path = build(bracket_via(3.0, 1), "c4_via_1")
    assert axis_offset_mm(path) > dfm.BEND_RELAXED_AXIS_MM
    r = sheet_check(path)
    m = r.data["measurements"]
    assert m["bends"] == 0 and len(m["unrecognised_bend_faces"]) == 4
    (lost,) = rules(r, "sheet.bend_unrecognised")
    assert lost["severity"] == "warn" and lost["check"] == "heuristic"
    assert lost["measured"] == {"value": 4.0, "unit": "count"}
    assert lost["message"].startswith("4 bend-like faces not evaluated: ")
    assert "radius and turn" in lost["message"] and lost["face_ids"] == m["unrecognised_bend_faces"]
    s = r.data["summary"]
    assert s["errors"] == 0 and s["warnings"] == 1
    assert s["not_evaluated"] == ["4 bend-like faces not evaluated"]
    assert s["headline"] == "errors 0, warnings 1, info 2; 4 bend-like faces not evaluated"
    assert any("4 bend-like faces not evaluated" in w for w in r.warnings)  # also in the result's own warnings


# --------------------------------------------------------------------------- C1-10: tool_axis


def test_an_empty_tool_axis_is_plus_z_with_a_warning_and_a_flag(ocp, tmp_path):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    path = cad.write_step(fx.wall_block(1.0), tmp_path / "wall.step")
    left_out = tool(check_dfm, model_path=str(path), limits=json.dumps({"min_wall": "1.5 mm"}))
    assert left_out.ok and left_out.data["tool_axis"] == "+z" and left_out.data["tool_axis_defaulted"] is True
    assert dfm.TOOL_AXIS_DEFAULTED_WARNING == "tool_axis defaulted to +z: the shop's setup may differ"
    assert dfm.TOOL_AXIS_DEFAULTED_WARNING in left_out.warnings
    given = tool(check_dfm, model_path=str(path), limits=json.dumps({"min_wall": "1.5 mm"}), tool_axis="+z")
    assert given.data["tool_axis"] == "+z" and given.data["tool_axis_defaulted"] is False
    assert dfm.TOOL_AXIS_DEFAULTED_WARNING not in given.warnings
    assert left_out.data["findings"] == given.data["findings"]  # the default is the old behaviour
    sheet = tool(check_dfm, model_path=str(path), process="sheet_metal")
    assert sheet.data["tool_axis_defaulted"] is False and dfm.TOOL_AXIS_DEFAULTED_WARNING not in sheet.warnings
    bad = tool(check_dfm, model_path=str(path), tool_axis="w")
    assert bad.ok is False and bad.error.details["param"] == "/tool_axis"


def test_the_view_says_it_too_when_the_dfm_colouring_used_the_default_axis(ocp, tmp_path):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    path = cad.write_step(fx.wall_block(1.0), tmp_path / "wall.step")
    v = tool(view, model_path=str(path), color_by="dfm", limits=json.dumps({"min_wall": "1.5 mm"}), preview="none")
    assert v.ok and v.data["tool_axis_defaulted"] is True and dfm.TOOL_AXIS_DEFAULTED_WARNING in v.warnings
    v = tool(view, model_path=str(path), color_by="dfm", limits=json.dumps({"min_wall": "1.5 mm"}), preview="none", tool_axis="+z")
    assert v.data["tool_axis_defaulted"] is False and dfm.TOOL_AXIS_DEFAULTED_WARNING not in v.warnings


# --------------------------------------------------------------------------- C1-9: markers, close-up, flagged share


def png_of(result: ToolResult) -> np.ndarray:
    (found,) = [a for a in result.artifacts if a.path.endswith(".png")]
    width, height, image = read_png(Path(found.path).read_bytes())
    assert (width, height) == (result.data["preview"]["width"], result.data["preview"]["height"])
    return image


def ring_pixels_match(image: np.ndarray, x: float, y: float, radius: float, rgb=RING) -> int:
    """How many of 8 points on the circle of ``radius`` around the marker's pixel carry the ring colour (a pixel next to the
    point counts: the ring is 1.5 to 3 pixels wide and anti-aliased)."""
    hits = 0
    for k in range(8):
        angle = k * math.pi / 4
        px, py = int(math.floor(x + radius * math.cos(angle))), int(math.floor(y + radius * math.sin(angle)))
        near = image[py - 1 : py + 2, px - 1 : px + 2].astype(int)
        if np.abs(near - np.array(rgb)).max(axis=2).min() <= 40:
            hits += 1
    return hits


@pytest.fixture(scope="module")
def parts(ocp, tmp_path_factory):
    import cad_fixtures as cad
    import dfm_fixtures as fx

    root = tmp_path_factory.mktemp("r3_models")
    return {
        "heatsink": cad.write_step(fx.heatsink(), root / "heatsink.step"),
        "wall": cad.write_step(fx.wall_block(1.0), root / "wall.step"),
    }


def view_dfm(path, **kwargs) -> ToolResult:
    r = tool(view, model_path=str(path), color_by="dfm", tool_axis="+z", **kwargs)
    assert r.ok, r.error
    return r


def test_rings_mark_the_three_ligament_points_at_their_projected_positions(parts):
    r = view_dfm(parts["heatsink"], limits=SHOP)
    image = png_of(r)
    marks = r.data["preview"]["markers"]
    walls = sorted((m for m in marks if m["rule"] == "cnc.min_wall"), key=lambda m: m["point_mm"][0])
    assert [round(m["point_mm"][0]) for m in walls] == [-12, 0, 12] and all(m["view"] for m in walls)
    assert len(marks) == 12 and r.data["preview"]["views"] == ["iso", "front", "top", "right"]
    for m in marks:  # every ring is drawn in the colour of RING where the tool says it is
        assert m["x"] is not None and m["view"] in ("iso", "iso_under", "iso_back", "iso_under_back", "front", "top", "right")
        radius = 8.0 if m["inside_material"] else 9.0
        assert ring_pixels_match(image, m["x"], m["y"], radius) >= 7, (m["finding"], m["view"])
    # the three points lie 12 mm apart along +x: equal steps on the screen, in the direction the isometric camera gives +x
    iso = [m for m in walls if m["view"] == "iso"]
    if len(iso) == 3:
        step = np.array([iso[1]["x"] - iso[0]["x"], iso[1]["y"] - iso[0]["y"]])
        again = np.array([iso[2]["x"] - iso[1]["x"], iso[2]["y"] - iso[1]["y"]])
        assert np.allclose(step, again, atol=1.5)
        right, up = P._basis("iso")[0] @ np.array([1.0, 0, 0]), P._basis("iso")[1] @ np.array([1.0, 0, 0])
        assert np.allclose(step / np.linalg.norm(step), np.array([right, -up]) / math.hypot(right, up), atol=0.02)
    # the ring is not the colour of the face inside it: the face keeps its flag colour (error red) around the ring
    assert r.data["preview"]["groups"][0]["group"] == "dfm_error"


def test_a_marker_no_view_can_see_is_drawn_thin_as_a_point_inside_the_material(parts):
    r = view_dfm(parts["wall"], limits=json.dumps({"min_wall": "1.5 mm", "tool_radius": "1 mm"}))
    marks = {m["finding"]: m for m in r.data["preview"]["markers"]}
    assert set(marks) == {"D1", "D2", "D3", "D4", "D5"}
    assert any(m["inside_material"] for m in marks.values()) and any(m["view"] != "iso" for m in marks.values())
    image = png_of(r)
    for m in marks.values():
        assert ring_pixels_match(image, m["x"], m["y"], 8.0 if m["inside_material"] else 9.0) >= 7


def test_focus_adds_a_close_up_panel_with_a_scale_bar_and_the_face_id(parts):
    plain = view_dfm(parts["wall"], limits=json.dumps({"min_wall": "1.5 mm", "tool_radius": "1 mm"}))
    close = view_dfm(parts["wall"], limits=json.dumps({"min_wall": "1.5 mm", "tool_radius": "1 mm"}), focus="F10")
    assert plain.data["focus"] is None
    assert plain.data["preview"]["width"] == close.data["preview"]["width"] == P.SHEET_WIDTH
    focus = close.data["focus"]
    panel = focus["panel"]
    assert close.data["preview"]["height"] == plain.data["preview"]["height"] + panel["height"] + 12  # the extra panel
    assert panel["width"] == P.LEFT_W and focus["faces"] == ["F10"] and focus["zoom"] > 1.0 and focus["view"] in P.VIEW_NAMES
    image = png_of(close)
    # the iso panel above the close-up is the one of the plain sheet
    assert np.array_equal(image[84 : 84 + 500, : P.LEFT_W], png_of(plain)[84 : 84 + 500, : P.LEFT_W])
    inside = image[panel["y"] + 24 : panel["y"] + panel["height"] - 40, panel["x"] + 2 : panel["x"] + panel["width"] - 2]
    assert (inside != 255).any(axis=2).mean() > 0.2  # the model fills a good part of the panel
    # the scale bar: a dark line at the bottom left whose length in pixels is scale_bar_mm x scale_px_per_mm
    by = panel["y"] + panel["height"] - 22
    bar_px = round(focus["scale_bar_mm"] * focus["scale_px_per_mm"])
    row = image[by + 1, panel["x"] + 16 : panel["x"] + 16 + bar_px + 20]
    dark = (np.abs(row.astype(int) - np.array(P.INK)).max(axis=1) <= 6)
    assert dark[:bar_px].all() and not dark[bar_px + 1 : bar_px + 8].any()  # the bar, then a gap before its label
    assert focus["scale_bar_mm"] in (1, 2, 5, 10, 20, 50) and 0.1 * panel["width"] < bar_px <= 0.3 * panel["width"] + 1
    # the face id is written in the panel in the ink colour (F10 is a flat wall: visible, so labelled)
    assert focus["labels_drawn"] == ["F10"] and focus["labels_not_drawn"] == []
    assert close.data["output"]["glb"]["unit"] == "metre"
    glb = next(Path(a.path) for a in close.artifacts if a.path.endswith(".glb"))
    assert glb.read_bytes() == next(Path(a.path) for a in plain.artifacts if a.path.endswith(".glb")).read_bytes()  # focus only changes the PNG


def test_the_close_up_is_framed_on_the_box_of_the_faces_with_a_20_percent_margin(ocp, tmp_path):
    import cad_fixtures as cad
    import dfm_fixtures as fx
    from eng_toolkit import brep

    # a 100 x 80 x 10 plate with a 6 x 6 x 6 block on it: the block's top face (6 x 6 mm, z = 16) is the face to enlarge
    part = fx.fuse(cad.box(length=100, width=80, height=10), cad.box(x=47, y=37, z=10, length=6, width=6, height=6))
    path = cad.write_step(part, tmp_path / "bump.step")
    inv = brep.inventory(brep.read_solid(path, param="model_path"))
    (top,) = [f.id for f in inv.faces if f.kind == "plane" and f.normal and f.normal[2] > 0.99 and abs(f.bbox_min_mm[2] - 16.0) < 1e-6]
    r = tool(view, model_path=str(path), color_by="face_kind", focus=f"F{top}")
    assert r.ok, r.error
    focus = r.data["focus"]
    box = np.array([[47.0, 37.0, 16.0], [53.0, 43.0, 16.0]])
    corners = np.array([[x, y, z] for x in box[:, 0] for y in box[:, 1] for z in box[:, 2]])
    seen = corners @ P._basis(focus["view"]).T
    size = seen.max(axis=0) - seen.min(axis=0)
    framed = size[:2] * (1.0 + 2.0 * P.CROP_MARGIN)  # the face plus 20 % of its size on each side
    expected_scale = min((P.LEFT_W - 20) / framed[0], (440 - 22 - 20) / framed[1])
    assert focus["scale_px_per_mm"] == pytest.approx(expected_scale, rel=0.01)
    iso_scale = min((P.LEFT_W - 20) / (100 * math.sqrt(0.5) + 80 * math.sqrt(0.5)), 1e9)
    assert focus["zoom"] > 5.0 and focus["zoom"] == pytest.approx(expected_scale / iso_scale, rel=0.2)
    assert focus["scale_bar_mm"] == 2.0  # the largest of 1, 2, 5 x 10^k below 30 % of the panel width: 225 px / 57 px per mm
    assert focus["labels_drawn"] == [f"F{top}"]
    assert focus["box_mm"] == [[47.0, 37.0, 16.0], [53.0, 43.0, 16.0]]


@pytest.mark.parametrize(
    "focus, param, family",
    [("F999", "/focus", "geometry.unknown_entity"), ("nonsense", "/focus", "input.invalid"), ("D99", "/focus", "input.invalid"),
     ("F3-F1", "/focus", "input.invalid"), (",", "/focus", "input.missing")],
)
def test_a_bad_focus_is_refused_at_its_own_pointer(parts, focus, param, family):
    r = tool(view, model_path=str(parts["wall"]), color_by="dfm", tool_axis="+z", focus=focus)
    assert r.ok is False and r.error.family == family and r.error.details["param"] == param


def test_focus_takes_a_highlight_group_and_a_finding_id_and_none_without_dfm(parts):
    g = tool(view, model_path=str(parts["wall"]), color_by="highlight", highlight="pocket: F7,F8; floor: F5", focus="pocket")
    assert g.ok, g.error
    assert g.data["focus"]["faces"] == ["F7", "F8"] and g.data["focus"]["groups"] == ["pocket"]
    d = view_dfm(parts["wall"], limits=json.dumps({"min_wall": "1.5 mm", "tool_radius": "1 mm"}), focus="D5")
    assert d.data["focus"]["findings"] == ["D5"] and d.data["focus"]["faces"] == ["F1", "F10"]
    refused = tool(view, model_path=str(parts["wall"]), color_by="face_kind", focus="D1")
    assert refused.ok is False and refused.error.details["param"] == "/focus"
    quiet = tool(view, model_path=str(parts["wall"]), color_by="face_kind", focus="F1", preview="none")
    assert quiet.ok and any("focus draws a close-up in the PNG" in w for w in quiet.warnings)


def test_flagged_area_share_says_how_much_of_a_coloured_face_a_finding_concerns(parts):
    r = view_dfm(parts["wall"], limits=json.dumps({"min_wall": "1.5 mm", "tool_radius": "1 mm"}))
    # the 1 mm wall x 0..1: the outer face x = 0 is 40 x 20 = 800 mm2 and the pocket wall x = 1 is 20 x 15 = 300 mm2, so the
    # overlap is at most 300 mm2: 300 / 800 of the outer face, all of the pocket wall
    (wall,) = [f for f in r.data["findings_coloured"] if f["rule"] == "cnc.min_wall"]
    assert wall["footprint"] == "area_bound" and wall["flagged_area_mm2"] == pytest.approx(600.0, abs=TOL)
    assert wall["flagged_area_share"] == {"F1": pytest.approx(0.375, abs=1e-6), "F10": pytest.approx(1.0, abs=1e-6)}
    corners = [f for f in r.data["findings_coloured"] if f["rule"] == "cnc.internal_corner_radius"]
    assert len(corners) == 4 and all(f["footprint"] == "edge" and f["flagged_area_mm2"] is None for f in corners)
    (group,) = r.data["flagged_area_share"]
    assert group["group"] == "dfm_error" and group["faces"] == 5 and group["area_mm2"] == pytest.approx(2300.0, abs=1e-3)
    assert group["flagged_area_mm2_upper_bound"] == pytest.approx(600.0, abs=1e-3)
    assert group["flagged_area_share"] == pytest.approx(600.0 / 2300.0, abs=1e-3)
    assert group["faces_local_only"] == ["F7", "F8", "F9"]
    assert any(text.startswith("F1 is coloured because of D5: at most 300 mm2 of its 800 mm2 (38 %)") for text in group["explanations"])
    assert any("F7 is coloured because of D1, D2" in text and "not over the whole face" in text for text in group["explanations"])


def test_the_hole_ligament_floor_is_named_as_a_local_finding_not_a_wholly_thin_face(parts):
    r = view_dfm(parts["heatsink"], limits=SHOP)
    walls = [f for f in r.data["findings_coloured"] if f["rule"] == "cnc.min_wall"]
    assert len(walls) == 3 and all(f["measured"]["value"] == pytest.approx(0.94, abs=TOL) for f in walls)
    (group,) = r.data["flagged_area_share"]
    (floor_id,) = set.intersection(*(set(f["face_ids"]) for f in walls))  # the bottom face every ligament finding names
    floor = [text for text in group["explanations"] if text.startswith(f"F{floor_id} is coloured because of")]
    assert len(floor) == 1 and "cnc.min_wall 0.94 mm" in floor[0] and "3 points" in floor[0] and "see the rings" in floor[0]
    assert "area not measured" in floor[0]
    assert group["flagged_area_share"] < 1.0
    for f in walls:  # the finding's own point is where the ring is
        ring = next(m for m in r.data["preview"]["markers"] if m["finding"] == f["id"])
        assert ring["point_mm"] == pytest.approx(f["location_mm"], abs=1e-4)


KICAD = Path(
    r"C:\Users\USER\AppData\Local\Programs\KiCad\10.0\share\kicad\3dmodels\Heatsink.3dshapes"
    r"\Heatsink_125x35x50mm_3xFixationM3.step"
)


@pytest.mark.skipif(not KICAD.exists(), reason="the KiCad heatsink is not installed")
def test_the_kicad_heatsink_shows_rings_at_the_three_ligaments_and_a_close_up_of_a_hole(ocp):
    r = tool(view, model_path=str(KICAD), color_by="dfm", tool_axis="+z", limits=SHOP, mesh_deflection="0.05 mm", focus="F71")
    assert r.ok, r.error
    marks = r.data["preview"]["markers"]
    walls = [m for m in marks if m["rule"] == "cnc.min_wall"]
    assert len(walls) == 3 and all(m["labelled"] for m in walls) and len(marks) == 28
    image = png_of(r)
    for m in marks:
        assert m["x"] is not None and ring_pixels_match(image, m["x"], m["y"], 8.0 if m["inside_material"] else 9.0) >= 5  # neighbouring rings overlap and a halo covers part of the other ring
    assert r.data["focus"]["faces"] == ["F71"] and r.data["focus"]["zoom"] > 1.5
    (group,) = r.data["flagged_area_share"]
    assert any(text.startswith("F1 is coloured because of D26, D27, D28") for text in group["explanations"])
