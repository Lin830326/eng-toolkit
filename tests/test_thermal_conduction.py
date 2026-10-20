"""thermal.conduction checked against hand calculations (resistance networks)."""

import math
from fractions import Fraction

import pytest

from eng_toolkit.domains.thermal.conduction import conduction
from tests.thermal_testing import fails, ok, v

# ---------------------------------------------------------------------------------------------------- plane wall
# Three layers and two films, area A = 10 m^2, inner 20 degC, outer -5 degC (dT = 25 K):
#   films   1/h:  1/8  = 0.125 ,   1/25 = 0.04            m^2 K/W
#   brick   t/k:  0.2/0.72  = 0.277778                    (0.2 / 0.72 = 5/18)
#   insul.  t/k:  0.1/0.04  = 2.5
#   plaster t/k:  0.02/0.5  = 0.04
#   R'' total = 0.125 + 0.277778 + 2.5 + 0.04 + 0.04 = 2.982778 m^2 K/W,  R_total = R''/A = 0.298278 K/W
#   q'' = 25 / 2.982778 = 8.381449 W/m^2,  q = 83.81449 W,   UA = 1/R = 3.352580 W/K,  U = 1/R'' = 0.335258 W/(m^2 K)
#   temperatures: inner surface 20 - 8.381449*0.125 = 18.952319;  after brick 18.952319 - 8.381449*0.277778 = 16.624139;
#   after insulation 16.624139 - 8.381449*2.5 = -4.329484;  after plaster -4.329484 - 8.381449*0.04 = -4.664742 (outer surface);
#   outer film 8.381449*0.04 = 0.335258 -> -5.000000
WALL = dict(
    geometry="plane",
    layers="200 mm @ 0.72 W/(m*K); 100 mm @ 0.04 W/(m*K); 20 mm @ 0.5 W/(m*K)",
    temperature_inner="20 degC",
    temperature_outer="-5 degC",
    h_inner="8 W/(m^2*K)",
    h_outer="25 W/(m^2*K)",
    area="10 m^2",
)
R_WALL = Fraction(1, 8) + Fraction(5, 18) + Fraction(5, 2) + Fraction(1, 25) + Fraction(1, 25)  # R'' exactly = 2.982778


