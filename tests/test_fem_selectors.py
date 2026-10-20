"""The selector language of the fem domain: what parses, what is refused, and what a plane selects.  No program is started.

The test body for plane selection is one 10 mm right-angle tetrahedron (corners (0,0,0), (10,0,0), (0,10,0), (0,0,10)):
    facet z = 0 (the base)  : outward normal -z, area 10 * 10 / 2 = 50 mm^2
    facet x = 0, y = 0      : normals -x, -y, area 50 each
    slanted facet           : area |(-10,10,0) x (-10,0,10)| / 2 = |(100,100,100)| / 2 = 86.6025 mm^2
"""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fem import mesh as M
from eng_toolkit.domains.fem import selectors as S
from eng_toolkit.errors import ToolFailure
from eng_toolkit.units import UnitsError


def one_tet(scale=10.0) -> M.TetMesh:
    corners = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float) * scale
    mids = [(corners[a] + corners[b]) / 2 for a, b in M._MID_EDGES]
    return M.TetMesh(nodes=np.vstack([corners, mids]), tets=np.arange(10, dtype=np.int64).reshape(1, 10), size_mm=scale)


def select(text, mesh=None):
    mesh = mesh or one_tet()
    facets = M.boundary_facets(mesh)
    plane = S.parse_plane(text, "test")
    return S.select_facets(plane, mesh.nodes, facets.corners, facets.mids, facets.normals, mesh.diagonal_mm)


# --------------------------------------------------------------------------- what parses


@pytest.mark.parametrize(
    "text, axis, offset_mm, sign",
    [
        ("x = 0 mm", 0, 0.0, 0),
        ("Z=20mm", 2, 20.0, 0),
        ("y = 1.5 cm", 1, 15.0, 0),
        ("x = 3.937007874015748 inch", 0, 100.0, 0),  # 100 mm / 25.4
        ("normal +z at z = 20 mm", 2, 20.0, 1),
        ("normal -x at x = -5 mm", 0, -5.0, -1),
        ("normal  +  y  at  y = 1e1 mm", 1, 10.0, 1),
    ],
)
def test_plane_selectors_parse_to_millimetres(text, axis, offset_mm, sign):
    plane = S.parse_plane(text, "test")
    assert (plane.axis, plane.sign) == (axis, sign)
    assert plane.offset_mm == pytest.approx(offset_mm, rel=1e-12, abs=1e-12)


def test_supports_default_to_fixed_and_take_a_dof_list():
    parsed = S.parse_supports("x = 0 mm; y = 0 mm : ux, uz; z = 0 mm : uy uz; point(1 mm, 2 mm, 3 cm) : fixed; normal -z at z = 0 mm : uy")
    assert [p.dofs for p in parsed] == [(True,) * 3, (True, False, True), (False, True, True), (True,) * 3, (False, True, False)]
    assert isinstance(parsed[3].target, S.PointSelector)
    assert parsed[3].target.xyz_mm == pytest.approx((1.0, 2.0, 30.0))


def test_loads_parse_force_and_pressure_in_newtons_and_megapascals():
    force, pressure = S.parse_loads("force (0 N, 1 kN, -2 lbf) on x = 100 mm ; pressure 2 bar on normal +z at z = 5 mm")
    assert force.force_n == pytest.approx((0.0, 1000.0, -2 * 4.4482216152605))
    assert pressure.pressure_mpa == pytest.approx(0.2)  # 2 bar = 2e5 Pa = 0.2 MPa
    assert pressure.target.sign == 1 and pressure.target.axis == 2


# --------------------------------------------------------------------------- what is refused


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("x = 0 mm; rm -rf /", "not a face selector"),  # passes the character test, fails the grammar
        ("x = 0 mm | calc", "'|'"),
        ("x = 0 mm && calc", "'&'"),
        ("x = 0 mm\nSystemCall", "'\\n'"),
        ("x = 0 mm\r\n", "'\\r'"),
        ("x = `calc` mm", "'`'"),
        ("x = $(calc) mm", "'$'"),
        ("x = 0 mm > out.txt", "'>'"),
        ("x = 0 mm ' OR '1", "\"'\""),
        ('x = "0" mm', "'\"'"),
        ("x = 0 mm\\..\\..", "'\\\\'"),
        ("x = {0} mm", "'{'"),
        ("Include \"evil.geo\";", '"'),
        ("x = 0 mm # comment", "'#'"),
        ("x = 0 mm\x00", "'\\x00'"),
        ("x = ", "not a face selector"),
        ("w = 0 mm", "not a face selector"),
        ("normal +z at x = 0 mm", "normal's axis must be the axis"),
        ("normal z at z = 0 mm", "not a face selector"),
        ("x = 0", "bare number"),  # a length needs a unit
        ("x = 0 kg", "dimension"),
        ("x = 0 blorps", "parse"),
        ("point(1 mm, 2 mm)", "not a point"),
        ("x = 0 mm : ux : uy", "more than one"),
        ("x = 0 mm : up", "ux, uy, uz"),
        ("", "empty"),
        (";;", "empty"),
    ],
)
def test_hostile_or_malformed_supports_are_refused_with_the_clause_named(text, fragment):
    with pytest.raises(UnitsError) as caught:
        S.parse_supports(text)
    assert fragment in caught.value.message, caught.value.message
    assert caught.value.family.startswith(("input.", "units."))


