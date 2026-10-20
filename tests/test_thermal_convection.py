"""thermal.convection checked against hand-computed correlation values."""

import math

import pytest

from eng_toolkit.domains.thermal.convection import (
    churchill_bernstein,
    churchill_chu_cylinder,
    churchill_chu_plate,
    churchill_chu_plate_laminar,
    convection,
    dittus_boelter,
    gnielinski,
    petukhov_friction_factor,
    whitaker_sphere,
)
from tests.thermal_testing import fails, ok, v

# Water-like fluid in a D = 50 mm pipe: rho = 1000, mu = 1e-3, k = 0.6, V = 1 m/s -> Re = rho V D/mu = 1000 * 1 * 0.05 / 1e-3 = 50 000
# Pr = 5 from cp = Pr k/mu = 5 * 0.6/1e-3 = 3000 J/(kg K)
PIPE = dict(length="50 mm", conductivity="0.6 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", specific_heat="3000 J/(kg*K)", velocity="1 m/s")


def test_gnielinski_hand_value():
    # Re = 50 000, Pr = 5:  ln Re = 10.819778 ; f = (0.790 * 10.819778 - 1.64)^-2 = 6.907625^-2 = 0.0209576 ; f/8 = 0.00261970
    #   sqrt(f/8) = 0.0511830 ; Pr^(2/3) = 2.924018 ; denominator = 1 + 12.7 * 0.0511830 * 1.924018 = 2.250638
    #   numerator = 0.0026197 * (50 000 - 1000) * 5 = 641.83 ;  Nu = 285.173 ; h = Nu k/D = 285.173 * 0.6/0.05 = 3422.08 W/m^2K
    r = ok(convection, correlation="gnielinski", **PIPE)
    assert math.isclose(v(r, "reynolds"), 50000.0, rel_tol=1e-12)
    assert math.isclose(v(r, "prandtl"), 5.0, rel_tol=1e-12)
    assert math.isclose(v(r, "nusselt"), 285.173, rel_tol=1e-5)
    assert math.isclose(v(r, "h"), 3422.08, rel_tol=1e-5)
    assert math.isclose(r.data["petukhov_friction_factor"], 0.0209576, rel_tol=1e-5)
    assert r.warnings[1:] == []  # inside 3000..5e6 and 0.5..2000
    assert r.value.unit == "watt / kelvin / meter ** 2"
    # the same through Pr given directly
    by_pr = ok(convection, correlation="gnielinski", **{**PIPE, "specific_heat": ""}, prandtl=5.0)
    assert math.isclose(v(by_pr, "h"), v(r, "h"), rel_tol=1e-12)


def test_dittus_boelter_hand_values_heating_and_cooling():
    # Re = 50 000, Pr = 5: Re^0.8 = exp(0.8 * 10.819778) = 5755.1 ; heating n = 0.4: 5^0.4 = 1.903654 -> Nu = 0.023 * 5755.1 * 1.903654 = 251.97...
    heating = ok(convection, correlation="dittus_boelter", **PIPE)
    cooling = ok(convection, correlation="dittus_boelter", **PIPE, heating=False)
    assert math.isclose(v(heating, "nusselt"), 251.4733, rel_tol=1e-6)
    assert math.isclose(v(heating, "nusselt"), 0.023 * 50000.0**0.8 * 5.0**0.4, rel_tol=1e-12)
    assert math.isclose(v(cooling, "nusselt"), 214.0892, rel_tol=1e-6)  # n = 0.3: 5^0.3 = 1.620657
    assert math.isclose(v(heating, "h"), 251.4733 * 0.6 / 0.05, rel_tol=1e-6)


