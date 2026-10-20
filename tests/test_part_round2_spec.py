"""Round 2 of the part spec language, the pure part: grammar, field-level errors and the geometry of path outlines.

No CAD engine is needed.  The built shapes are checked against hand calculations in tests/test_make_part_round2.py.
"""

import json
import math

import pytest
from part_fixtures import box, hole, spec

from eng_toolkit import brep
from eng_toolkit.domains.geometry import part
from eng_toolkit.domains.geometry.part import OPS, parse_spec
from eng_toolkit.errors import ToolFailure

PLATE = box(("100 mm", "60 mm", "8 mm"))
HOLE = {**hole([("10 mm", "10 mm", "8 mm")], "6 mm"), "name": "h"}


def pattern(**extra):
    return {"op": "pattern", "of": "h", "count": 4, "step": ["20 mm", "0 mm", "0 mm"], **extra}


def rejected(features, pointer: str, *, family: str = "input.invalid") -> ToolFailure:
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec(features))
    assert info.value.family == family, info.value
    assert info.value.details["pointer"] == pointer, (info.value.details["pointer"], info.value.message)
    return info.value


def stadium(**extra):
    path = {
        "start": ["-10 mm", "-10 mm"],
        "segments": [
            {"to": ["10 mm", "-10 mm"]},
            {"to": ["10 mm", "10 mm"], "radius": "10 mm", "turn": "left"},
            {"to": ["-10 mm", "10 mm"]},
            {"to": ["-10 mm", "-10 mm"], "radius": "10 mm", "turn": "left"},
        ],
    }
    path.update(extra)
    return {"path": path}


def extrude(profile, **extra):
    return {"op": "extrude", "profile": profile, "distance": "5 mm", **extra}


# --------------------------------------------------------------------------- accepted


def test_a_linear_pattern_parses_to_millimetres_and_names_its_source():
    parsed = parse_spec(spec([PLATE, HOLE, pattern(step=["2 cm", "0 mm", "0 mm"])]))
    args = parsed.features[2].args
    assert args["of"] == "h" and args["count"] == 4 and args["step"] == (20.0, 0.0, 0.0)
    assert args["angle"] is None and args["center"] is None
    assert parsed.referenced == frozenset()  # only of: terms ask the builder to record what a feature created


def test_a_polar_pattern_parses_with_its_center_and_a_360_degree_default_axis():
    polar = {"op": "pattern", "of": "h", "count": 6, "angle": "360 deg", "center": ["50 mm", "50 mm", "0 mm"]}
    args = parse_spec(spec([PLATE, HOLE, polar])).features[2].args
    assert args["angle"] == pytest.approx(2 * math.pi) and args["center"] == (50.0, 50.0, 0.0) and args["axis"] is None


def test_the_canonical_form_of_every_round_two_op_is_itself_a_valid_spec_with_the_same_hash():
    features = [
        PLATE,
        HOLE,
        pattern(name="row"),
        {"op": "mirror", "plane": "yz", "offset": "50 mm", "of": "h", "name": "m"},
        {"op": "extrude", "name": "e", "profile": stadium(), "distance": "5 mm", "taper": "5 deg", "plane": "xy", "offset": "8 mm"},
        {"op": "shell", "faces": ">z & of:e", "thickness": "1 mm"},
        {"op": "fillet", "edges": "of:e & circle", "radius": "1 mm"},
        {"op": "import", "file": "block.step", "at": ["1 mm", "2 mm", "3 mm"], "mode": "cut"},
        {"op": "mirror", "plane": "xz"},
    ]
    first = parse_spec(spec(features))
    again = parse_spec(first.canonical)
    assert again.canonical == first.canonical and again.sha256 == first.sha256
    assert json.loads(first.canonical)["features"][4]["profile"]["path"]["segments"][1]["turn"] == "left"


def test_the_description_names_every_new_op_and_the_path_profile():
    from eng_toolkit.domains.geometry.tools import MAKE_PART_DESCRIPTION

    for name in ("pattern", "mirror", "shell", "import"):
        assert f"- {name} (" in MAKE_PART_DESCRIPTION
    assert '{"path": {start* V2, segments* SEGS}}' in MAKE_PART_DESCRIPTION
    assert "of:NAME" in MAKE_PART_DESCRIPTION and "taper" in MAKE_PART_DESCRIPTION


