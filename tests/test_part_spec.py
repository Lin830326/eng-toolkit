"""The part spec language: parsing, unit expressions, the expression guard, selectors and the generated description.

Everything here is pure: no CAD engine is needed.
"""

import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest
from part_fixtures import PROBE_FEATURES, PROBE_SPEC, box, chamfer, cone, cylinder, fillet, hole, sphere, spec

from eng_toolkit import brep
from eng_toolkit.domains.geometry import part
from eng_toolkit.domains.geometry.part import OPS, check_expression, parse_spec
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import parse_quantity

README = Path(__file__).resolve().parents[1] / "docs" / "tools" / "geometry.md"  # the tool reference of the domain
DESCRIPTION_LIMIT = 5200  # characters; round 1 had 3900 for nine ops, round 2 adds seven features of the language (docs/geometry-drawing/make-part-round2.md)
BODIES = {"box", "cylinder", "sphere", "cone", "extrude", "import", "revolve"}


def rejected(text: str, family: str = "input.invalid") -> ToolFailure:
    with pytest.raises(ToolFailure) as info:
        parse_spec(text)
    assert info.value.family == family, info.value
    return info.value


def sized(*size) -> str:
    return spec([box(size)])


# --------------------------------------------------------------------------- accepted specs


def test_the_probe_spec_parses_into_three_features_in_millimetres():
    parsed = parse_spec(PROBE_SPEC)
    assert [f.op for f in parsed.features] == ["box", "hole", "fillet"]
    assert parsed.features[0].args["size"] == (100.0, 50.0, 20.0)
    assert parsed.features[1].args["at"] == ((25.0, 25.0, 20.0),)
    assert parsed.features[1].args["axis"] == "-z" and parsed.features[1].args["depth"] is None
    assert parsed.features[2].args["radius"] == 2.0


def test_units_are_converted_to_millimetres():
    parsed = parse_spec(sized("100 mm", "5 cm", "0.002 m"))
    assert parsed.features[0].args["size"] == (100.0, 50.0, 2.0)


def test_unit_expressions_are_evaluated():
    parsed = parse_spec(sized("(100 mm) / 4 + 2 mm", "2 * (10 mm + 5 mm)", "(10 mm ** 2) / (5 mm)"))
    assert parsed.features[0].args["size"] == (27.0, 30.0, 2.0)


def test_parameters_chain_in_order():
    text = spec([box(("$c", "$a", "$b"))], a="10 mm", b="$a * 2", c="$b + $a")
    parsed = parse_spec(text)
    assert parsed.features[0].args["size"] == (30.0, 10.0, 20.0)
    assert parsed.params == {"a": "10.0 mm", "b": "20.0 mm", "c": "30.0 mm"}


def test_an_angle_parameter_is_usable_where_an_angle_is_needed():
    text = spec(
        [{"op": "revolve", "plane": "xz", "profile": {"rect": {"size": ["2 mm", "3 mm"], "at": ["1 mm", "0 mm"]}}, "angle": "$turn"}],
        turn="90 deg",
    )
    parsed = parse_spec(text)
    assert parsed.features[0].args["angle"] == pytest.approx(math.pi / 2, abs=1e-12)
    number, unit = parsed.params["turn"].split()
    assert unit == "rad" and float(number) == pytest.approx(math.pi / 2, abs=1e-12)


def test_defaults_are_filled_in_the_canonical_spec():
    canonical = json.loads(parse_spec(sized("1 mm", "2 mm", "3 mm")).canonical)
    (feature,) = canonical["features"]
    assert feature == {
        "op": "box",
        "name": "box1",
        "size": ["1.0 mm", "2.0 mm", "3.0 mm"],
        "at": ["0.0 mm", "0.0 mm", "0.0 mm"],
        "mode": "add",
    }


def test_the_canonical_spec_is_itself_a_valid_spec_with_the_same_canonical_form():
    first = parse_spec(PROBE_SPEC)
    again = parse_spec(first.canonical)
    assert again.canonical == first.canonical
    assert again.sha256 == first.sha256