def test_plane_wall_three_layers_with_films():
    r = ok(conduction, **WALL)
    assert math.isclose(v(r, "total_resistance"), float(R_WALL) / 10.0, rel_tol=1e-12)
    assert math.isclose(v(r, "total_resistance"), 0.298278, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), 83.81449, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), float(Fraction(25) * 10 / R_WALL), rel_tol=1e-12)
    assert math.isclose(v(r, "ua"), 3.352580, rel_tol=1e-6)
    assert math.isclose(v(r, "u"), 0.335258, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_flux"), 8.381449, rel_tol=1e-6)
    assert r.value.unit == "watt" and r.values["total_resistance"].unit == "kelvin / watt"
    assert math.isclose(v(r, "temperature_inner_surface"), 18.952319, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature_interface_1"), 16.624139, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature_interface_2"), -4.329484, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature_outer_surface"), -4.664742, abs_tol=1e-5)
    assert r.values["temperature_inner_surface"].unit == "degree_Celsius"


def test_energy_balance_every_layer_carries_the_same_heat_rate():
    r = ok(conduction, **WALL)
    q = v(r, "heat_rate")
    # q recomputed layer by layer from the reported temperature drops: brick (16.624139 -> -4.329484 is the insulation), etc.
    t = [20.0, v(r, "temperature_inner_surface"), v(r, "temperature_interface_1"), v(r, "temperature_interface_2"), v(r, "temperature_outer_surface"), -5.0]
    resistances = [1 / 8, 0.2 / 0.72, 0.1 / 0.04, 0.02 / 0.5, 1 / 25]
    for j, rr in enumerate(resistances):
        assert math.isclose((t[j] - t[j + 1]) * 10.0 / rr, q, rel_tol=1e-9)
    assert r.data["energy_balance"]["max_relative_difference_between_elements"] < 1e-12
    assert math.isclose(sum(e["fraction_of_total"] for e in r.data["resistances"]), 1.0, rel_tol=1e-12)


def test_no_films_surface_is_at_fluid_temperature_and_default_area_is_one():
    # single layer 0.1 m, k = 0.5: R'' = 0.2 m^2 K/W, dT = 30 K -> q'' = 150 W/m^2 on the default 1 m^2
    r = ok(conduction, geometry="plane", layers="100 mm @ 0.5 W/(m*K)", temperature_inner="50 degC", temperature_outer="20 degC")
    assert math.isclose(v(r, "heat_rate"), 150.0, rel_tol=1e-12)
    assert math.isclose(v(r, "temperature_inner_surface"), 50.0, abs_tol=1e-9)
    assert math.isclose(v(r, "temperature_outer_surface"), 20.0, abs_tol=1e-9)


def test_contact_resistance_adds_a_temperature_jump():
    # two layers 0.1 m / k 1 (0.1 each), contact R''c = 0.05 m^2 K/W between them, no films, dT = 50 K, A = 1:
    #   R'' = 0.1 + 0.05 + 0.1 = 0.25 -> q = 200 W ; jump across the contact = 200*0.05 = 10 K
    #   interface: 100 - 200*0.1 = 80 on the inner side, 70 on the outer side
    r = ok(conduction, geometry="plane", layers="100 mm @ 1 W/(m*K); 100 mm @ 1 W/(m*K)", temperature_inner="100 degC", temperature_outer="50 degC", contact_resistances="0.05 m^2*K/W")
    assert math.isclose(v(r, "heat_rate"), 200.0, rel_tol=1e-12)
    assert math.isclose(v(r, "temperature_interface_1_inner_side"), 80.0, abs_tol=1e-9)
    assert math.isclose(v(r, "temperature_interface_1_outer_side"), 70.0, abs_tol=1e-9)
    assert "temperature_interface_1" not in r.values
    zero = ok(conduction, geometry="plane", layers="100 mm @ 1 W/(m*K); 100 mm @ 1 W/(m*K)", temperature_inner="100 degC", temperature_outer="50 degC", contact_resistances="0 m^2*K/W")
    assert math.isclose(v(zero, "heat_rate"), 250.0, rel_tol=1e-12) and "temperature_interface_1" in zero.values


# ---------------------------------------------------------------------------------------------------- pipe wall with insulation
# Steel pipe r1 = 25 mm, wall 5 mm (r2 = 30 mm, k = 50), insulation 25 mm (r3 = 55 mm, k = 0.04), L = 10 m,
# inside film h = 1000 W/m^2K, outside h = 10, fluid 150 degC inside, 20 degC outside (dT = 130 K):
#   R_film,in  = 1/(1000 * 2 pi 0.025 * 10)       = 1/1570.796 = 6.36620e-4  K/W
#   R_steel    = ln(30/25)/(2 pi 50 * 10)         = 0.1823216/3141.593 = 5.80348e-5
#   R_insul    = ln(55/30)/(2 pi 0.04 * 10)       = 0.6061358/2.513274 = 0.2411738
#   R_film,out = 1/(10 * 2 pi 0.055 * 10)         = 1/34.55752 = 0.02893726
#   R_total = 0.2708057 K/W ;  q = 130/0.2708057 = 480.0490 W (48.0049 W/m)
#   T: after film 149.694391, after steel 149.666532, after insulation = outer surface 33.891304
PIPE = dict(
    geometry="cylinder",
    layers="5 mm @ 50 W/(m*K); 25 mm @ 0.04 W/(m*K)",
    inner_radius="25 mm",
    length="10 m",
    temperature_inner="150 degC",
    temperature_outer="20 degC",
    h_inner="1000 W/(m^2*K)",
    h_outer="10 W/(m^2*K)",
)


def test_pipe_wall_with_insulation_and_films():
    r = ok(conduction, **PIPE)
    assert math.isclose(v(r, "total_resistance"), 0.2708057, rel_tol=1e-6)
    # closed form of the insulation alone, R = ln(r_out/r_in)/(2 pi k L)
    assert math.isclose(math.log(55 / 30) / (2 * math.pi * 0.04 * 10), 0.2411738, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), 480.0490, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate_per_length"), 48.00490, rel_tol=1e-6)
    assert math.isclose(v(r, "temperature_inner_surface"), 149.694391, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature_interface_1"), 149.666532, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature_outer_surface"), 33.891304, abs_tol=1e-5)
    assert math.isclose(v(r, "outer_radius"), 0.055, rel_tol=1e-12)
    # U on the outer surface: 1/(R A_o) = 1/(0.2708057 * 2 pi 0.055 * 10) = 1/0.935852 = 1.068563
    assert math.isclose(v(r, "u_outer"), 1.068563, rel_tol=1e-5)
    assert r.data["energy_balance"]["max_relative_difference_between_elements"] < 1e-12