@pytest.mark.parametrize(
    "text",
    [
        "force (1 N, 2 N) on x = 0 mm",
        "force (1 N, 2 N, 3 N) x = 0 mm",
        "force 1 N on x = 0 mm",
        "pressure on x = 0 mm",
        "pressure 1 N on x = 0 mm",  # a force is not a pressure
        "force (1 MPa, 0 N, 0 N) on x = 0 mm",
        "force (0 N, 0 N, 0 N) on x = 0 mm",  # zero
        "pressure 0 MPa on x = 0 mm",
        "force (1 N, 0 N, 0 N) on x = 0 mm; import os",
        "torque 3 N*m on x = 0 mm",
    ],
)
def test_malformed_loads_are_refused(text):
    with pytest.raises(UnitsError):
        S.parse_loads(text)


@pytest.mark.parametrize(
    "text",
    [
        "force (1e308 N, 0 N, 0 N) on x = 0 mm",
        "force (1e308 kN, 0 N, 0 N) on x = 0 mm",  # overflows to inf when converted to newtons
        "force (1e12 N, 1e12 N, 0 N) on x = 0 mm",  # each component fine, the vector is not
        "pressure 1e300 MPa on x = 0 mm",
        "pressure 1e308 GPa on x = 0 mm",
    ],
)
def test_absurd_load_magnitudes_are_out_of_range(text):
    with pytest.raises(UnitsError) as caught:
        S.parse_loads(text)
    assert caught.value.family == "input.out_of_range"


@pytest.mark.parametrize("text", ["x = 1e308 mm", "x = 1e30 m", "point(0 mm, 0 mm, 1e9 m)"])
def test_absurd_coordinates_are_out_of_range(text):
    with pytest.raises(UnitsError) as caught:
        S.parse_supports(text)
    assert caught.value.family == "input.out_of_range"


def test_too_many_clauses_and_too_long_text_are_refused():
    with pytest.raises(UnitsError):
        S.parse_supports("; ".join(["x = 0 mm"] * (S.MAX_CLAUSES + 1)))
    with pytest.raises(UnitsError):
        S.parse_supports("x = 0 mm" + " " * S.MAX_TEXT)


def test_the_character_whitelist_has_no_shell_or_script_metacharacter():
    allowed = {c for c in map(chr, range(0, 256)) if S._ALLOWED.fullmatch(c)}
    for dangerous in "$`|&<>\\\"'!?{}[]~#%@\n\r\t\x00":
        assert dangerous not in allowed, repr(dangerous)


# --------------------------------------------------------------------------- what a plane selects


def test_a_plane_selects_the_facets_lying_in_it():
    mesh = one_tet()
    facets = M.boundary_facets(mesh)
    assert len(facets.areas) == 4
    chosen = select("z = 0 mm")
    assert len(chosen) == 1
    assert facets.areas[chosen].sum() == pytest.approx(50.0, rel=1e-12)
    assert facets.normals[chosen][0] == pytest.approx((0.0, 0.0, -1.0))
    assert len(select("x = 0 mm")) == 1 and len(select("y = 0 mm")) == 1


def test_the_normal_sign_decides_which_side_of_the_material_is_meant():
    assert len(select("normal -z at z = 0 mm")) == 1  # the base faces down
    assert len(select("normal +z at z = 0 mm")) == 0  # nothing there faces up


def test_a_plane_through_the_air_or_across_a_facet_selects_nothing():
    assert len(select("z = 3 mm")) == 0
    assert len(select("z = 10 mm")) == 0  # the apex touches it, no facet lies in it


def test_the_tolerance_is_a_millionth_of_the_diagonal():
    diagonal = one_tet().diagonal_mm  # 10 * sqrt(3)... the box of the corners is 10 x 10 x 10
    assert diagonal == pytest.approx(10 * math.sqrt(3))
    inside = 0.5e-6 * diagonal
    outside = 2e-6 * diagonal
    assert len(select(f"z = {inside} mm")) == 1
    assert len(select(f"z = {outside} mm")) == 0


def test_an_empty_selection_error_names_the_selector():
    failure = S.empty_selection("x = 55 mm", "support", 100.0)
    assert isinstance(failure, ToolFailure) and failure.family == "fem.empty_selection"
    assert "'x = 55 mm'" in failure.message and "support" in failure.message