def test_whitespace_and_key_order_do_not_change_the_canonical_spec():
    plain = parse_spec(PROBE_SPEC)
    document = json.loads(PROBE_SPEC)
    reordered = {
        "features": [dict(reversed(list(f.items()))) for f in document["features"]],
        "params": document["params"],
    }
    other = parse_spec(json.dumps(reordered, indent=4))
    assert other.canonical == plain.canonical
    assert other.sha256 == plain.sha256


def test_different_geometry_changes_the_hash():
    assert parse_spec(sized("1 mm", "2 mm", "3 mm")).sha256 != parse_spec(sized("1 mm", "2 mm", "4 mm")).sha256


def test_equal_lengths_in_different_units_have_the_same_canonical_form():
    assert parse_spec(sized("25.4 mm", "1 mm", "1 mm")).canonical == parse_spec(sized("1 inch", "1 mm", "1 mm")).canonical


def test_the_default_feature_name_is_the_op_and_its_position():
    parsed = parse_spec(spec([box(("1 mm", "1 mm", "1 mm")), fillet("all", "0.1 mm"), {**box(("1 mm", "1 mm", "1 mm")), "name": "extra"}]))
    assert [f.name for f in parsed.features] == ["box1", "fillet2", "extra"]


def test_two_hundred_features_are_accepted():
    parse_spec(spec([box(("1 mm", "1 mm", "1 mm"))] * 200))


def test_a_hole_depth_can_be_a_length_or_thru():
    blind = parse_spec(spec([box(("9 mm", "9 mm", "9 mm")), hole([("1 mm", "1 mm", "9 mm")], "2 mm", depth="3 mm")]))
    assert blind.features[1].args["depth"] == 3.0
    through = parse_spec(spec([box(("9 mm", "9 mm", "9 mm")), hole([("1 mm", "1 mm", "9 mm")], "2 mm")]))
    assert through.features[1].args["depth"] is None


def test_counterbore_countersink_thread_and_fit_are_accepted():
    base = box(("40 mm", "40 mm", "20 mm"))
    parsed = parse_spec(
        spec(
            [
                base,
                hole([("10 mm", "10 mm", "20 mm")], "6.6 mm", counterbore={"diameter": "11 mm", "depth": "6.5 mm"}, thread="M6x1-6H", fit="H7"),
                hole([("30 mm", "30 mm", "20 mm")], "6 mm", countersink={"diameter": "12 mm", "angle": "90 deg"}),
            ]
        )
    )
    first, second = parsed.features[1].args, parsed.features[2].args
    assert first["counterbore"] == (11.0, 6.5) and first["thread"] == "M6x1-6H" and first["fit"] == "H7"
    assert second["countersink"][0] == 12.0 and second["countersink"][1] == pytest.approx(math.pi / 2, abs=1e-12)


def test_a_sphere_and_a_cone_parse_with_their_defaults():
    parsed = parse_spec(spec([sphere("3 cm"), cone("40 mm", "0 mm", "1 inch", mode="cut")]))
    first, second = parsed.features[0].args, parsed.features[1].args
    assert first == {"diameter": 30.0, "at": (0.0, 0.0, 0.0), "mode": "add"}
    assert second == {"diameter": 40.0, "top_diameter": 0.0, "height": 25.4, "at": (0.0, 0.0, 0.0), "axis": "+z", "mode": "cut"}
    assert [f.name for f in parsed.features] == ["sphere1", "cone2"]


def test_a_cone_top_diameter_may_be_zero_or_larger_than_the_bottom():
    assert parse_spec(spec([cone("10 mm", "0 mm", "5 mm")])).features[0].args["top_diameter"] == 0.0
    assert parse_spec(spec([cone("10 mm", "20 mm", "5 mm")])).features[0].args["top_diameter"] == 20.0


def test_sphere_and_cone_canonical_forms_are_valid_specs():
    first = parse_spec(spec([sphere("30 mm"), cone("40 mm", "0 mm", "10 mm", axis="-x")]))
    assert parse_spec(first.canonical).canonical == first.canonical


# --------------------------------------------------------------------------- errors carry a pointer and the allowed names


