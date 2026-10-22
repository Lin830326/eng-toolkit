"""Mass and frequency checks against independent beam formulas."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d._model import compile_frame, parse_frame
from eng_toolkit.domains.structures3d.modal import _element_mass, assemble_mass, modal
from tests.structures3d_testing import FRAME, call, close, ok, quantity, table, v


def test_element_mass_matches_shape_function_quadrature():
    length, mu, polar = 2.3, 7.8, .013
    actual = _element_mass(length, mu, polar)
    reference = np.zeros((12, 12))
    points, weights = np.polynomial.legendre.leggauss(5)
    for point, weight in zip(points, weights):
        t = (point + 1) / 2
        linear = np.array([1-t, t])
        hermite = np.array([1-3*t*t+2*t**3, length*(t-2*t*t+t**3),
                            3*t*t-2*t**3, length*(-t*t+t**3)])
        shapes = np.zeros((4, 12))
        shapes[0, [0, 6]] = linear
        shapes[1, [1, 5, 7, 11]] = hermite
        shapes[2, [2, 4, 8, 10]] = hermite * [1, -1, 1, -1]
        shapes[3, [3, 9]] = linear
        reference += weight * length / 2 * (mu * shapes[:3].T @ shapes[:3] + polar * np.outer(shapes[3], shapes[3]))
    np.testing.assert_allclose(actual, reference, rtol=2e-14, atol=1e-13)


def test_rayleigh_bending_rotary_mass_matches_independent_quadrature():
    length, mu, polar, rotary_y, rotary_z = 2.3, 7.8, .013, .021, .034
    ordinary = _element_mass(length, mu, polar)
    rayleigh = _element_mass(length, mu, polar, rotary_y, rotary_z)
    reference = np.zeros((12, 12))
    points, weights = np.polynomial.legendre.leggauss(5)
    for point, weight in zip(points, weights):
        t = (point + 1) / 2
        slope = np.array([(-6*t+6*t*t)/length, 1-4*t+3*t*t,
                          (6*t-6*t*t)/length, -2*t+3*t*t])
        slope_v = np.zeros(12)
        slope_w = np.zeros(12)
        slope_v[[1, 5, 7, 11]] = slope
        slope_w[[2, 4, 8, 10]] = slope * [1, -1, 1, -1]
        reference += weight * length / 2 * (rotary_z * np.outer(slope_v, slope_v)
                                           + rotary_y * np.outer(slope_w, slope_w))
    np.testing.assert_allclose(rayleigh-ordinary, reference, rtol=2e-13, atol=1e-13)


def test_cantilever_frequency_converges_to_closed_beam_value():
    results = [ok(modal, **FRAME, density="7800 kg/m^3", elements_per_member=n, modes_count=1)
               for n in (1, 4, 10)]
    # First bending direction uses Iy=1e-6 m^4 and mu=rho*A=7.8 kg/m.
    expected = 1.875104068711961**2 / (2*math.pi) * math.sqrt(200e9*1e-6/(7.8*2**4))
    errors = [abs(v(result, "first_frequency")-expected) for result in results]
    assert errors[2] < errors[1] < errors[0]
    close(v(results[-1], "first_frequency"), expected, rel=2e-6)
    close(results[-1].data["closed_form_comparison"]["bending_about_local_y_hz"][0], expected)
    comparison = results[-1].data["closed_form_comparison"]
    close(comparison["axial_hz"][0], math.sqrt(200e9 * 1e-3 / 7.8) / (4 * 2))
    close(comparison["torsion_hz"][0], math.sqrt(80e9 * 1e-6 / (7.8 * 3e-6 / 1e-3)) / (4 * 2))
    assert max(results[-1].data["eigen_residuals"]) < 1e-8
    assert len(table(results[-1])) == 11


def test_optional_rayleigh_inertia_lowers_cantilever_frequency():
    base = ok(modal, **FRAME, density="7800 kg/m^3", elements_per_member=4, modes_count=1)
    rotary = ok(modal, **FRAME, density="7800 kg/m^3", elements_per_member=4,
                modes_count=1, include_rotary_inertia=True)
    assert v(rotary, "first_frequency") < v(base, "first_frequency")
    assert rotary.data["mass_model"] == "Rayleigh bending rotary inertia"
    assert "closed_form_comparison" not in rotary.data


def test_free_frame_has_six_rigid_modes_and_translation_mass():
    result = ok(modal, **{**FRAME, "supports": ""}, mass_per_length="7.8 kg/m", modes_count=8)
    assert result.data["zero_mode_count"] == 6
    for mode in result.data["modes"][:6]:
        close(quantity(mode["frequency"]), 0)
    for axis in "xyz":
        close(sum(mode["effective_mass_fraction_" + axis] for mode in result.data["modes"][:6]), 1, rel=1e-8)
    for axis in ("rx", "ry", "rz"):
        close(sum(mode["effective_mass_fraction_" + axis] for mode in result.data["modes"][:6]), 1, rel=1e-8)


@pytest.mark.parametrize("supports,zeros", [
    ("", 6),
    ("a,pinned", 3),
    ("a,pinned;b,uy+uz", 1),
    ("a,fixed", 0),
])
def test_analytic_rigid_modes_follow_partial_restraints(supports, zeros):
    result = ok(modal, **{**FRAME, "supports": supports}, density="7800 kg/m^3", modes_count=6)
    assert result.data["zero_mode_count"] == zeros
    assert all(quantity(mode["frequency"]) == 0 for mode in result.data["modes"][:zeros])


@pytest.mark.parametrize("free_axis,other_axes,expected_omega2", [
    ("ux", "uy+uz+rx+ry+rz", 3 * 200e9 * 1e-3 / (7.8 * 2**2)),
    ("rx", "ux+uy+uz+ry+rz", 3 * 80e9 * 1e-6 / (7.8 * (3e-6 / 1e-3) * 2**2)),
])
def test_single_element_axial_and_torsional_hand_frequencies(free_axis, other_axes, expected_omega2):
    result = ok(modal, **{**FRAME, "supports": f"a,fixed;b,{other_axes}"},
                mass_per_length="7.8 kg/m", elements_per_member=1, modes_count=1)
    close(v(result, "first_frequency"), math.sqrt(expected_omega2) / (2 * math.pi), rel=2e-12)


def test_private_release_rotation_retains_bending_mass():
    result = ok(modal, **{**FRAME, "supports": "a,fixed;b,fixed"},
                releases="e,start,rz;e,end,rz", mass_per_length="7.8 kg/m",
                elements_per_member=1, modes_count=1)
    expected = math.sqrt(120 * 200e9 * 2e-6 / (7.8 * 2**4)) / (2 * math.pi)
    close(v(result, "first_frequency"), expected, rel=2e-12)
    assert any("@e/start/rz" == row["node_or_release"] for row in table(result))


def test_converged_axial_and_torsional_chain_against_continuous_bar():
    count = 20
    nodes = ";".join(f"n{i},{i/10} m,0 m,0 m" for i in range(count + 1))
    members = ";".join(
        f"e{i},n{i},n{i+1},200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4"
        for i in range(count)
    )
    supports = "n0,fixed;" + ";".join(f"n{i},uy+uz+ry+rz" for i in range(1, count + 1))
    result = ok(modal, nodes=nodes, members=members, supports=supports,
                density="7800 kg/m^3", elements_per_member=1, modes_count=2)
    torsion = math.sqrt(80e9 * 1e-6 / (7800 * 3e-6)) / 8
    axial = math.sqrt(200e9 / 7800) / 8
    close(quantity(result.data["modes"][0]["frequency"]), torsion, rel=5e-4)
    close(quantity(result.data["modes"][1]["frequency"]), axial, rel=5e-4)
    assert result.data["zero_mode_count"] == 0


def test_nodal_inertia_on_inactive_released_joint_rotation_is_refused():
    result = call(modal, **FRAME, releases="e,end,rx+ry+rz", density="7800 kg/m^3",
                  nodal_masses="b,0 kg,1 kg*m^2")
    assert not result.ok
    assert result.error.details["param"] == "/nodal_masses"


def test_generalized_eigenvalues_against_scipy_and_released_mass():
    scipy = pytest.importorskip("scipy.linalg")
    model = parse_frame(FRAME["nodes"], FRAME["members"], FRAME["supports"], releases="e,end,ry+rz")
    mesh = compile_frame(model, 3)
    mass = assemble_mass(mesh, np.array([7.8]), np.zeros(12))
    assert np.min(np.linalg.eigvalsh(mass[np.ix_([*range(6, len(mass))], [*range(6, len(mass))])])) >= -1e-10
    result = ok(modal, **FRAME, releases="e,end,ry+rz", mass_per_length="7.8 kg/m", elements_per_member=3, modes_count=5)
    from eng_toolkit.domains.structures3d._model import kinematic_space
    free, _, _ = kinematic_space(mesh)
    expected = scipy.eigh(mesh.k[np.ix_(free, free)], mass[np.ix_(free, free)], eigvals_only=True)
    actual = np.array([quantity(mode["omega"])**2 for mode in result.data["modes"]])
    np.testing.assert_allclose(actual, expected[:5], rtol=1e-8)


@pytest.mark.parametrize("extra,param", [
    ({"density": "7800 kg/m^3", "mass_per_length": "7.8 kg/m"}, "/mass_per_length"),
    ({"member_masses": "missing,1 kg/m"}, "/member_masses/0/0"),
    ({"density": "7800 kg/m^3", "shear_properties": "e,800 mm^2,800 mm^2"}, "/shear_properties"),
])
def test_modal_refusals(extra, param):
    result = call(modal, **FRAME, **extra)
    assert not result.ok
    assert result.error.details["param"] == param
