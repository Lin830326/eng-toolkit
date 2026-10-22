"""Reference-load frame buckling against Hermite and Euler calculations."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d._model import compile_frame, parse_frame, solve_static
from eng_toolkit.domains.structures3d.buckling import _element_geometric, buckling
from tests.structures3d_testing import FRAME, call, close, ok, quantity, table, v


PINNED = {**FRAME, "supports": "a,ux+uy+uz+rx;b,uy+uz",
          "loads": "b,-1 kN,0 N,0 N,0 N*m,0 N*m,0 N*m"}


def test_constant_axial_geometric_matrix_matches_hand_coefficients():
    model = parse_frame(PINNED["nodes"], PINNED["members"], PINNED["supports"], PINNED["loads"])
    mesh = compile_frame(model)
    displacement, _, _ = solve_static(mesh)
    geometric, minimum, maximum = _element_geometric(mesh.elements[0], displacement)
    close(minimum, -1000)
    close(maximum, -1000)
    length = 2.
    reference = -1000 / (30*length) * np.array([
        [36, 3*length, -36, 3*length],
        [3*length, 4*length**2, -3*length, -length**2],
        [-36, -3*length, 36, -3*length],
        [3*length, -length**2, -3*length, 4*length**2],
    ])
    np.testing.assert_allclose(geometric[np.ix_([1, 5, 7, 11], [1, 5, 7, 11])], reference, rtol=2e-14, atol=1e-12)


def test_point_axial_load_splits_geometric_integration():
    model = parse_frame(FRAME["nodes"], FRAME["members"], FRAME["supports"],
                        member_loads="e,point,local,1 m,-1 kN,0 N,0 N")
    mesh = compile_frame(model)
    displacement, _, _ = solve_static(mesh)
    geometric, minimum, maximum = _element_geometric(mesh.elements[0], displacement)
    close(minimum, -1000)
    close(maximum, 0)
    # For the transverse displacement at the fixed end, N1'=6(t^2-t)/L.
    points, weights = np.polynomial.legendre.leggauss(5)
    x = (points + 1) / 2
    slope = 3 * ((x / 2)**2 - x / 2)
    expected = -1000 * np.dot(weights / 2, slope**2)
    close(geometric[1, 1], expected, rel=2e-14, abs=1e-12)


def test_pinned_column_converges_to_euler_load():
    results = [ok(buckling, **PINNED, elements_per_member=n, modes_count=1) for n in (1, 4, 10)]
    expected = math.pi**2 * 200e9 * 1e-6 / (1000 * 2**2)
    errors = [abs(v(result, "first_load_factor") - expected) for result in results]
    assert errors[2] < errors[1] < errors[0]
    close(v(results[-1], "first_load_factor"), expected, rel=2e-5)
    close(quantity(results[-1].data["pinned_column_reference"]["about_local_y"]), expected * 1000)
    close(results[-1].data["closed_form_comparison"]["first_euler_load_factor"], expected)
    assert max(results[-1].data["eigen_residuals"]) < 1e-8
    assert len(table(results[-1])) == 11


@pytest.mark.parametrize("supports,coefficient,subdivisions", [
    ("a,fixed", .25, (1, 4, 10)),
    ("a,fixed;b,uy+uz+rx+ry+rz", 4., (2, 4, 10)),
])
def test_cantilever_and_clamped_column_euler_convergence(supports, coefficient, subdivisions):
    column = {**PINNED, "supports": supports}
    results = [ok(buckling, **column, elements_per_member=n, modes_count=1) for n in subdivisions]
    expected = coefficient * math.pi**2 * 200e9 * 1e-6 / (1000 * 2**2)
    errors = [abs(v(result, "first_load_factor") - expected) for result in results]
    assert errors[2] < errors[1] < errors[0]
    close(v(results[-1], "first_load_factor"), expected, rel=3e-4)
    comparison = results[-1].data["closed_form_comparison"]
    close(comparison["first_euler_load_factor"], expected)
    assert comparison["relative_error"] < 3e-4
    assert comparison["elements_per_member"] == subdivisions[-1]


def test_load_scaling_and_tension_refusal():
    reference = ok(buckling, **PINNED, modes_count=1, elements_per_member=4)
    doubled = ok(buckling, **{**PINNED, "loads": "b,-2 kN,0 N,0 N,0 N*m,0 N*m,0 N*m"},
                 modes_count=1, elements_per_member=4)
    close(v(doubled, "first_load_factor"), v(reference, "first_load_factor") / 2, rel=1e-9)
    tension = call(buckling, **{**PINNED, "loads": "b,1 kN,0 N,0 N,0 N*m,0 N*m,0 N*m"})
    assert not tension.ok
    assert tension.error.details["param"] == "/loads"


def test_rotated_pure_bending_does_not_create_fictitious_compression():
    # (1,2,3) dot (2000,-1000,0)=0 exactly: this is transverse loading.
    result = call(buckling, **{**FRAME, "nodes": "a,0 m,0 m,0 m;b,1 m,2 m,3 m"},
                  loads="b,2000 N,-1000 N,0 N,0 N*m,0 N*m,0 N*m")
    assert not result.ok
    assert result.error.details["param"] == "/loads"
    assert result.error.family in ("input.invalid", "input.out_of_range")


@pytest.mark.parametrize("extra,param", [
    ({"shear_properties": "e,800 mm^2,800 mm^2"}, "/shear_properties"),
    ({"thermal": "e,12e-6 1/K,10 K"}, "/thermal"),
    ({"supports": "a,pinned"}, "/supports"),
])
def test_buckling_refusals(extra, param):
    result = call(buckling, **{**PINNED, **extra})
    assert not result.ok
    assert result.error.details["param"] == param