def test_an_unknown_op_names_the_pointer_and_the_allowed_ops():
    error = rejected(spec([{"op": "torus", "size": ["1 mm"]}]))
    assert error.details["pointer"] == "/features/0/op"
    assert set(error.details["allowed"]) == set(OPS)
    assert "torus" in error.message


def test_an_unknown_field_names_the_pointer_and_the_fields_of_that_op():
    error = rejected(spec([{"op": "box", "sise": ["1 mm", "1 mm", "1 mm"]}]))
    assert error.details["pointer"] == "/features/0/sise"
    assert "size" in error.details["allowed"] and "at" in error.details["allowed"] and "mode" in error.details["allowed"]
    assert set(error.details["allowed"]) == set(OPS["box"].fields) | {"op", "name"}


def test_a_missing_required_field_is_reported_at_its_pointer():
    error = rejected(spec([{"op": "box"}]))
    assert error.details["pointer"] == "/features/0/size"
    assert "required" in error.message


def test_a_bare_number_is_a_unit_error_with_a_pointer():
    error = rejected(spec([box((100, "50 mm", "20 mm"))]), "units.bare_number")
    assert error.details["pointer"] == "/features/0/size/0"
    error = rejected(spec([box(("100", "50 mm", "20 mm"))]), "units.bare_number")
    assert error.details["pointer"] == "/features/0/size/0"


def test_a_mass_where_a_length_is_needed_is_a_dimension_error_with_a_pointer():
    error = rejected(spec([box(("100 mm", "50 kg", "20 mm"))]), "units.dimension_mismatch")
    assert error.details["pointer"] == "/features/0/size/1"


def test_a_length_where_an_angle_is_needed_is_a_dimension_error():
    text = spec([{"op": "revolve", "profile": {"rect": {"size": ["1 mm", "1 mm"]}}, "angle": "10 mm"}])
    assert rejected(text, "units.dimension_mismatch").details["pointer"] == "/features/0/angle"


def test_an_undefined_parameter_is_reported_with_the_defined_names():
    error = rejected(spec([box(("$nope", "1 mm", "1 mm"))], L="1 mm"))
    assert error.details["pointer"] == "/features/0/size/0"
    assert "nope" in error.message and "L" in error.message


def test_a_forward_reference_is_rejected():
    error = rejected(spec([box(("1 mm", "1 mm", "1 mm"))], a="$b", b="1 mm"))
    assert error.details["pointer"] == "/params/a"
    assert "defined later" in error.message


@pytest.mark.parametrize("params", [{"a": "$a + 1 mm"}, {"a": "$b", "b": "$a"}])
def test_a_self_reference_and_a_cycle_are_rejected(params):
    error = rejected(spec([box(("1 mm", "1 mm", "1 mm"))], **params))
    assert error.details["pointer"] == "/params/a"


def test_a_parameter_must_be_a_length_or_an_angle():
    assert rejected(spec([box(("1 mm", "1 mm", "1 mm"))], m="5 kg"), "input.invalid").details["pointer"] == "/params/m"


def test_a_parameter_name_must_be_an_identifier():
    assert rejected(spec([box(("1 mm", "1 mm", "1 mm"))], **{"bad name": "1 mm"})).details["pointer"] == "/params/bad name"


def test_the_first_feature_must_be_a_body():
    error = rejected(spec([hole([("1 mm", "1 mm", "1 mm")], "1 mm")]))
    assert error.details["pointer"] == "/features/0/op"
    assert set(error.details["allowed"]) == BODIES


def test_the_first_feature_must_be_in_mode_add():
    error = rejected(spec([box(("1 mm", "1 mm", "1 mm"), mode="cut")]))
    assert error.details["pointer"] == "/features/0/mode"


def test_more_than_two_hundred_features_are_rejected():
    error = rejected(spec([box(("1 mm", "1 mm", "1 mm"))] * 201))
    assert error.details["pointer"] == "/features" and error.details["limit"] == 200