def test_laminar_internal_constants():
    slow = {**PIPE, "velocity": "0.02 m/s"}  # Re = 1000
    t = ok(convection, correlation="internal_laminar_constant_temperature", **slow)
    q = ok(convection, correlation="internal_laminar_constant_flux", **slow)
    assert v(t, "nusselt") == 3.66 and v(q, "nusselt") == 4.36
    assert math.isclose(v(t, "h"), 3.66 * 0.6 / 0.05, rel_tol=1e-12)  # 43.92
    assert math.isclose(v(t, "reynolds"), 1000.0, rel_tol=1e-12)
    turbulent = ok(convection, correlation="internal_laminar_constant_flux", **PIPE)
    assert any("not laminar" in w for w in turbulent.warnings)
    short = ok(convection, correlation="internal_laminar_constant_temperature", **slow, pipe_length="0.5 m")  # thermal entry 0.05*1000*5*0.05 = 12.5 m
    assert any("thermal entry length" in w for w in short.warnings)


def test_flat_plate_laminar_and_mixed():
    # Re_L = rho V L/mu = 1000 * 1 * 0.1/1e-3 = 1e5, Pr = 7: Nu = 0.664 * 316.2278 * 7^(1/3) (1.912931) = 401.668
    plate = dict(length="0.1 m", conductivity="0.6 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", velocity="1 m/s", prandtl=7.0)
    lam = ok(convection, correlation="flat_plate_laminar", **plate)
    assert math.isclose(v(lam, "nusselt"), 0.664 * math.sqrt(1e5) * 7 ** (1 / 3), rel_tol=1e-12)
    assert math.isclose(v(lam, "nusselt"), 401.668, rel_tol=1e-6)
    assert math.isclose(lam.data["local_nusselt_at_trailing_edge"], v(lam, "nusselt") / 2, rel_tol=1e-12)  # 0.332 vs 0.664
    assert math.isclose(v(lam, "h"), 401.668 * 6.0, rel_tol=1e-6)
    # mixed: Re_L = 1e6, Pr = 0.7: (0.037 * 1e6^0.8 - 871) * 0.7^(1/3) = (0.037 * 63095.73 - 871) * 0.887904 = 1463.54 * 0.887904 = 1299.485
    mixed = ok(convection, correlation="flat_plate_mixed", length="1 m", conductivity="0.026 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", velocity="1 m/s", prandtl=0.7)
    assert math.isclose(v(mixed, "reynolds"), 1e6, rel_tol=1e-12)
    assert math.isclose(v(mixed, "nusselt"), 1299.485, rel_tol=1e-6)
    assert mixed.warnings[1:] == []
    # at the transition Reynolds number both agree: laminar 0.664 sqrt(5e5) = 469.5 against mixed 0.037 * 5e5^0.8 - 871 = 472.5 (1 %)
    assert abs(0.664 * math.sqrt(5e5) - (0.037 * 5e5**0.8 - 871)) / 470 < 0.01


def test_cylinder_in_cross_flow_and_sphere():
    # D = 10 mm, Re = 1e4 (V = 1 m/s of a rho 1000 / mu 1e-3 fluid), Pr = 0.7
    #   Churchill-Bernstein: 0.3 + 0.62 * 100 * 0.887904/[1 + (0.4/0.7)^(2/3)]^(1/4) * [1 + (1e4/282000)^(5/8)]^(4/5) = 53.328
    #   Whitaker: 2 + (0.4 * 100 + 0.06 * 1e4^(2/3)) * 0.7^0.4 = 2 + (40 + 27.85) * 0.866... = 60.828
    obj = dict(length="10 mm", conductivity="0.026 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", velocity="1 m/s", prandtl=0.7)
    cyl = ok(convection, correlation="cylinder_cross_flow", **obj)
    sph = ok(convection, correlation="sphere", **obj)
    assert math.isclose(v(cyl, "nusselt"), 53.3278, rel_tol=1e-6)
    assert math.isclose(v(sph, "nusselt"), 60.8283, rel_tol=1e-6)
    assert any("viscosity_surface not given" in w for w in sph.warnings)
    with_ratio = ok(convection, correlation="sphere", **obj, viscosity_surface="0.5e-3 Pa*s")  # mu/mu_s = 2 -> factor 2^0.25 on the second term
    assert math.isclose(v(with_ratio, "nusselt"), 2 + (60.8283 - 2) * 2 ** 0.25, rel_tol=1e-6)