# --------------------------------------------------------------------------- selector groups


@pytest.mark.parametrize(
    ("op", "field", "size"),
    [("shell", "faces", {"thickness": "1 mm"}), ("fillet", "edges", {"radius": "1 mm"}), ("chamfer", "edges", {"distance": "1 mm"})],
)
def test_selector_groups_preserve_precedence_in_the_canonical_spec(op, field, size):
    text = " of:body& >z  or  of:body & <z "
    parsed = parse_spec(spec([{**PLATE, "name": "body"}, {"op": op, field: text, **size}]))
    selector = parsed.features[1].args[field]
    assert selector.groups == (("of:body", ">z"), ("of:body", "<z"))
    assert parsed.referenced == frozenset({"body"})
    assert json.loads(parsed.canonical)["features"][1][field] == "of:body & >z or of:body & <z"
    again = parse_spec(parsed.canonical)
    assert again.features[1].args[field].groups == selector.groups
    assert again.sha256 == parsed.sha256


@pytest.mark.parametrize("parse", [brep.parse_edge_selector, brep.parse_face_selector])
def test_or_is_a_whitespace_delimited_token_and_eight_groups_are_allowed(parse):
    assert parse("of:or or of:floor & >z").groups == (("of:or",), ("of:floor", ">z"))
    assert len(parse(" or ".join([">z"] * 8)).groups) == 8


@pytest.mark.parametrize("field", ["faces", "edges"])
def test_a_feature_named_or_is_unambiguous_and_all_union_references_are_validated(field):
    feature = {"op": "shell", "faces": "of:or or <z", "thickness": "1 mm"} if field == "faces" else {"op": "fillet", "edges": "of:or or <z", "radius": "1 mm"}
    parsed = parse_spec(spec([{**PLATE, "name": "or"}, feature]))
    assert parsed.referenced == frozenset({"or"})
    rejected([{**PLATE, "name": "or"}, {**feature, field: "of:or or of:ghost"}], f"/features/1/{field}")


@pytest.mark.parametrize("parse", [brep.parse_edge_selector, brep.parse_face_selector])
@pytest.mark.parametrize("text", [">z or", "or >z", ">z or or <z", ">z OR <z", ">z || <z", ">z, <z", ">zor <z", ">z or<z", "(>z or <z)", ">z & or <z", ">z or <z &"])
def test_invalid_union_syntax_is_refused_by_both_parsers(parse, text):
    with pytest.raises(ToolFailure) as info:
        parse(text)
    assert info.value.family == "input.invalid"
    assert info.value.details["allowed"]


@pytest.mark.parametrize("field", ["faces", "edges"])
def test_the_group_limit_reaches_the_selector_field(field):
    feature = {"op": "shell", "thickness": "1 mm"} if field == "faces" else {"op": "fillet", "radius": "1 mm"}
    error = rejected([PLATE, {**feature, field: " or ".join([">z"] * 9)}], f"/features/1/{field}")
    assert error.details["limit"] == 8
    assert error.details["allowed"]


# --------------------------------------------------------------------------- pattern errors


@pytest.mark.parametrize("count", [0, 1, -3, 257, 1e9, 4.5, "4", None, True, [4]])
def test_a_bad_pattern_count_names_the_count_field(count):
    rejected([PLATE, HOLE, pattern(count=count)], "/features/2/count")


def test_a_count_written_as_a_whole_float_is_accepted():
    assert parse_spec(spec([PLATE, HOLE, pattern(count=4.0)])).features[2].args["count"] == 4


@pytest.mark.parametrize("step", [["1e308 mm", "0 mm", "0 mm"], ["1e10 mm", "0 mm", "0 mm"], ["9**9**9 mm", "0 mm", "0 mm"]])
def test_an_absurd_pattern_step_is_refused_at_the_field(step):
    rejected([PLATE, HOLE, pattern(step=step)], "/features/2/step/0")


def test_a_json_number_for_a_step_is_refused_as_a_bare_number():
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([PLATE, HOLE, pattern(step=[1e308, 0, 0])]))
    assert info.value.family == "units.bare_number"
    assert info.value.details["pointer"] == "/features/2/step/0"


def test_a_step_that_reaches_beyond_the_supported_size_is_refused():
    rejected([PLATE, HOLE, pattern(count=256, step=["900 m", "0 mm", "0 mm"])], "/features/2/step")