def test_a_spec_over_64_kilobytes_is_rejected():
    error = rejected(spec([box(("1 mm", "1 mm", "1 mm"))], pad="1 mm" + " " * 70000))
    assert error.details["limit_bytes"] == 65536


def test_invalid_json_is_input_invalid():
    error = rejected('{"features": [')
    assert error.details["pointer"] == ""


@pytest.mark.parametrize("text", ["[]", '"box"', "3", "null", "{}", '{"features": []}', '{"features": {}}', '{"features": [3]}'])
def test_a_spec_must_be_an_object_with_a_feature_list(text):
    rejected(text)


def test_an_unknown_top_level_key_is_rejected():
    error = rejected(json.dumps({"features": [box(("1 mm", "1 mm", "1 mm"))], "units": "mm"}))
    assert error.details["pointer"] == "/units" and set(error.details["allowed"]) == {"params", "features"}


def test_deeply_nested_json_is_input_invalid_not_a_crash():
    rejected("[" * 60000)


@pytest.mark.parametrize(
    ("feature", "pointer"),
    [
        (box(("1 mm", "1 mm", "1 mm"), mode="subtract"), "/features/0/mode"),
        (box(("1 mm", "1 mm"),), "/features/0/size"),
        (box(("1 mm", "1 mm", "0 mm")), "/features/0/size/2"),
        (box(("1 mm", "1 mm", "-1 mm")), "/features/0/size/2"),
        (box("1 mm"), "/features/0/size"),
        ({**box(("1 mm", "1 mm", "1 mm")), "name": "bad name!"}, "/features/0/name"),
        ({**box(("1 mm", "1 mm", "1 mm")), "name": ""}, "/features/0/name"),
        ({**box(("1 mm", "1 mm", "1 mm")), "name": 3}, "/features/0/name"),
        ({**box(("1 mm", "1 mm", "1 mm")), "size": {"value": 1, "unit": "mm"}}, "/features/0/size"),
        (box(("{\"value\": 1, \"unit\": \"mm\"}", "1 mm", "1 mm")), "/features/0/size/0"),
        (cylinder("0 mm", "1 mm"), "/features/0/diameter"),
        (sphere("0 mm"), "/features/0/diameter"),
        (sphere("-1 mm"), "/features/0/diameter"),
        ({"op": "sphere"}, "/features/0/diameter"),
        ({**sphere("1 mm"), "radius": "1 mm"}, "/features/0/radius"),
        (cone("0 mm", "0 mm", "1 mm"), "/features/0/diameter"),
        (cone("2 mm", "-1 mm", "1 mm"), "/features/0/top_diameter"),
        (cone("2 mm", "1 mm", "0 mm"), "/features/0/height"),
        (cone("2 mm", "1 mm", "-1 mm"), "/features/0/height"),
        ({"op": "cone", "diameter": "2 mm", "top_diameter": "1 mm"}, "/features/0/height"),
        ({"op": "cone", "diameter": "2 mm", "height": "1 mm"}, "/features/0/top_diameter"),
        (cone("2 mm", "1 mm", "1 mm", axis="z"), "/features/0/axis"),
        (cone("2 mm", "1 kg", "1 mm"), "/features/0/top_diameter"),
        (cylinder("1 mm", "1 mm", axis="w"), "/features/0/axis"),
        (cylinder("1 mm", "1 mm", axis="0,0,0"), "/features/0/axis"),
        (cylinder("1 mm", "1 mm", axis="z"), "/features/0/axis"),
        ({"op": "extrude", "plane": "ab", "profile": {"circle": {"diameter": "1 mm"}}, "distance": "1 mm"}, "/features/0/plane"),
        ({"op": "extrude", "profile": {"circle": {"diameter": "1 mm"}}, "distance": "0 mm"}, "/features/0/distance"),
        ({"op": "extrude", "profile": {"circle": {"diameter": "1 mm"}, "rect": {"size": ["1 mm", "1 mm"]}}, "distance": "1 mm"}, "/features/0/profile"),
        ({"op": "extrude", "profile": {"ellipse": {}}, "distance": "1 mm"}, "/features/0/profile/ellipse"),
        ({"op": "extrude", "profile": {}, "distance": "1 mm"}, "/features/0/profile"),
        ({"op": "extrude", "profile": "circle", "distance": "1 mm"}, "/features/0/profile"),
        ({"op": "extrude", "profile": {"rect": {}}, "distance": "1 mm"}, "/features/0/profile/rect/size"),
        ({"op": "extrude", "profile": {"rect": {"size": ["1 mm", "1 mm"], "centred": True}}, "distance": "1 mm"}, "/features/0/profile/rect/centred"),
        ({"op": "extrude", "profile": {"rect": {"size": ["1 mm", "1 mm"], "centered": "yes"}}, "distance": "1 mm"}, "/features/0/profile/rect/centered"),
        ({"op": "extrude", "profile": {"circle": {"diameter": "-1 mm"}}, "distance": "1 mm"}, "/features/0/profile/circle/diameter"),
        ({"op": "extrude", "profile": {"polygon": {"points": [["0 mm", "0 mm"], ["1 mm", "0 mm"]]}}, "distance": "1 mm"}, "/features/0/profile/polygon/points"),
        ({"op": "extrude", "profile": {"polygon": {"points": [["0 mm", "0 mm"], ["1 mm", "0 mm"], ["1 mm"]]}}, "distance": "1 mm"}, "/features/0/profile/polygon/points/2"),
        ({"op": "extrude", "profile": {"slot": {"length": "5 mm", "width": "5 mm"}}, "distance": "1 mm"}, "/features/0/profile/slot/length"),
        ({"op": "revolve", "profile": {"rect": {"size": ["1 mm", "1 mm"]}}, "axis": "w"}, "/features/0/axis"),
        ({"op": "revolve", "profile": {"rect": {"size": ["1 mm", "1 mm"]}}, "angle": "0 deg"}, "/features/0/angle"),
        ({"op": "revolve", "profile": {"rect": {"size": ["1 mm", "1 mm"]}}, "angle": "361 deg"}, "/features/0/angle"),
    ],
)
def test_body_field_errors_carry_the_pointer_and_the_allowed_names(feature, pointer):
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([feature]))
    error = info.value
    assert error.family in ("input.invalid", "units.bare_number", "units.dimension_mismatch", "units.parse_error"), error
    assert error.details["pointer"] == pointer, error.message
    assert isinstance(error.details["allowed"], list)
    assert error.message.startswith(pointer)