def test_a_single_cylindrical_layer_matches_ln_formula_exactly():
    # r1 = 10 mm, r2 = 20 mm, k = 2, L = 1 m, dT = 100 K: R = ln 2 / (4 pi) = 0.0551589 K/W, q = 1812.9 W
    r = ok(conduction, geometry="cylinder", layers="10 mm @ 2 W/(m*K)", inner_radius="10 mm", length="1 m", temperature_inner="150 degC", temperature_outer="50 degC")
    assert math.isclose(v(r, "total_resistance"), math.log(2) / (4 * math.pi), rel_tol=1e-12)
    assert math.isclose(v(r, "heat_rate"), 100 * 4 * math.pi / math.log(2), rel_tol=1e-12)
    assert math.isclose(v(r, "heat_rate"), 1812.8, rel_tol=1e-4)


def test_critical_radius_of_insulation_on_a_cylinder():
    # k = 0.2 W/mK insulation, outside h = 10 W/m^2K -> r_crit = k/h = 0.02 m.  Wire r = 1 mm, insulation to R_o, L = 1 m, 100 -> 20 degC:
    #   q(R_o) = 80 / [ ln(R_o/0.001)/(2 pi 0.2) + 1/(10 * 2 pi R_o) ]  (no inner film, copper wire resistance neglected: thin layer k=1e4)
    def q(outer_mm: float) -> float:
        r = ok(
            conduction,
            geometry="cylinder",
            layers=f"{outer_mm - 1} mm @ 0.2 W/(m*K)",
            inner_radius="1 mm",
            length="1 m",
            temperature_inner="100 degC",
            temperature_outer="20 degC",
            h_outer="10 W/(m^2*K)",
        )
        return r.values["heat_rate"].quantity

    expected = lambda ro: 80.0 / (math.log(ro / 0.001) / (2 * math.pi * 0.2) + 1.0 / (10 * 2 * math.pi * ro))  # noqa: E731
    for outer in (2.0, 20.0, 50.0):
        assert math.isclose(q(outer), expected(outer / 1000), rel_tol=1e-12)
    # q peaks at r_crit = 20 mm: more than at 2 mm (thin insulation) and more than at 50 mm
    assert q(20.0) > q(2.0) and q(20.0) > q(50.0)
    # first derivative of the denominator is zero at r = k/h: (1/(2 pi k r) - 1/(10 2 pi r^2)) = 0 -> r = k/h
    r_small = ok(conduction, geometry="cylinder", layers="1 mm @ 0.2 W/(m*K)", inner_radius="1 mm", length="1 m", temperature_inner="100 degC", temperature_outer="20 degC", h_outer="10 W/(m^2*K)")
    assert math.isclose(v(r_small, "critical_radius"), 0.02, rel_tol=1e-12)
    assert any("critical radius" in w and "INCREASES" in w for w in r_small.warnings)
    r_big = ok(conduction, geometry="cylinder", layers="49 mm @ 0.2 W/(m*K)", inner_radius="1 mm", length="1 m", temperature_inner="100 degC", temperature_outer="20 degC", h_outer="10 W/(m^2*K)")
    assert not any("critical radius" in w for w in r_big.warnings)