def test_a_zero_step_is_refused():
    rejected([PLATE, HOLE, pattern(step=["0 mm", "0 mm", "0 mm"])], "/features/2/step")


def test_a_pattern_needs_exactly_one_of_step_and_angle():
    rejected([PLATE, HOLE, {"op": "pattern", "of": "h", "count": 4}], "/features/2/step")
    rejected([PLATE, HOLE, pattern(angle="90 deg")], "/features/2/angle")


def test_a_center_or_axis_belongs_to_the_polar_pattern_only():
    rejected([PLATE, HOLE, pattern(center=["0 mm", "0 mm", "0 mm"])], "/features/2/center")
    rejected([PLATE, HOLE, pattern(axis="+z")], "/features/2/axis")


@pytest.mark.parametrize("angle", ["0 deg", "-10 deg", "361 deg", "90 mm", 90])
def test_a_polar_angle_must_be_above_0_and_at_most_360_degrees(angle):
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([PLATE, HOLE, {"op": "pattern", "of": "h", "count": 3, "angle": angle}]))
    assert info.value.details["pointer"] == "/features/2/angle"


def test_a_pattern_of_something_that_does_not_exist_or_cannot_be_copied():
    rejected([PLATE, HOLE, pattern(of="nope")], "/features/2/of")
    fil = {"op": "fillet", "name": "f", "edges": "all", "radius": "1 mm"}
    rejected([PLATE, HOLE, fil, pattern(of="f")], "/features/3/of")
    rejected([PLATE, HOLE, pattern(name="row"), pattern(of="row")], "/features/3/of")
    cut = box(("5 mm", "5 mm", "5 mm"), mode="intersect", name="i")
    rejected([PLATE, cut, {"op": "pattern", "of": "i", "count": 2, "step": ["1 mm", "0 mm", "0 mm"]}], "/features/2/of")


def test_a_pattern_cannot_refer_forward_and_a_shared_name_is_ambiguous():
    rejected([PLATE, pattern(), HOLE], "/features/1/of")
    twin = {**HOLE, "at": [["50 mm", "30 mm", "8 mm"]]}
    rejected([PLATE, HOLE, twin, pattern()], "/features/3/of")


def test_the_copies_of_all_patterns_are_limited():
    many = [PLATE, HOLE] + [pattern(count=256, step=["0.001 mm", "0 mm", "0 mm"]) for _ in range(2)]
    error = rejected(many, "/features/3/count")
    assert error.details["limit"] == part.MAX_PATTERN_COPIES


def test_the_hole_positions_of_a_pattern_count_against_the_spec_limit():
    big = {**hole([(f"{i} mm", "10 mm", "8 mm") for i in range(10)], "1 mm"), "name": "h"}
    rejected([PLATE, big, pattern(count=256)], "/features/2/count")


# --------------------------------------------------------------------------- mirror, shell, import, taper errors


def test_a_mirror_needs_a_plane_and_a_known_feature():
    rejected([PLATE, {"op": "mirror"}], "/features/1/plane")
    rejected([PLATE, {"op": "mirror", "plane": "yz", "of": "ghost"}], "/features/1/of")
    rejected([PLATE, {"op": "mirror", "plane": "ab"}], "/features/1/plane")


def test_a_shell_needs_a_face_selector_and_a_positive_thickness():
    rejected([PLATE, {"op": "shell", "thickness": "1 mm"}], "/features/1/faces")
    rejected([PLATE, {"op": "shell", "faces": "|z", "thickness": "1 mm"}], "/features/1/faces")
    rejected([PLATE, {"op": "shell", "faces": ">z", "thickness": "0 mm"}], "/features/1/thickness")
    rejected([PLATE, {"op": "shell", "faces": ">z", "thickness": "-1 mm"}], "/features/1/thickness")


def test_an_of_term_must_name_an_earlier_feature_that_has_a_tool_shape():
    rejected([PLATE, {"op": "fillet", "edges": "of:ghost", "radius": "1 mm"}], "/features/1/edges")
    rejected([PLATE, {"op": "shell", "faces": "of:ghost", "thickness": "1 mm"}], "/features/1/faces")
    fil = {"op": "fillet", "name": "f", "edges": "all", "radius": "1 mm"}
    rejected([PLATE, fil, {"op": "chamfer", "edges": "of:f", "distance": "0.1 mm"}], "/features/2/edges")
    rejected([{**PLATE, "name": "body"}, {"op": "fillet", "edges": "of:body & of:later", "radius": "1 mm"}, {**PLATE, "name": "later", "mode": "add"}], "/features/1/edges")