def hole_spec(**fields) -> str:
    entry = {"at": [["1 mm", "1 mm", "9 mm"]], "diameter": "2 mm"}
    return spec([box(("9 mm", "9 mm", "9 mm")), {"op": "hole", **{**entry, **fields}}])


@pytest.mark.parametrize(
    ("fields", "pointer"),
    [
        ({"axis": "w"}, "/features/1/axis"),
        ({"axis": "0 0 0"}, "/features/1/axis"),
        ({"depth": "deep"}, "/features/1/depth"),
        ({"depth": "0 mm"}, "/features/1/depth"),
        ({"depth": 5}, "/features/1/depth"),
        ({"diameter": "0 mm"}, "/features/1/diameter"),
        ({"at": []}, "/features/1/at"),
        ({"at": [["1 mm", "1 mm"]]}, "/features/1/at/0"),
        ({"at": [["1 mm", "1 mm", "9"]]}, "/features/1/at/0/2"),
        ({"at": "1 mm"}, "/features/1/at"),
        ({"at": [["1 mm", "1 mm", "9 mm"], ["1 mm", "1 mm", "9 mm"]]}, "/features/1/at/1"),
        ({"counterbore": {"diameter": "2 mm", "depth": "1 mm"}}, "/features/1/counterbore/diameter"),
        ({"counterbore": {"diameter": "5 mm"}}, "/features/1/counterbore/depth"),
        ({"counterbore": {"diameter": "5 mm", "depth": "4 mm"}, "depth": "3 mm"}, "/features/1/counterbore/depth"),
        ({"countersink": {"diameter": "2 mm", "angle": "90 deg"}}, "/features/1/countersink/diameter"),
        ({"countersink": {"diameter": "5 mm", "angle": "180 deg"}}, "/features/1/countersink/angle"),
        ({"countersink": {"diameter": "5 mm", "angle": "90 deg"}, "counterbore": {"diameter": "5 mm", "depth": "1 mm"}}, "/features/1/countersink"),
        ({"thread": 6}, "/features/1/thread"),
        ({"fit": "H7" * 40}, "/features/1/fit"),
        ({"bolt": "M6"}, "/features/1/bolt"),
    ],
)
def test_hole_errors_carry_the_pointer(fields, pointer):
    with pytest.raises(ToolFailure) as info:
        parse_spec(hole_spec(**fields))
    assert info.value.details["pointer"] == pointer, info.value.message
    assert set(info.value.details["allowed"]) >= {"op", "name"} or isinstance(info.value.details["allowed"], list)