# ---------------------------------------------------------------------------------------------------- sphere
# Hollow sphere r1 = 0.1 m, r2 = 0.15 m, k = 0.05, outside h = 20, 100 degC inside, 20 degC outside:
#   R_wall = (1/0.1 - 1/0.15)/(4 pi 0.05) = 3.333333/0.6283185 = 5.305165 K/W
#   R_film = 1/(20 * 4 pi 0.15^2) = 1/5.654867 = 0.1768388
#   R = 5.482004 K/W, q = 80/5.482004 = 14.59320 W; outer surface = 20 + 14.5932*0.1768388 = 22.58065
#   r_crit = 2k/h = 2*0.05/20 = 0.005 m
def test_sphere_shell_with_outer_film():
    r = ok(conduction, geometry="sphere", layers="50 mm @ 0.05 W/(m*K)", inner_radius="0.1 m", temperature_inner="100 degC", temperature_outer="20 degC", h_outer="20 W/(m^2*K)")
    assert math.isclose(v(r, "total_resistance"), 5.482004, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), 14.59320, rel_tol=1e-6)
    assert math.isclose(v(r, "temperature_outer_surface"), 22.58065, abs_tol=1e-5)
    assert math.isclose(v(r, "critical_radius"), 0.005, rel_tol=1e-12)
    assert math.isclose(v(r, "u_outer"), 1 / (5.482004 * 4 * math.pi * 0.15**2), rel_tol=1e-6)


def test_imperial_output_units():
    # 1 W = 3600 / 1055.056 = 3.4121412 Btu/h (Btu = 1055.056 J); 20 degC = 68 degF
    metric = ok(conduction, **WALL)
    imperial = ok(conduction, **WALL, unit_system="imperial")
    assert imperial.value.unit == "british_thermal_unit / hour"
    assert math.isclose(imperial.value.quantity, v(metric, "heat_rate") * 3.412141156, rel_tol=1e-7)
    assert math.isclose(v(imperial, "temperature_inner_surface"), 18.952319 * 1.8 + 32, abs_tol=1e-4)
    assert imperial.values["temperature_inner_surface"].unit == "degree_Fahrenheit"
    assert imperial.values["total_resistance"].unit == "delta_degree_Fahrenheit * hour / british_thermal_unit"
    # R in degF h/Btu: 1 K/W = 0.5275280 degF h/Btu
    assert math.isclose(v(imperial, "total_resistance"), v(metric, "total_resistance") * 0.527528, rel_tol=1e-5)


# ---------------------------------------------------------------------------------------------------- temperatures and units
def test_degc_in_a_rate_is_a_difference_not_an_offset_temperature():
    base = ok(conduction, **{**WALL, "layers": "100 mm @ 0.04 W/(m*K)"})
    celsius = ok(conduction, **{**WALL, "layers": "100 mm @ 0.04 W/(m*degC)"})
    assert math.isclose(v(celsius, "heat_rate"), v(base, "heat_rate"), rel_tol=1e-12)  # not 0.04/274.15
    assert any("temperature difference" in w for w in celsius.warnings)
    # 0.04 W/(m*delta_degF) = 0.04 * 1.8 = 0.072 W/(m K)
    fahrenheit = ok(conduction, **{**WALL, "layers": "100 mm @ 0.04 W/(m*degF)"})
    equivalent = ok(conduction, **{**WALL, "layers": "100 mm @ 0.072 W/(m*K)"})
    assert math.isclose(v(fahrenheit, "heat_rate"), v(equivalent, "heat_rate"), rel_tol=1e-12)
    json_layers = ok(conduction, **{**WALL, "layers": '[{"thickness": "100 mm", "conductivity": "0.04 W/(m*degC)"}]'})
    assert math.isclose(v(json_layers, "heat_rate"), v(base, "heat_rate"), rel_tol=1e-12)


def test_temperature_units_are_absolute_and_equivalent():
    a = ok(conduction, **WALL)
    b = ok(conduction, **{**WALL, "temperature_inner": "293.15 K", "temperature_outer": "268.15 K"})
    c = ok(conduction, **{**WALL, "temperature_inner": "68 degF", "temperature_outer": "23 degF"})
    for other in (b, c):
        assert math.isclose(v(other, "heat_rate"), v(a, "heat_rate"), rel_tol=1e-9)