def test_natural_convection_churchill_chu():
    # Vertical plate L = 0.5 m, air-like: rho = 1.2, mu = 1.8e-5, beta = 0.003 1/K, dT = 80 - 20 = 60 K, Pr = 0.71
    #   Gr = g beta dT L^3 rho^2/mu^2 = 9.80665 * 0.003 * 60 * 0.125 * 1.44 / 3.24e-10 = 9.80665e8 ; Ra = Gr Pr = 6.962722e8
    #   Nu(full range) = {0.825 + 0.387 Ra^(1/6) / [1 + (0.492/Pr)^(9/16)]^(8/27)}^2 = 109.901
    #   Nu(laminar)    = 0.68 + 0.670 Ra^(1/4) / [1 + (0.492/Pr)^(9/16)]^(4/9) = 84.214
    #   horizontal cylinder (0.559): {0.60 + 0.387 Ra^(1/6) / [1 + (0.559/Pr)^(9/16)]^(8/27)}^2 = 103.323
    nat = dict(length="0.5 m", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, surface_temperature="80 degC", ambient_temperature="20 degC", expansion_coefficient="0.003 1/K")
    plate = ok(convection, correlation="natural_vertical_plate", **nat)
    assert math.isclose(v(plate, "grashof"), 9.80665e8, rel_tol=1e-12)
    assert math.isclose(v(plate, "rayleigh"), 6.962722e8, rel_tol=1e-6)
    assert math.isclose(v(plate, "nusselt"), 109.901, rel_tol=1e-5)
    assert math.isclose(v(plate, "h"), 109.901 * 0.026 / 0.5, rel_tol=1e-5)
    assert math.isclose(v(ok(convection, correlation="natural_vertical_plate_laminar", **nat), "nusselt"), 84.214, rel_tol=1e-5)
    assert math.isclose(v(ok(convection, correlation="natural_horizontal_cylinder", **nat), "nusselt"), 103.323, rel_tol=1e-5)
    # velocity is meaningless for natural convection
    assert any("velocity" in w for w in ok(convection, correlation="natural_vertical_plate", **nat, velocity="1 m/s").warnings)


def test_natural_convection_temperature_handling():
    nat = dict(length="0.5 m", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, expansion_coefficient="0.003 1/K")
    c = ok(convection, correlation="natural_vertical_plate", **nat, surface_temperature="80 degC", ambient_temperature="20 degC")
    k = ok(convection, correlation="natural_vertical_plate", **nat, surface_temperature="353.15 K", ambient_temperature="293.15 K")
    f = ok(convection, correlation="natural_vertical_plate", **nat, surface_temperature="176 degF", ambient_temperature="68 degF")  # dT = 108 degF = 60 K
    cold = ok(convection, correlation="natural_vertical_plate", **nat, surface_temperature="20 degC", ambient_temperature="80 degC")
    for other in (k, f, cold):
        assert math.isclose(v(other, "rayleigh"), v(c, "rayleigh"), rel_tol=1e-9)
    # beta blank: ideal gas 1/T_film = 1/323.15 K (with a note)
    ideal = ok(convection, correlation="natural_vertical_plate", length="0.5 m", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, surface_temperature="80 degC", ambient_temperature="20 degC")
    assert math.isclose(v(ideal, "rayleigh"), v(c, "rayleigh") * (0.003 ** -1) / 323.15, rel_tol=1e-9)
    assert any("ideal gas" in w for w in ideal.warnings)
    fails(convection, "input.invalid", "surface_temperature|ambient_temperature", correlation="natural_vertical_plate", **nat, surface_temperature="20 degC", ambient_temperature="293.15 K")
    fails(convection, "input.invalid", "surface_temperature", correlation="natural_vertical_plate", **nat, surface_temperature="60 delta_degC", ambient_temperature="20 degC")
    fails(convection, "input.missing", "surface_temperature|ambient_temperature", correlation="natural_vertical_plate", **nat)
    # beta given per degC means per kelvin of difference, not an offset temperature
    per_c = ok(convection, correlation="natural_vertical_plate", **{**nat, "expansion_coefficient": "0.003 1/degC"}, surface_temperature="80 degC", ambient_temperature="20 degC")
    assert math.isclose(v(per_c, "rayleigh"), v(c, "rayleigh"), rel_tol=1e-12)