def test_more_than_a_thousand_hole_positions_are_rejected():
    many = [[f"{i} mm", "1 mm", "9 mm"] for i in range(1001)]
    error = rejected(hole_spec(at=many))
    assert error.details["pointer"] == "/features/1/at" and error.details["limit"] == 1000
    parse_spec(hole_spec(at=many[:1000]))


def test_the_hole_limit_counts_every_hole_in_the_spec():
    many = [[f"{i} mm", "1 mm", "9 mm"] for i in range(600)]
    text = spec([box(("9 mm", "9 mm", "9 mm")), {**hole(many[:600], "2 mm")}, {**hole(many[:500], "1 mm")}])
    assert rejected(text).details["pointer"] == "/features/2/at"


@pytest.mark.parametrize(
    ("feature", "pointer"),
    [
        (fillet("|z & convex", "0 mm"), "/features/1/radius"),
        (fillet("|z & convex", "-1 mm"), "/features/1/radius"),
        (fillet("|w", "1 mm"), "/features/1/edges"),
        (fillet("", "1 mm"), "/features/1/edges"),
        (fillet(3, "1 mm"), "/features/1/edges"),
        (chamfer("curvy", "1 mm"), "/features/1/edges"),
        (chamfer("all", "0 mm"), "/features/1/distance"),
        ({"op": "fillet", "radius": "1 mm"}, "/features/1/edges"),
        ({"op": "fillet", "edges": "all"}, "/features/1/radius"),
        ({"op": "chamfer", "edges": "all", "radius": "1 mm"}, "/features/1/radius"),
    ],
)
def test_modifier_errors_carry_the_pointer(feature, pointer):
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([box(("9 mm", "9 mm", "9 mm")), feature]))
    assert info.value.details["pointer"] == pointer, info.value.message


def test_a_bad_selector_term_lists_the_allowed_terms():
    with pytest.raises(ToolFailure) as info:
        parse_spec(spec([box(("9 mm", "9 mm", "9 mm")), fillet("|w & convex", "1 mm")]))
    assert info.value.details["pointer"] == "/features/1/edges"
    assert set(info.value.details["allowed"]) == set(brep.EDGE_TERMS) | {"of:<feature name>"}


# --------------------------------------------------------------------------- edge selectors


@pytest.mark.parametrize(
    ("text", "terms"),
    [
        ("all", ("all",)),
        ("|z & convex", ("|z", "convex")),
        ("  >z&circle ", (">z", "circle")),
        ("|x|y", None),
        ("<x & >y & concave & line", ("<x", ">y", "concave", "line")),
    ],
)
def test_edge_selectors_parse(text, terms):
    if terms is None:
        with pytest.raises(ToolFailure):
            brep.parse_edge_selector(text)
        return
    selector = brep.parse_edge_selector(text)
    assert selector.terms == terms and selector.text == text


@pytest.mark.parametrize("text", ["", " ", "&", "|z &", "& all", "|w", ">q", "> z", "ALL", "convex convex ", "all & ", "z", "|", "line,circle"])
def test_bad_edge_selectors_are_input_invalid_and_list_the_terms(text):
    with pytest.raises(ToolFailure) as info:
        brep.parse_edge_selector(text)
    assert info.value.family == "input.invalid"
    assert info.value.details["selector"] == text
    assert set(info.value.details["allowed"]) == set(brep.EDGE_TERMS) | {"of:<feature name>"}


