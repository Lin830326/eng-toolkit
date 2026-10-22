"""Seeded independent numerical routes across the six public tools."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d._model import compile_frame, parse_frame
from eng_toolkit.domains.structures3d.buckling import buckling
from eng_toolkit.domains.structures3d.foundation import foundation
from eng_toolkit.domains.structures3d.modal import assemble_mass, modal
from eng_toolkit.domains.structures3d.plate import plate
from eng_toolkit.domains.structures3d.plate_stability import plate_stability
from tests.structures3d_testing import close, ok, quantity, table, v


def test_random_modal_generalized_eigenproblem_with_lumped_mass():
    scipy = pytest.importorskip("scipy.linalg")
    rng = np.random.default_rng(211)
    for _ in range(4):
        length, tip_mass = rng.uniform(1., 3.), rng.uniform(.2, 4.)
        args = dict(nodes=f"a,0 m,0 m,0 m;b,{length:.16g} m,0 m,0 m",
                    members="e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4", supports="a,fixed")
        mesh = compile_frame(parse_frame(**args), 4)
        lumped = np.zeros(12)
        lumped[6:9] = tip_mass
        mass = assemble_mass(mesh, np.array([7.8]), lumped)
        # Independent generalized driver, with 6 fixed DOFs removed explicitly.
        expected = scipy.eigh(mesh.k[6:, 6:], mass[6:, 6:], eigvals_only=True)
        actual = ok(modal, **args, mass_per_length="7.8 kg/m", nodal_masses=f"b,{tip_mass:.16g} kg,0 kg*m^2", modes_count=8)
        np.testing.assert_allclose([quantity(mode["omega"])**2 for mode in actual.data["modes"]], expected[:8], rtol=3e-8)


def test_inclined_released_end_preserves_modal_and_buckling_spectra():
    axis = np.array([1., 2., 3.])/math.sqrt(14)
    skew = np.array([[0., -axis[2], axis[1]], [axis[2], 0., -axis[0]], [-axis[1], axis[0], 0.]])
    rotation = np.eye(3)+math.sin(.8)*skew+(1-math.cos(.8))*(skew@skew)
    original = dict(nodes="a,0 m,0 m,0 m;b,2 m,0 m,0 m",
                    members="e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4",
                    supports="a,fixed", releases="e,end,ry+rz", elements_per_member=4)
    endpoint, reference_y = rotation@np.array([2., 0., 0.]), rotation@np.array([0., 1., 0.])
    turned = {**original, "nodes": "a,0 m,0 m,0 m;b,"+",".join(f"{x:.16g} m" for x in endpoint),
              "orientations": "e,vector,"+",".join(f"{x:.16g}" for x in reference_y)}
    a = ok(modal, **original, density="7800 kg/m^3", modes_count=8)
    b = ok(modal, **turned, density="7800 kg/m^3", modes_count=8)
    np.testing.assert_allclose([quantity(mode["frequency"]) for mode in a.data["modes"]],
                               [quantity(mode["frequency"]) for mode in b.data["modes"]], rtol=2e-9)
    compressive = rotation@np.array([-1000., 0., 0.])
    a = ok(buckling, **original, loads="b,-1000 N,0 N,0 N,0 N*m,0 N*m,0 N*m", modes_count=4)
    b = ok(buckling, **turned, loads="b,"+",".join(f"{x:.16g} N" for x in compressive)+",0 N*m,0 N*m,0 N*m", modes_count=4)
    np.testing.assert_allclose([quantity(mode["load_factor"]) for mode in a.data["buckling_modes"]],
                               [quantity(mode["load_factor"]) for mode in b.data["buckling_modes"]], rtol=2e-9)


def test_random_buckling_independent_weak_form_assembly():
    scipy = pytest.importorskip("scipy.linalg")
    rng = np.random.default_rng(737)
    gauss, weights = np.polynomial.legendre.leggauss(4)
    for _ in range(4):
        length, ei, load = rng.uniform(1., 3.), rng.uniform(1e5, 5e5), rng.uniform(500., 2000.)
        n, h = 4, length/4
        kk, gg = np.zeros((10, 10)), np.zeros((10, 10))
        for element in range(n):
            ke, ge = np.zeros((4, 4)), np.zeros((4, 4))
            for xi, weight in zip(gauss, weights):
                s = (xi+1)/2
                curvature = np.array([-6+12*s, h*(-4+6*s), 6-12*s, h*(-2+6*s)])/h**2
                slope = np.array([-6*s+6*s*s, h*(1-4*s+3*s*s), 6*s-6*s*s, h*(-2*s+3*s*s)])/h
                ke += weight*h/2*ei*np.outer(curvature, curvature)
                ge += weight*h/2*load*np.outer(slope, slope)
            indices = np.arange(2*element, 2*element+4)
            kk[np.ix_(indices, indices)] += ke
            gg[np.ix_(indices, indices)] += ge
        free = [1, 2, 3, 4, 5, 6, 7, 9]  # Both end transverse translations fixed.
        expected = scipy.eigh(kk[np.ix_(free, free)], gg[np.ix_(free, free)], eigvals_only=True)[0]
        actual = ok(buckling, nodes=f"a,0 m,0 m,0 m;b,{length:.16g} m,0 m,0 m",
                    members=f"e,a,b,200 GPa,80 GPa,1000 mm^2,{ei/200e9:.16g} m^4,{2*ei/200e9:.16g} m^4,1e-6 m^4",
                    supports="a,pinned;a,rx;b,uy+uz", loads=f"b,{-load:.16g} N,0 N,0 N,0 N*m,0 N*m,0 N*m",
                    elements_per_member=4, modes_count=1)
        close(v(actual, "first_load_factor"), expected, rel=1e-9)


def test_random_plate_centres_against_scalar_double_series():
    rng = np.random.default_rng(109)
    for _ in range(4):
        a, b, pressure = rng.uniform(.7, 2.), rng.uniform(.8, 1.3), rng.uniform(20., 500.)
        d = 200e9*.01**3/(12*(1-.3**2))
        # At the centre, sin(m*pi/2)=(-1)^((m-1)/2) for odd m.
        expected = sum(16*pressure*(-1)**((m+n-2)//2)/(math.pi**6*d*m*n*((m/a)**2+(n/b)**2)**2)
                       for m in range(1, 202, 2) for n in range(1, 202, 2))
        actual = ok(plate, length=f"{a:.16g} m", width=f"{b:.16g} m", thickness="10 mm", youngs_modulus="200 GPa",
                    pressure=f"{pressure:.16g} Pa", terms=81)
        close(v(actual, "centre_deflection"), expected, rel=1e-7, abs=1e-12)


def test_random_plate_buckling_against_exhaustive_integer_modes():
    rng = np.random.default_rng(301)
    for aspect in rng.uniform(.15, 8., 8):
        actual = ok(plate_stability, length=f"{aspect:.16g} m", width="1 m", thickness="5 mm", youngs_modulus="200 GPa")
        # Full m,n search of D*(alpha²+beta²)²/(t*alpha²), divided by pi²D/(t*b²).
        expected = min(((m/aspect)**2+n*n)**2/(m/aspect)**2 for m in range(1, 50) for n in range(1, 50))
        close(v(actual, "buckling_coefficient"), expected, rel=1e-12)


def test_random_foundation_against_separated_continuum_series():
    rng = np.random.default_rng(804)
    for _ in range(4):
        length, ei, k, pressure = rng.uniform(3., 8.), rng.uniform(1e6, 5e6), rng.uniform(2e4, 1e5), rng.uniform(500., 2000.)
        actual = ok(foundation, length=f"{length:.16g} m", flexural_rigidity=f"{ei:.16g} N*m^2",
                    foundation_stiffness=f"{k:.16g} N/m^2", uniform_load=f"{pressure:.16g} N/m",
                    left_end="pinned", right_end="pinned", elements=60)
        rows = table(actual)
        centre = min(rows, key=lambda row: abs(float(row["x_m"])-length/2))
        x = float(centre["x_m"])
        # Simply supported continuum: (EI*(n*pi/L)^4+k)*w_n=4q/(n*pi), odd n.
        expected = sum(4*pressure*math.sin(n*math.pi*x/length)/(n*math.pi*(ei*(n*math.pi/length)**4+k)) for n in range(1, 2002, 2))
        close(float(centre["deflection_m"]), expected, rel=3e-7, abs=1e-11)