def test_correlation_functions_directly():
    assert math.isclose(petukhov_friction_factor(50000.0), 0.0209576, rel_tol=1e-5)
    assert math.isclose(gnielinski(50000.0, 5.0), 285.173, rel_tol=1e-5)
    assert math.isclose(dittus_boelter(1e4, 1.0, True), 0.023 * 1e4**0.8, rel_tol=1e-14)  # Pr = 1: n does not matter
    assert math.isclose(churchill_bernstein(1e4, 0.7), 53.3278, rel_tol=1e-6)
    assert math.isclose(whitaker_sphere(1e4, 0.7), 60.8283, rel_tol=1e-6)
    assert math.isclose(whitaker_sphere(0.0, 0.7), 2.0, rel_tol=1e-14)  # still fluid around a sphere: Nu = 2
    assert math.isclose(churchill_chu_plate(6.96272150e8, 0.71), 109.901, rel_tol=1e-5)
    assert math.isclose(churchill_chu_plate_laminar(6.96272150e8, 0.71), 84.214, rel_tol=1e-5)
    assert math.isclose(churchill_chu_cylinder(6.96272150e8, 0.71), 103.323, rel_tol=1e-5)
    # Gnielinski and Dittus-Boelter agree to within ~10 % in their common range (a textbook consistency check)
    assert abs(gnielinski(5e4, 5.0) / dittus_boelter(5e4, 5.0, True) - 1.0) < 0.15


# ---------------------------------------------------------------------------------------------------- validity warnings
@pytest.mark.parametrize(
    "correlation, velocity, prandtl, fragment",
    [
        ("dittus_boelter", "0.05 m/s", 5.0, "below 10 000"),  # Re = 2500
        ("dittus_boelter", "1 m/s", 200.0, "0.6 .. 160"),
        ("gnielinski", "0.04 m/s", 5.0, "outside 3000 .. 5e6"),  # Re = 2000
        ("gnielinski", "1 m/s", 3000.0, "0.5 .. 2000"),
        ("flat_plate_laminar", "20 m/s", 7.0, "turbulent"),  # Re_L = 1e6 on L = 50 mm
        ("flat_plate_laminar", "1 m/s", 100.0, "0.6 <= Pr <= 50"),
        ("flat_plate_mixed", "8 m/s", 7.0, "outside 5e5 .. 1e8"),  # Re_L = 4e5: positive but below the transition
        ("cylinder_cross_flow", "1e-6 m/s", 0.7, "0.2"),
        ("sphere", "1e-5 m/s", 0.7, "3.5 .. 7.6e4"),
        ("sphere", "1 m/s", 1000.0, "0.71 .. 380"),
    ],
)
def test_out_of_range_inputs_warn(correlation, velocity, prandtl, fragment):
    r = ok(convection, correlation=correlation, length="50 mm", conductivity="0.6 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", velocity=velocity, prandtl=prandtl)
    assert any(fragment in w for w in r.warnings), r.warnings


def test_inside_the_range_does_not_warn():
    r = ok(convection, correlation="gnielinski", length="50 mm", conductivity="0.6 W/(m*K)", density="1000 kg/m^3", dynamic_viscosity="1e-3 Pa*s", velocity="0.1 m/s", prandtl=5.0)
    assert v(r, "reynolds") == 5000.0 and r.warnings[1:] == []