def test_the_edge_terms_are_the_documented_set():
    assert set(brep.EDGE_TERMS) == {"all", "|x", "|y", "|z", ">x", "<x", ">y", "<y", ">z", "<z", "convex", "concave", "line", "circle"}


# --------------------------------------------------------------------------- the expression guard

HOSTILE = [
    "9**9**9**9 mm",
    "9**9**9 mm",
    "10**1000000 mm",
    "10**100000 mm",
    "9**9 mm",
    "2 ** 2 ** 2 ** 2 mm",
    "2 ^ 2 ^ 2 mm",
    "10 mm ** 7",
    "10 ^ 1000 mm",
    "2 ** (3) mm",
    "2 ** $a mm",
    "1e10 mm",
    "10000000000 mm",
    "1e400 mm",
    "1" + "0" * 400 + " mm",
    "1 mm * 1e9 * 1.0000001e9",
    "(" * 9 + "1 mm" + ")" * 9,
    "(" * 1000 + "1 mm" + ")" * 1000,
    "1 mm + " * 40 + "1 mm",
]


@pytest.mark.parametrize("expression", HOSTILE)
def test_the_guard_rejects_hostile_expressions(expression):
    with pytest.raises(ToolFailure) as info:
        check_expression(expression, "/params/x")
    assert info.value.family == "input.invalid"
    assert info.value.details["pointer"] == "/params/x"


@pytest.mark.parametrize("expression", HOSTILE)
def test_a_spec_with_a_hostile_expression_is_input_invalid(expression):
    rejected(spec([box((expression, "1 mm", "1 mm"))], a="1 mm"))
    rejected(spec([box(("1 mm", "1 mm", "1 mm"))], x=expression))


def test_the_guard_runs_before_pint_so_hostile_input_is_rejected_quickly():
    # Without the guard "9**9**9 mm" computes a number of 369 million digits and never returns; a child
    # process with a timeout keeps this test from hanging when the guard is broken.
    script = (
        "import json, sys, time\n"
        "from eng_toolkit.domains.geometry.part import parse_spec\n"
        "from eng_toolkit.errors import ToolFailure\n"
        "out = []\n"
        "for expression in json.loads(sys.argv[1]):\n"
        "    text = json.dumps({'features': [{'op': 'box', 'size': [expression, '1 mm', '1 mm']}]})\n"
        "    start = time.perf_counter()\n"
        "    try:\n"
        "        parse_spec(text)\n"
        "        out.append(['accepted', time.perf_counter() - start])\n"
        "    except ToolFailure as exc:\n"
        "        out.append([exc.family, time.perf_counter() - start])\n"
        "print(json.dumps(out))\n"
    )
    done = subprocess.run([sys.executable, "-c", script, json.dumps(HOSTILE)], capture_output=True, text=True, timeout=10, check=False)
    assert done.returncode == 0, done.stderr
    outcomes = json.loads(done.stdout)
    assert [family for family, _ in outcomes] == ["input.invalid"] * len(HOSTILE)
    assert max(seconds for _, seconds in outcomes) < 1.0


@pytest.mark.parametrize(
    ("expression", "millimetres"),
    [
        ("(100 mm) / 4 + 2 mm", 27.0),
        ("2 * (10 mm + 5 mm)", 30.0),
        ("10 mm", 10.0),
        ("0.5 inch", 12.7),
        ("1e3 um", 1.0),
        ("2 ** 3 * 1 mm", 8.0),
        ("(((((((1 mm)))))))", 1.0),
        ("(10 cm ** 2) / (1 cm)", 100.0),
        ("1 m", 1000.0),
        ("999999 mm", 999999.0),
        ("10 mm * 2 ** -1", 5.0),
    ],
)
def test_ordinary_expressions_still_work(expression, millimetres):
    assert parse_spec(sized(expression, "1 mm", "1 mm")).features[0].args["size"][0] == pytest.approx(millimetres, rel=1e-12)
    assert check_expression(expression, "/p") is None