def test_an_of_term_with_a_bad_name_lists_the_allowed_terms():
    error = rejected([PLATE, {"op": "fillet", "edges": "of: & all", "radius": "1 mm"}], "/features/1/edges")
    assert "of:<feature name>" in error.details["allowed"]


@pytest.mark.parametrize("taper", ["90 deg", "-90 deg", "46 deg", "-5 deg", "-46 deg", "1.6 rad", "5 mm", 5])
def test_a_taper_outside_0_to_45_degrees_or_without_an_angle_unit_names_the_field(taper):
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([extrude({"rect": {"size": ["10 mm", "10 mm"]}}, taper=taper)]))
    assert info.value.details["pointer"] == "/features/0/taper"


def test_a_taper_of_0_to_exactly_45_degrees_is_accepted():
    for taper in ("45 deg", "0 deg", "0.5 deg", "0.7853981633974483 rad"):
        assert parse_spec(spec([extrude({"rect": {"size": ["10 mm", "10 mm"]}}, taper=taper)])).features[0].args["taper"] is not None


def test_an_import_needs_a_model_file_name():
    rejected([{"op": "import"}], "/features/0/file")
    rejected([{"op": "import", "file": "notes.txt"}], "/features/0/file")
    rejected([{"op": "import", "file": ""}], "/features/0/file")
    rejected([{"op": "import", "file": 5}], "/features/0/file")
    parsed = parse_spec(spec([{"op": "import", "file": " C:/parts/block.STEP ", "at": ["1 mm", "0 mm", "0 mm"]}]))
    assert parsed.features[0].args["file"] == "C:/parts/block.STEP"


def test_build_without_a_file_opener_refuses_an_import():
    pytest.importorskip("OCP")
    parsed = parse_spec(spec([{"op": "import", "file": "block.step"}]))
    with pytest.raises(ToolFailure) as info:
        part.build(parsed)
    assert info.value.details["pointer"] == "/features/0/file"


# --------------------------------------------------------------------------- path profiles


def test_a_stadium_path_is_valid():
    path = parse_spec(spec([extrude(stadium())])).features[0].args["profile"]
    assert isinstance(path, brep.Path2) and len(path.segments) == 4
    assert brep.check_path(path) == []


def test_a_path_that_is_not_closed_points_at_the_last_segment():
    open_path = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["10 mm", "0 mm"]}, {"to": ["10 mm", "10 mm"]}, {"to": ["0 mm", "10 mm"]}]}}
    error = rejected([extrude(open_path)], "/features/0/profile/path/segments/2/to")
    assert "not closed" in error.message


def test_a_bow_tie_is_refused_as_self_crossing():
    bow = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["10 mm", "10 mm"]}, {"to": ["10 mm", "0 mm"]}, {"to": ["0 mm", "10 mm"]}, {"to": ["0 mm", "0 mm"]}]}}
    error = rejected([extrude(bow)], "/features/0/profile/path/segments/2/to")
    assert "crosses itself" in error.message


def test_a_spike_that_folds_back_on_itself_is_refused():
    spike = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["10 mm", "0 mm"]}, {"to": ["5 mm", "0 mm"]}, {"to": ["5 mm", "5 mm"]}, {"to": ["0 mm", "0 mm"]}]}}
    rejected([extrude(spike)], "/features/0/profile/path/segments/1/to")


def test_two_arcs_that_cross_each_other_are_refused():
    # an eight: two semicircles joined so that the outline passes through itself
    eight = {
        "path": {
            "start": ["0 mm", "0 mm"],
            "segments": [
                {"to": ["10 mm", "0 mm"], "via": ["5 mm", "5 mm"]},
                {"to": ["0 mm", "0 mm"], "via": ["5 mm", "-5 mm"]},
                {"to": ["10 mm", "0 mm"], "via": ["5 mm", "-5 mm"]},
                {"to": ["0 mm", "0 mm"], "via": ["5 mm", "5 mm"]},
            ],
        }
    }
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([extrude(eight)]))
    assert info.value.family == "input.invalid" and info.value.details["pointer"].startswith("/features/0/profile/path/segments")


