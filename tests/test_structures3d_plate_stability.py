"""Literal plate stability benchmarks and scoped-branch rejection tests."""

import math

import pytest

from eng_toolkit.domains.structures3d.plate_stability import plate_stability
from tests.structures3d_testing import call, close, ok, quantity, v


PLATE = dict(length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa")
D = 200e9 * 0.01**3 / (12 * (1 - 0.3**2))


def test_square_uniaxial_exact_and_finite_integer_minimum():
    square = ok(plate_stability, **PLATE, stress_x="10 MPa")
    close(v(square, "buckling_coefficient"), 4.)
    # D=200e9*0.01^3/(12*0.91)=18315.018315 N*m;
    # sigma_cr=4*pi^2*D/(0.01*1^2)=72.304794147 MPa; divide by 10 MPa for lambda.
    close(v(square, "critical_stress", "Pa"), 72_304_794.14717479)
    close(v(square, "load_multiplier"), 7.230479414717479)
    assert square.data["critical_mode"] == {"m": 1, "n": 1}
    rectangle = ok(plate_stability, **{**PLATE, "length": "2.5 m"})
    # Try m=2 and 3 about a/b=2.5: k2=(0.8+1.25)^2=4.2025;
    # k3=(1.2+2.5/3)^2=4.134444444, so three longitudinal half-waves govern.
    close(v(rectangle, "buckling_coefficient"), 4.134444444444444)
    assert rectangle.data["critical_mode"] == {"m": 3, "n": 1}


@pytest.mark.parametrize("edges,k", [("SSSC", 5.42), ("SCSC", 6.97)])
def test_long_coefficients_have_explicit_scope(edges, k):
    result = ok(plate_stability, **{**PLATE, "length": "10 m"}, edges=edges)
    close(v(result, "buckling_coefficient"), k)
    close(v(result, "critical_stress"), k * math.pi**2 * D / 0.01)
    assert "estimate" in result.data["solution"]
    assert not call(plate_stability, **PLATE, edges=edges).ok


def test_shear_and_in_plane_bending():
    shear = ok(plate_stability, **PLATE, analysis="shear")
    close(v(shear, "buckling_coefficient"), 9.34)
    long_shear = ok(plate_stability, **{**PLATE, "length": "10 m"}, analysis="shear")
    close(v(long_shear, "buckling_coefficient"), 5.38)
    bending = ok(plate_stability, **{**PLATE, "length": "10 m"}, analysis="bending")
    close(v(bending, "buckling_coefficient"), 23.9)
    assert any("membrane" in warning for warning in bending.warnings)


def test_biaxial_equal_compression_and_uniaxial_limit():
    biaxial = ok(plate_stability, **PLATE, analysis="biaxial", stress_x="10 MPa", stress_y="10 MPa")
    # Equal x/y compression doubles the (1,1) geometric denominator,
    # giving lambda=72.304794147/(2*10)=3.615239707.
    close(v(biaxial, "load_multiplier"), 3.6152397073587395)
    assert biaxial.data["critical_mode"] == {"m": 1, "n": 1}
    uni = ok(plate_stability, **PLATE, analysis="biaxial", stress_x="10 MPa")
    close(v(uni, "load_multiplier"), 7.230479414717479)
    close(v(uni, "critical_stress_y"), 0.)


def test_biaxial_axis_swap_and_certified_bound():
    one = ok(plate_stability, **{**PLATE, "length": "2 m"}, analysis="biaxial", stress_x="10 MPa", stress_y="3 MPa")
    two = ok(plate_stability, **{**PLATE, "width": "2 m"}, analysis="biaxial", stress_x="3 MPa", stress_y="10 MPa")
    close(v(one, "load_multiplier"), v(two, "load_multiplier"))
    assert one.data["critical_mode"] == {"m": two.data["critical_mode"]["n"], "n": two.data["critical_mode"]["m"]}
    assert not call(plate_stability, **{**PLATE, "length": "20 m"}, analysis="biaxial", stress_x="10 MPa", series_terms=2).ok


def test_vibration_exact_modes_density_scaling_and_units():
    vibration = ok(plate_stability, **PLATE, analysis="vibration", density="7850 kg/m^3", modes=4)
    # rho*t=7850*0.01=78.5 kg/m^2 and f11=pi*sqrt(18315.018315/78.5).
    # The integer sums m^2+n^2 are 2,5,5,8; multiplying density by four halves f.
    expected = 47.986461117141864
    close(v(vibration, "fundamental_frequency", "Hz"), expected)
    frequencies = [quantity(mode["frequency"], "Hz") for mode in vibration.data["modes"]]
    assert frequencies == pytest.approx([expected, 2.5 * expected, 2.5 * expected, 4 * expected])
    denser = ok(plate_stability, **PLATE, analysis="vibration", density="31400 kg/m^3", unit_system="imperial")
    close(v(denser, "fundamental_frequency", "Hz"), expected / 2)


@pytest.mark.parametrize("changes", [
    {"analysis": "vibration"}, {"analysis": "vibration", "density": "7850 kg/m^3", "edges": "SCSC"},
    {"analysis": "vibration", "density": "7850 kg/m^3", "stress_x": "1 MPa"},
    {"analysis": "biaxial"}, {"analysis": "shear", "length": "0.5 m"},
    {"analysis": "bending"}, {"stress_y": "1 MPa"}, {"thickness": "100 mm"},
    {"stress_x": "-1 MPa"}, {"series_terms": True}, {"modes": 31}, {"density": "7850 kg/m^3"},
])
def test_stability_scope_rejections(changes):
    assert not call(plate_stability, **{**PLATE, **changes}).ok


def test_yield_scope_warning():
    result = ok(plate_stability, **PLATE, yield_strength="10 MPa")
    assert any("yield" in warning for warning in result.warnings)