def test_the_expression_limit_is_two_hundred_characters():
    ok = "1 mm" + " + 1 mm" * 27
    assert len(ok) <= 200
    assert check_expression(ok, "/p") is None
    with pytest.raises(ToolFailure):
        check_expression(ok + " + 1 mm" * 20, "/p")


def test_the_guard_does_not_change_how_the_unit_gatekeeper_parses_quantities():
    assert parse_quantity("2 ** 3 mm", name="x", dimension="length").to("mm").magnitude == 8
    assert parse_quantity("1e10 mm", name="x", dimension="length").to("mm").magnitude == 1e10
    assert parse_quantity("(" * 12 + "1 mm" + ")" * 12, name="x", dimension="length").to("mm").magnitude == 1


# --------------------------------------------------------------------------- OPS, the generated description and the docs


def description() -> str:
    from eng_toolkit.domains.geometry.tools import MAKE_PART_DESCRIPTION

    return MAKE_PART_DESCRIPTION


def test_the_registry_has_the_thirteen_ops_with_roles():
    assert set(OPS) == {"box", "cylinder", "sphere", "cone", "extrude", "import", "revolve", "hole", "pattern", "mirror", "shell", "fillet", "chamfer"}
    assert {name for name, op in OPS.items() if op.role == "body"} == BODIES
    assert {name for name, op in OPS.items() if op.role == "modifier"} == {"hole", "pattern", "mirror", "shell", "fillet", "chamfer"}
    assert all(op.summary for op in OPS.values())


def test_every_op_in_the_description_is_in_the_registry_and_every_op_is_described():
    listed = re.findall(r"^- (\w+) \(", description(), re.M)
    assert sorted(listed) == sorted(OPS), listed


def test_every_field_of_every_op_is_in_the_description_line_of_that_op():
    lines = {m.group(1): m.group(0) for m in re.finditer(r"^- (\w+) \(.*$", description(), re.M)}
    for name, op in OPS.items():
        for field in op.fields:
            assert re.search(rf"\b{field}\b", lines[name]), (name, field)


def test_the_description_is_generated_from_the_registry(monkeypatch):
    assert part.grammar_text() in description()
    extra = part.OpDef(name="spline", role="body", fields={}, summary="made-up op")
    monkeypatch.setitem(part.OPS, "spline", extra)
    assert "- spline (" in part.grammar_text()


def test_the_description_is_compact_and_carries_one_example_that_parses():
    text = description()
    assert len(text) < DESCRIPTION_LIMIT, len(text)
    assert text.count('{"params"') == 1
    example = json.dumps(part.EXAMPLE_SPEC, separators=(",", ":"))
    assert example in text
    parsed = parse_spec(example)
    assert [f.op for f in parsed.features] == ["box", "hole", "fillet"]
    assert "input.invalid" in text and "details.pointer" in text


def test_the_description_names_the_extra_the_tool_needs_and_the_unit_rule():
    text = description()
    assert "uv sync --extra cad" in text and "bare numbers are rejected" in text


def test_the_readme_describes_every_op_and_every_field():
    readme = README.read_text(encoding="utf-8")
    start = readme.index("## `geometry_make_part`")
    end = readme.index("\n## ", start + 5) if "\n## " in readme[start + 5 :] else len(readme)
    section = readme[start:end]
    for name, op in OPS.items():
        assert f"`{name}`" in section, name
        for field in op.fields:
            assert f"`{field}`" in section, (name, field)
    for term in brep.EDGE_TERMS:
        assert f"`{term}`" in section, term
    assert "spec_sha256" in section and "features.json" in section and "geometry.empty_selection" in section


def test_the_example_in_the_description_is_the_probe_part_and_is_in_the_readme():
    assert [f["op"] for f in PROBE_FEATURES] == [f["op"] for f in part.EXAMPLE_SPEC["features"]]
    assert json.dumps(part.EXAMPLE_SPEC, separators=(",", ":")) in README.read_text(encoding="utf-8")
