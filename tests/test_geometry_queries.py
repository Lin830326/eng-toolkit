"""The geometry_measure query grammar.  Pure text parsing: runs without any engine."""

import ast
from pathlib import Path

import pytest

from eng_toolkit.domains.geometry import queries
from eng_toolkit.domains.geometry.queries import FaceId, HoleId, PointText
from eng_toolkit.errors import ToolFailure


def parse_one(text: str):
    (query,) = queries.parse_queries(text)
    return query


@pytest.mark.parametrize("text", ["", "   ", " ; ;; "])
def test_no_queries_is_an_empty_list(text):
    assert queries.parse_queries(text) == []


@pytest.mark.parametrize(
    ("text", "verb", "refs", "axis"),
    [
        ("distance F1 H2", "distance", (FaceId(1), HoleId(2)), None),
        ("angle F3 F4", "angle", (FaceId(3), FaceId(4)), None),
        ("diameter H1", "diameter", (HoleId(1),), None),
        ("radius F12", "radius", (FaceId(12),), None),
        ("area H1", "area", (HoleId(1),), None),
        ("distance P(10 mm, 0 mm, 5 mm) F2", "distance", (PointText("10 mm", "0 mm", "5 mm"), FaceId(2)), None),
        ("thickness P(10 mm, 10 mm, 30 mm) -z", "thickness", (PointText("10 mm", "10 mm", "30 mm"),), "-z"),
        ("thickness P(1 inch,2 inch,3 inch) +x", "thickness", (PointText("1 inch", "2 inch", "3 inch"),), "+x"),
    ],
)
def test_each_verb_parses_to_its_references(text, verb, refs, axis):
    query = parse_one(text)
    assert (query.verb, query.refs, query.axis) == (verb, refs, axis)
    assert query.index == 0
    assert query.text == text


def test_verbs_and_entity_letters_are_case_insensitive_and_whitespace_is_free():
    query = parse_one("  DISTANCE   f1   h2  ")
    assert (query.verb, query.refs) == ("distance", (FaceId(1), HoleId(2)))
    assert parse_one("Thickness p(1 mm, 2 mm, 3 mm) -Z").axis == "-z"


def test_queries_are_separated_by_semicolons_in_order_and_keep_their_index():
    found = queries.parse_queries("diameter H1; distance F1 H1 ;; area F2;")
    assert [(q.index, q.verb) for q in found] == [(0, "diameter"), (1, "distance"), (2, "area")]


def test_a_point_keeps_its_text_so_the_unit_gatekeeper_can_judge_it():
    assert parse_one("thickness P(10, 10, 30) -z").refs == (PointText("10", "10", "30"),)


def test_exactly_twenty_queries_are_allowed_and_twenty_one_are_not():
    assert len(queries.parse_queries("; ".join(["area F1"] * queries.MAX_QUERIES))) == 20
    with pytest.raises(ToolFailure) as caught:
        queries.parse_queries("; ".join(["area F1"] * 21))
    assert caught.value.family == "input.invalid"
    assert caught.value.details["limit"] == 20 and caught.value.details["received"] == 21


@pytest.mark.parametrize(
    "bad",
    [
        "distanc F1 H1",
        "distance F1",
        "distance F1 H1 F2",
        "diameter",
        "diameter H1 H2",
        "area F",
        "area F1x",
        "area 1",
        "area G1",
        "angle F1",
        "thickness F1 -z",
        "thickness P(1 mm, 2 mm) -z",
        "thickness P(1 mm, 2 mm, 3 mm, 4 mm) -z",
        "thickness P(1 mm, 2 mm, 3 mm)",
        "thickness P(1 mm, 2 mm, 3 mm) z",
        "thickness P(1 mm, 2 mm, 3 mm) +w",
        "thickness P(1 mm, 2 mm, 3 mm) -z extra",
        "thickness P(1 mm, 2 mm, 3 mm -z",
        "distance P(1 mm, 2 mm, 3 mm F1",
        "F1",
    ],
)
def test_a_malformed_query_is_input_invalid_with_its_index_and_text(bad):
    with pytest.raises(ToolFailure) as caught:
        queries.parse_queries(f"area F1; {bad}")
    failure = caught.value
    assert failure.family == "input.invalid"
    assert failure.details["query_index"] == 1
    assert failure.details["query"] == bad
    assert failure.details["param"] == "queries"
    assert "q2" in failure.message


def test_a_bad_axis_lists_the_allowed_ones():
    with pytest.raises(ToolFailure) as caught:
        queries.parse_queries("thickness P(1 mm, 2 mm, 3 mm) +w")
    assert caught.value.details["allowed_axes"] == ["+x", "-x", "+y", "-y", "+z", "-z"]


def test_the_grammar_module_imports_neither_ocp_nor_pint():
    source = Path(queries.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0] if node.level == 0 else node.module)
            if node.module.startswith("eng_toolkit"):
                imported.add(node.module)
    assert imported.isdisjoint({"OCP", "pint", "open3d", "numpy", "eng_toolkit.units", "eng_toolkit.brep"})
