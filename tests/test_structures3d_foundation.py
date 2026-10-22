"""Winkler checks against independent continuum solutions and equilibrium."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d.foundation import _element, foundation
from tests.structures3d_testing import call, close, ok, quantity, table, v


BEAM = dict(length="10 m", flexural_rigidity="2e7 N*m^2", foundation_stiffness="1e6 N/m^2")


def _nearest(rows, x, side="right"):
    return min((r for r in rows if r["side"] == side), key=lambda r: abs(float(r["x_m"])-x))


def _finite_exact(length, ei, k, load, points, left, right, locations):
    """Independent continuum transfer via exp(A*x), with force/moment jumps."""
    expm = pytest.importorskip("scipy.linalg").expm
    a = np.zeros((5, 5))
    a[0, 1], a[1, 2], a[2, 3], a[3, 0], a[3, 4] = 1, -1/ei, 1, k, -load
    boundary = {"free": (2, 3), "pinned": (0, 2), "fixed": (0, 1)}

    def transfer(initial, x):
        y = np.r_[initial, 1.]
        previous = 0.
        for location, p, c in sorted(points):
            if location > x:
                break
            y = expm(a*(location-previous))@y
            y[2] += c
            y[3] -= p
            previous = location
        return (expm(a*(x-previous))@y)[:4]

    free = [i for i in range(4) if i not in boundary[left]]
    baseline = transfer(np.zeros(4), length)
    columns = []
    for i in free:
        initial = np.zeros(4)
        initial[i] = 1
        columns.append(transfer(initial, length)-baseline)
    values = np.linalg.solve(np.array(columns).T[list(boundary[right])], -baseline[list(boundary[right])])
    initial = np.zeros(4)
    initial[free] = values
    return np.array([transfer(initial, x) for x in locations])


def test_element_matches_independent_four_point_gauss_integration():
    # Integrate the Hermite polynomials instead of repeating the coefficient matrix.
    h, ei, k, load = 2.7, 7.1e6, 4.3e5, 130.
    nodes, weights = np.polynomial.legendre.leggauss(4)
    expected, expected_force = np.zeros((4, 4)), np.zeros(4)
    for t, weight in zip((nodes+1)/2, weights*h/2):
        shape = np.array([1-3*t*t+2*t**3, h*(t-2*t*t+t**3), 3*t*t-2*t**3, h*(-t*t+t**3)])
        curvature = np.array([-6+12*t, h*(-4+6*t), 6-12*t, h*(-2+6*t)])/h**2
        expected += weight*(ei*np.outer(curvature, curvature)+k*np.outer(shape, shape))
        expected_force += weight*shape*load
    actual, force = _element(h, ei, k, load)
    np.testing.assert_allclose(actual, expected, rtol=2e-15, atol=1e-8)
    np.testing.assert_allclose(force, expected_force, rtol=2e-15)


@pytest.mark.parametrize("elements", [7, 40, 200])
def test_free_uniform_load_is_exact_rigid_translation_and_soil_balance(elements):
    r = ok(foundation, **BEAM, uniform_load="10 kN/m", elements=elements)
    close(v(r, "maximum_abs_deflection"), .01, rel=2e-11)
    close(v(r, "maximum_abs_soil_reaction"), 10000, rel=2e-11)
    close(quantity(r.data["soil_resultants"]["force"]), -100000, rel=2e-11)
    close(v(r, "maximum_abs_moment"), 0.)
    close(v(r, "maximum_abs_shear"), 0.)
    close(v(r, "maximum_abs_slope"), 0.)
    assert r.data["convergence"]["converged"]
    assert all(abs(quantity(x["force"])) < 1e-10 for x in r.data["end_reactions"])
    for row in table(r):
        close(float(row["deflection_m"]), .01, rel=2e-11)
        close(float(row["soil_reaction_N_per_m"]), -10000, rel=2e-11)


def test_pinned_uniform_load_against_independent_navier_series():
    r = ok(foundation, **BEAM, left_end="pinned", right_end="pinned", uniform_load="10 kN/m", elements=40)
    n = np.arange(1, 2000, 2)
    alpha = n*math.pi/10
    coefficient = 4*10000/(n*math.pi)/(2e7*alpha**4+1e6)
    w = float(np.sum(coefficient*np.sin(n*math.pi/2)))
    moment = float(np.sum(2e7*alpha**2*coefficient*np.sin(n*math.pi/2)))
    center = _nearest(table(r), 5)
    close(float(center["deflection_m"]), w, rel=2e-7)
    close(float(center["moment_N_m"]), moment, rel=3e-6)
    assert r.data["convergence"]["converged"]
    for key in ("force", "moment_about_left"):
        assert abs(quantity(r.data["equilibrium_residual"][key])) < 3e-5


def test_free_uniform_particular_solution_preserves_point_load_superposition():
    point = ok(foundation, **BEAM, point_loads="3 m,8 kN,2 kN*m", elements=25)
    combined = ok(foundation, **BEAM, uniform_load="10 kN/m", point_loads="3 m,8 kN,2 kN*m", elements=25)
    point_rows, combined_rows = table(point), table(combined)
    for x in (0., 2., 3., 5., 8.):
        a, b = _nearest(point_rows, x), _nearest(combined_rows, x)
        close(float(b["deflection_m"]), float(a["deflection_m"])+.01, rel=2e-11)
        close(float(b["soil_reaction_N_per_m"]), float(a["soil_reaction_N_per_m"])-10000, rel=2e-11)
        for key in ("slope_rad", "moment_N_m", "shear_N"):
            close(float(b[key]), float(a[key]), rel=2e-11)


@pytest.mark.parametrize("left,right", [("fixed", "free"), ("free", "fixed"), ("pinned", "fixed"), ("fixed", "fixed"), ("free", "pinned")])
def test_finite_mixed_ends_and_off_grid_point_loads_against_continuum(left, right):
    points = [(2.37, 17000., 2300.), (7.13, -6000., -1300.)]
    r = ok(foundation, **BEAM, left_end=left, right_end=right, uniform_load="2 kN/m",
           point_loads="2.37 m,17 kN,2300 N*m;7.13 m,-6 kN,-1300 N*m", elements=65)
    rows = [_nearest(table(r), x) for x in (1., 3., 5., 8., 9.)]
    locations = [float(row["x_m"]) for row in rows]
    expected = _finite_exact(10., 2e7, 1e6, 2000., points, left, right, locations)
    actual = np.array([[float(row[key]) for key in ("deflection_m", "slope_rad", "moment_N_m", "shear_N")] for row in rows])
    np.testing.assert_allclose(actual, expected, rtol=3e-4, atol=3e-7)
    assert r.data["requested_elements"] == 65
    assert 65 <= r.data["elements"] <= 200


def test_point_force_and_moment_one_sided_jumps_and_global_balance():
    r = ok(foundation, **BEAM, left_end="fixed", right_end="pinned", uniform_load="-1 kN/m",
           point_loads="3.141 m,15 kN,7 kN*m;3.141 m,-2 kN,1 kN*m;0 m,1 kN,1 kN*m;10 m,2 kN,3 kN*m", elements=32)
    rows = table(r)
    left = _nearest(rows, 3.141, "left")
    right = _nearest(rows, 3.141, "right")
    close(float(right["shear_N"])-float(left["shear_N"]), -13000, rel=2e-10)
    close(float(right["moment_N_m"])-float(left["moment_N_m"]), 8000, rel=2e-10)
    close(float(right["deflection_m"]), float(left["deflection_m"]), rel=1e-12)
    close(float(right["slope_rad"]), float(left["slope_rad"]), rel=1e-12)
    assert abs(quantity(r.data["equilibrium_residual"]["force"])) < 2e-5
    assert abs(quantity(r.data["equilibrium_residual"]["moment_about_left"])) < 2e-4


def test_long_beam_point_load_against_infinite_hetenyi_and_refinement():
    # beta=1/m, ends 10 characteristic lengths from a centered point load.
    inputs = dict(length="20 m", flexural_rigidity="250000 N*m^2", foundation_stiffness="1000000 N/m^2", point_loads="10 m,10000 N,0 N*m")
    coarse = ok(foundation, **inputs, elements=20)
    fine = ok(foundation, **inputs, elements=160)
    reference = 10000/(2*1e6)
    assert abs(v(fine, "maximum_abs_deflection")-reference) < abs(v(coarse, "maximum_abs_deflection")-reference)
    close(v(fine, "maximum_abs_deflection"), reference, rel=2e-6)
    rows = table(fine)
    for x in (6., 8., 9., 10., 11., 12., 14.):
        distance = abs(x-10)
        expected = reference*math.exp(-distance)*(math.cos(distance)+math.sin(distance))
        close(float(_nearest(rows, x)["deflection_m"]), expected, rel=4e-5, abs=2e-9)
    close(v(fine, "beta_length"), 20.)
    close(v(fine, "characteristic_length"), 1.)
    assert fine.data["length_classification"] == "long"
    assert fine.data["convergence"]["converged"]
    comparison = fine.data["infinite_comparison"]
    assert comparison["applicable"]
    close(quantity(comparison["reference_at_load"]), reference)
    close(quantity(comparison["finite_at_load"]), v(fine, "maximum_abs_deflection"))
    assert comparison["maximum_difference_relative_to_load"] < 3e-6
    assert len(comparison["samples"]) == 7
    assert len(table(fine, 1)) == 7
    assert comparison["maximum_difference_relative_to_load"] < coarse.data["infinite_comparison"]["maximum_difference_relative_to_load"]
    assert any("Hetenyi comparison is approximate" in warning for warning in fine.warnings)


def test_small_foundation_limit_matches_cantilever_end_force_and_moment():
    r = ok(foundation, length="2 m", flexural_rigidity="200000 N*m^2", foundation_stiffness="1e-3 N/m^2",
           left_end="fixed", point_loads="2 m,1000 N,200 N*m", elements=12)
    # w=P L^3/(3EI)+C L^2/(2EI); theta=P L^2/(2EI)+C L/EI.
    end = _nearest(table(r), 2., "left")
    close(float(end["deflection_m"]), 1000*8/(3*200000)+200*4/(2*200000), rel=3e-8)
    close(float(end["slope_rad"]), 1000*4/(2*200000)+200*2/200000, rel=3e-8)
    close(quantity(r.data["end_reactions"][0]["force"]), -1000, rel=3e-8)
    close(quantity(r.data["end_reactions"][0]["moment"]), -2200, rel=3e-8)
    assert r.data["length_classification"] == "short"


def test_linearity_sign_symmetry_and_imperial_conversion():
    positive = ok(foundation, **BEAM, left_end="pinned", right_end="fixed", uniform_load="3 kN/m", point_loads="4 m,8 kN,2 kN*m", elements=12)
    negative = ok(foundation, **BEAM, left_end="pinned", right_end="fixed", uniform_load="-6 kN/m", point_loads="4 m,-16 kN,-4 kN*m", elements=12, unit_system="imperial")
    for name, unit in (("deflection", "m"), ("moment", "N*m"), ("shear", "N"), ("soil_reaction", "N/m")):
        close(v(negative, "maximum_abs_"+name, unit), 2*v(positive, "maximum_abs_"+name), rel=2e-10)
    assert "inch" in negative.values["maximum_abs_deflection"].unit
    close(quantity(negative.data["beta"], "1/m"), quantity(positive.data["beta"], "1/m"))
    close(v(negative, "characteristic_length", "m"), v(positive, "characteristic_length"))
    assert any("tension" in warning for warning in positive.warnings)


def test_extrema_are_polynomial_results_and_table_contains_locations():
    r = ok(foundation, **BEAM, left_end="fixed", right_end="pinned", uniform_load="5 kN/m", elements=5, stations=2)
    rows = table(r)
    location = quantity(r.data["maximum_abs_deflection_location"]["x"])
    assert any(abs(float(row["x_m"])-location) < 1e-12 for row in rows)
    assert "FE polynomial" in r.data["maximum_abs_deflection_location"]["method"]
    assert "not analytical continuum" in r.data["validity"]
    assert not r.data["infinite_comparison"]["applicable"]
    assert any("Infinite-beam point-load comparison is unavailable" in warning for warning in r.warnings)


def test_zero_load_and_maximum_mesh_have_finite_results():
    r = ok(foundation, **BEAM, elements=200, left_end="fixed", right_end="fixed")
    close(v(r, "maximum_abs_deflection"), 0)
    assert r.data["convergence"]["comparison_elements"] == 100
    assert r.data["convergence"]["converged"]
    assert len(table(r)) < 1000


def test_coarse_mesh_refines_until_convergence_check_passes():
    r = ok(foundation, **BEAM, left_end="fixed", right_end="fixed", uniform_load="10 kN/m", elements=1)
    assert r.data["convergence"]["converged"]
    assert r.data["elements"] > 1


@pytest.mark.parametrize("length,classification", [("0.5 m", "short"), ("1 m", "medium"), ("4 m", "long")])
def test_beta_length_classification(length, classification):
    r = ok(foundation, length=length, flexural_rigidity="1 N*m^2", foundation_stiffness="4 N/m^2", elements=2)
    assert r.data["length_classification"] == classification
    assert any(warning.startswith(classification.capitalize()+" beam") for warning in r.warnings)


@pytest.mark.parametrize("updates", [
    {"length": 10}, {"length": "NaN m"}, {"flexural_rigidity": "2e7 N*m"},
    {"foundation_stiffness": "0 N/m^2"}, {"foundation_stiffness": "1 N/m^3"},
    {"foundation_model": "compression_only"}, {"left_end": "spring"},
    {"elements": 0}, {"elements": 201}, {"elements": True}, {"elements": 1.5},
    {"stations": 1}, {"convergence_tolerance": float("nan")}, {"convergence_tolerance": True},
    {"point_loads": "11 m,1 N,0 N*m"}, {"point_loads": "-1 m,1 N,0 N*m"},
    {"point_loads": "1 m,1 N"}, {"point_loads": "1 m,1 N,0 N*m;"},
    {"point_loads": "1,1 N,0 N*m"}, {"point_loads": "1 m,1 N,0 N*m;2 m,1 N,0 N*m", "elements": 2},
    {"point_loads": "1e-10 m,1 N,0 N*m"}, {"uniform_load": "1 kN"},
])
def test_strict_inputs_refuse_unsupported_or_malformed_cases(updates):
    assert not call(foundation, **{**BEAM, **updates}).ok


def test_extreme_foundation_contrast_returns_controlled_conditioning_error():
    r = call(foundation, length="100 m", flexural_rigidity="1e20 N*m^2", foundation_stiffness="1e-9 N/m^2", uniform_load="1 N/m")
    assert not r.ok
    assert r.error.family == "input.out_of_range"