def test_natural_ranges():
    huge = ok(convection, correlation="natural_vertical_plate_laminar", length="2 m", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, surface_temperature="80 degC", ambient_temperature="20 degC", expansion_coefficient="0.003 1/K")
    assert any("above 1e9" in w for w in huge.warnings)  # Ra = 6.96e8 * 64 = 4.46e10
    tiny = ok(convection, correlation="natural_horizontal_cylinder", length="0.01 mm", conductivity="0.026 W/(m*K)", density="1.2 kg/m^3", dynamic_viscosity="1.8e-5 Pa*s", prandtl=0.71, surface_temperature="21 degC", ambient_temperature="20 degC", expansion_coefficient="0.003 1/K")
    assert any("1e-5 .. 1e12" in w for w in tiny.warnings)


# ---------------------------------------------------------------------------------------------------- refusals
def test_undefined_correlations_are_refused_not_extrapolated():
    slow = {**PIPE, "velocity": "0.01 m/s"}  # Re = 500
    fails(convection, "input.out_of_range", "velocity", correlation="gnielinski", **slow)  # (Re - 1000) < 0
    fails(convection, "input.out_of_range", "velocity", correlation="flat_plate_mixed", **{**PIPE, "velocity": "0.1 m/s"})  # Re = 5000: formula goes negative


@pytest.mark.parametrize(
    "overrides, family, param",
    [
        ({"length": "0 mm"}, "input.invalid", "length"),
        ({"length": "-1 m"}, "input.invalid", "length"),
        ({"length": "1e308 m"}, "input.out_of_range", "length"),
        ({"length": "50"}, "units.bare_number", "length"),
        ({"length": "50 kg"}, "units.dimension_mismatch", "length"),
        ({"conductivity": "-0.6 W/(m*K)"}, "input.invalid", "conductivity"),
        ({"conductivity": "0.6 W/m"}, "units.dimension_mismatch", "conductivity"),
        ({"density": "0 kg/m^3"}, "input.invalid", "density"),
        ({"dynamic_viscosity": "0 Pa*s"}, "input.invalid", "dynamic_viscosity"),
        ({"dynamic_viscosity": "1e308 Pa*s"}, "input.out_of_range", "dynamic_viscosity"),
        ({"dynamic_viscosity": "1e-3 m^2/s"}, "units.dimension_mismatch", "dynamic_viscosity"),
        ({"velocity": "-1 m/s"}, "input.invalid", "velocity"),
        ({"velocity": "0 m/s"}, "input.invalid", "velocity"),
        ({"velocity": ""}, "input.missing", "velocity"),
        ({"velocity": "1e308 m/s"}, "input.out_of_range", "velocity"),
        ({"specific_heat": "", "prandtl": 0.0}, "input.missing", "specific_heat|prandtl"),
        ({"specific_heat": "", "prandtl": -5.0}, "input.invalid", "prandtl"),
        ({"specific_heat": "-3000 J/(kg*K)"}, "input.invalid", "specific_heat"),
        ({"specific_heat": "3000 J/kg"}, "units.dimension_mismatch", "specific_heat"),
        ({"correlation": "magic"}, "input.invalid", "correlation"),
    ],
)
def test_refused_inputs(overrides, family, param):
    fails(convection, family, param, **{"correlation": "gnielinski", **PIPE, **overrides})


def test_prandtl_and_cp_disagreement_is_flagged():
    r = ok(convection, correlation="gnielinski", **PIPE, prandtl=7.0)
    assert any("differs from cp*mu/k" in w for w in r.warnings)
    assert math.isclose(v(r, "prandtl"), 5.0, rel_tol=1e-12)  # cp*mu/k wins


def test_imperial_output_units():
    metric = ok(convection, correlation="gnielinski", **PIPE)
    imperial = ok(convection, correlation="gnielinski", **PIPE, unit_system="imperial")
    # 1 W/m^2K = 0.1761102 Btu/(h ft^2 degF)
    assert math.isclose(v(imperial, "h"), v(metric, "h") * 0.1761102, rel_tol=1e-6)
    assert imperial.values["h"].unit == "british_thermal_unit / delta_degree_Fahrenheit / foot ** 2 / hour"