def test_a_difference_unit_is_not_an_absolute_temperature():
    fails(conduction, "input.invalid", "temperature_inner", **{**WALL, "temperature_inner": "20 delta_degC"})
    fails(conduction, "input.invalid", "temperature_outer", **{**WALL, "temperature_outer": "5 delta_degF"})
    fails(conduction, "units.dimension_mismatch", "temperature_inner", **{**WALL, "temperature_inner": "20 mm"})
    fails(conduction, "units.bare_number", "temperature_inner", **{**WALL, "temperature_inner": "20"})
    fails(conduction, "input.invalid", "temperature_inner", **{**WALL, "temperature_inner": "-300 degC"})
    fails(conduction, "input.invalid", "temperature_inner", **{**WALL, "temperature_inner": "0 K"})
    fails(conduction, "input.out_of_range", "temperature_inner", **{**WALL, "temperature_inner": "1e9 K"})


# ---------------------------------------------------------------------------------------------------- refused inputs
@pytest.mark.parametrize(
    "layers, family, param",
    [
        ("0 mm @ 0.72 W/(m*K)", "input.invalid", "layers[1].thickness"),
        ("-5 mm @ 0.72 W/(m*K)", "input.invalid", "layers[1].thickness"),
        ("100 mm @ -0.5 W/(m*K)", "input.invalid", "layers[1].conductivity"),
        ("100 mm @ 0 W/(m*K)", "input.invalid", "layers[1].conductivity"),
        ("100 mm @ 0.5", "units.bare_number", "layers[1].conductivity"),
        ("100 mm @ 0.5 mm", "units.dimension_mismatch", "layers[1].conductivity"),
        ("100 W @ 0.5 W/(m*K)", "units.dimension_mismatch", "layers[1].thickness"),
        ("1e308 m @ 0.5 W/(m*K)", "input.out_of_range", "layers[1].thickness"),
        ("100 mm @ 1e308 W/(m*K)", "input.out_of_range", "layers[1].conductivity"),
        ("100 mm 0.5 W/(m*K)", "input.invalid", "layers"),
        ("", "input.invalid", "layers"),
        ("[]", "input.invalid", "layers"),
        ("[1, 2]", "input.invalid", "layers"),
        ("[{\"thickness\": \"1 mm\"}]", "input.invalid", "layers"),
        ("not json [", "input.invalid", "layers"),
    ],
)
def test_bad_layers_are_refused(layers, family, param):
    fails(conduction, family, param, **{**WALL, "layers": layers})


def test_mismatched_contact_resistance_list_and_other_refusals():
    fails(conduction, "input.invalid", "contact_resistances", **{**WALL, "contact_resistances": "0.001 m^2*K/W"})  # needs 2 for 3 layers
    fails(conduction, "input.invalid", "contact_resistances", **{**WALL, "contact_resistances": "0.001 m^2*K/W; 0; 0"})
    fails(conduction, "input.invalid", "contact_resistances[1]", **{**WALL, "contact_resistances": "-1 m^2*K/W; 0 m^2*K/W"})
    fails(conduction, "units.dimension_mismatch", "contact_resistances[1]", **{**WALL, "contact_resistances": "1 W; 0 m^2*K/W"})
    fails(conduction, "input.invalid", "geometry", **{**WALL, "geometry": "cube"})
    fails(conduction, "input.invalid", "unit_system", **{**WALL, "unit_system": "furlongs"})
    fails(conduction, "input.invalid", "area", **{**WALL, "area": "0 m^2"})
    fails(conduction, "input.out_of_range", "area", **{**WALL, "area": "1e308 m^2"})
    fails(conduction, "input.missing", "inner_radius", **{**PIPE, "inner_radius": ""})
    fails(conduction, "input.invalid", "h_outer", **{**PIPE, "h_outer": "-10 W/(m^2*K)"})
    fails(conduction, "units.dimension_mismatch", "h_outer", **{**PIPE, "h_outer": "10 W/(m*K)"})
    fails(conduction, "input.out_of_range", "layers", **{**WALL, "layers": "; ".join(["1 mm @ 1 W/(m*K)"] * 31), "contact_resistances": ""})


def test_ignored_geometry_parameters_are_reported():
    r = ok(conduction, **{**WALL, "inner_radius": "10 mm"})
    assert any("'inner_radius' is not used" in w for w in r.warnings)