def test_an_arc_radius_smaller_than_half_the_chord_names_the_radius():
    tight = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["10 mm", "0 mm"], "radius": "4 mm"}, {"to": ["0 mm", "0 mm"]}]}}
    error = rejected([extrude(tight)], "/features/0/profile/path/segments/0/radius")
    assert "half the chord" in error.message


def test_via_points_on_one_line_name_the_via_field():
    flat = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["10 mm", "0 mm"], "via": ["5 mm", "0 mm"]}, {"to": ["0 mm", "0 mm"]}]}}
    rejected([extrude(flat)], "/features/0/profile/path/segments/0/via")


def test_a_zero_length_segment_is_refused():
    zero = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["0 mm", "0 mm"]}, {"to": ["10 mm", "0 mm"]}, {"to": ["0 mm", "5 mm"]}, {"to": ["0 mm", "0 mm"]}]}}
    rejected([extrude(zero)], "/features/0/profile/path/segments/0/to")


@pytest.mark.parametrize(
    ("segment", "pointer"),
    [
        ({"to": ["1 mm", "1 mm"], "radius": "2 mm", "via": ["0 mm", "1 mm"]}, "/via"),
        ({"to": ["1 mm", "1 mm"], "turn": "left"}, "/turn"),
        ({"to": ["1 mm", "1 mm"], "radius": "2 mm", "turn": "up"}, "/turn"),
        ({"to": ["1 mm", "1 mm"], "radius": "0 mm"}, "/radius"),
        ({"to": ["1 mm", "1 mm"], "radius": "-2 mm"}, "/radius"),
        ({"radius": "2 mm"}, "/to"),
        ({"to": ["1 mm", "1 mm"], "bulge": "1 mm"}, "/bulge"),
    ],
)
def test_a_malformed_segment_names_its_field(segment, pointer):
    profile = {"path": {"start": ["0 mm", "0 mm"], "segments": [segment, {"to": ["0 mm", "0 mm"]}]}}
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([extrude(profile)]))
    assert info.value.details["pointer"] == f"/features/0/profile/path/segments/0{pointer}"


def test_a_path_has_two_to_sixty_four_segments():
    one = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": ["0 mm", "0 mm"]}]}}
    rejected([extrude(one)], "/features/0/profile/path/segments")
    many = {"path": {"start": ["0 mm", "0 mm"], "segments": [{"to": [f"{i + 1} mm", "0 mm"]} for i in range(70)]}}
    rejected([extrude(many)], "/features/0/profile/path/segments")


# --------------------------------------------------------------------------- the outline geometry by hand


def _area(points) -> float:
    x, y = points[:, 0], points[:, 1]
    return abs(float((x * (y.take(range(1, len(y) + 1), mode="wrap")) - y * x.take(range(1, len(x) + 1), mode="wrap")).sum())) / 2.0


def test_the_radius_arc_midpoint_lies_on_the_circle_and_bulges_to_the_right_when_turning_left():
    # from (10, -10) to (10, 10), radius 10: a half circle about (10, 0); turning left (counter-clockwise) it passes (20, 0)
    from eng_toolkit.brep import _path

    via = _path.radius_arc_via((10.0, -10.0), (10.0, 10.0), 10.0, "left")
    assert via == pytest.approx((20.0, 0.0))
    assert _path.radius_arc_via((10.0, -10.0), (10.0, 10.0), 10.0, "right") == pytest.approx((0.0, 0.0))
    # a 90 degree arc of radius 10 from (10, 0) to (0, 10) about the origin, turning left, passes (7.0711, 7.0711)
    assert _path.radius_arc_via((10.0, 0.0), (0.0, 10.0), 10.0, "left") == pytest.approx((10 / math.sqrt(2),) * 2)


def test_the_flattened_stadium_has_the_closed_form_area_to_the_flattening_error():
    # two 20 mm lines and two half circles of radius 10: 20 * 20 + pi * 10^2 = 714.159; a 5 degree polygon of the arcs is short by < 0.1 %
    from eng_toolkit.brep import _path

    path = parse_spec(spec([extrude(stadium())])).features[0].args["profile"]
    pieces, problems = _path.resolve(path)
    assert problems == []
    area = _area(_path.polyline(pieces))
    assert area == pytest.approx(400 + math.pi * 100, rel=1e-3) and area < 400 + math.pi * 100
