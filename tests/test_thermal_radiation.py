"""thermal.radiation checked against the enclosure formulas by hand."""

import math

import pytest

from eng_toolkit.domains.thermal.radiation import radiation
from tests.thermal_testing import fails, ok, v

SIGMA = 5.670374419e-8  # W/(m^2 K^4)

# sigma (T1^4 - T2^4) for 600 K and 300 K: 600^4 = 1.296e11, 300^4 = 8.1e9, difference 1.215e11 -> 5.670374e-8 * 1.215e11 = 6889.505 W/m^2
PLATES = dict(configuration="parallel_plates", temperature_1="600 K", temperature_2="300 K", emissivity_1=0.8, emissivity_2=0.6, area="2 m^2")


def test_parallel_plates_grey_exchange():
    # 1/e1 + 1/e2 - 1 = 1.25 + 1.666667 - 1 = 1.916667 -> q'' = 6889.505/1.916667 = 3594.524 W/m^2, q = 2 m^2 * = 7189.049 W
    r = ok(radiation, **PLATES)
    assert math.isclose(v(r, "heat_rate"), 7189.049, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), 2 * SIGMA * (600.0**4 - 300.0**4) / (1 / 0.8 + 1 / 0.6 - 1), rel_tol=1e-12)
    assert math.isclose(v(r, "heat_flux_surface_1"), 3594.524, rel_tol=1e-6)
    assert math.isclose(v(r, "effective_emissivity"), 1 / 1.916667, rel_tol=1e-6)
    # h_r = q/(A (T1 - T2)) = 3594.524/300 = 11.98175 W/m^2K = sigma e_eff (T1 + T2)(T1^2 + T2^2) = 5.670374e-8 * 0.521739 * 900 * 450000
    assert math.isclose(v(r, "radiation_coefficient"), 11.98175, rel_tol=1e-6)
    assert math.isclose(v(r, "radiation_coefficient"), SIGMA * (1 / 1.9166666666666667) * 900 * 450000, rel_tol=1e-12)
    assert r.value.unit == "watt" and r.data["assumptions"]


def test_black_plates_and_reversed_direction():
    black = ok(radiation, **{**PLATES, "emissivity_1": 1.0, "emissivity_2": 1.0})
    assert math.isclose(v(black, "heat_rate"), 2 * 6889.505, rel_tol=1e-6)  # e_eff = 1/(1 + 1 - 1) = 1
    reverse = ok(radiation, **{**PLATES, "temperature_1": "300 K", "temperature_2": "600 K"})
    assert math.isclose(v(reverse, "heat_rate"), -7189.049, rel_tol=1e-6)  # heat flows from surface 2 to surface 1


@pytest.mark.parametrize("n", [1, 2, 3, 7])
def test_n_shields_with_equal_emissivities_divide_the_exchange_by_n_plus_one(n):
    # all emissivities 0.5: e_eff0 = 1/(2 + 2 - 1) = 1/3 ; with N shields 1/(3 + N * 3) = 1/(3 (N + 1))
    flat = dict(configuration="parallel_plates", temperature_1="600 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.5, area="1 m^2")
    none = ok(radiation, **flat)
    shielded = ok(radiation, **flat, shields=n, shield_emissivity=0.5)
    assert math.isclose(v(none, "heat_rate"), 6889.505 / 3.0, rel_tol=1e-6)  # 2296.502 W
    assert math.isclose(v(shielded, "heat_rate"), v(none, "heat_rate") / (n + 1), rel_tol=1e-12)
    # shield emissivity left out: taken equal to the plates' because those are equal (with a note)
    implied = ok(radiation, **flat, shields=n)
    assert math.isclose(v(implied, "heat_rate"), v(shielded, "heat_rate"), rel_tol=1e-12)
    assert any("shield_emissivity not given" in w for w in implied.warnings)


def test_shield_temperatures_follow_the_fourth_power_drop():
    # one shield, all e equal: Ts^4 = (T1^4 + T2^4)/2 = (1.296e11 + 8.1e9)/2 = 6.885e10 -> Ts = 512.2 K = 239.1 degC
    flat = dict(configuration="parallel_plates", temperature_1="600 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.5, area="1 m^2")
    r = ok(radiation, **flat, shields=1, shield_emissivity=0.5)
    assert math.isclose(v(r, "temperature_shield_1") + 273.15, (6.885e10) ** 0.25, rel_tol=1e-12)
    # three shields: T_k^4 = T1^4 - k/(N+1) * (T1^4 - T2^4)
    r3 = ok(radiation, **flat, shields=3, shield_emissivity=0.5)
    for k in (1, 2, 3):
        assert math.isclose(v(r3, f"temperature_shield_{k}") + 273.15, (1.296e11 - k / 4 * 1.215e11) ** 0.25, rel_tol=1e-12)


def test_shields_with_different_emissivity_the_published_example():
    # e1 = 0.8, e2 = 0.6, two shields es = 0.1: denominator 1.916667 + 2 (20 - 1) = 39.916667 -> q = 2 * 6889.505 / 39.916667 = 345.194 W
    r = ok(radiation, **PLATES, shields=2, shield_emissivity=0.1)
    assert math.isclose(v(r, "heat_rate"), 345.194, rel_tol=1e-5)
    # each shield is isothermal at a temperature between the plates (monotone), and the last gap carries the same heat
    t1, t2 = v(r, "temperature_shield_1") + 273.15, v(r, "temperature_shield_2") + 273.15
    assert 600.0 > t1 > t2 > 300.0
    q_per_area = v(r, "heat_flux_surface_1")
    assert math.isclose(SIGMA * (t2**4 - 300.0**4) / (1 / 0.1 + 1 / 0.6 - 1), q_per_area, rel_tol=1e-9)  # last gap, shield 2 -> plate 2
    assert math.isclose(SIGMA * (600.0**4 - t1**4) / (1 / 0.8 + 1 / 0.1 - 1), q_per_area, rel_tol=1e-9)  # first gap, plate 1 -> shield 1
    fails(radiation, "input.missing", "shield_emissivity", **PLATES, shields=1)  # different plate emissivities: shield emissivity is needed


def test_small_body_in_a_large_enclosure():
    # e = 0.8, A = 0.5 m^2, 400 K body in 300 K surroundings: q = 0.8 * 5.670374e-8 * 0.5 * (2.56e10 - 8.1e9) = 0.4 * 992.3156 = 396.926 W
    r = ok(radiation, configuration="small_body_in_enclosure", temperature_1="400 K", temperature_2="300 K", emissivity_1=0.8, area="0.5 m^2")
    assert math.isclose(v(r, "heat_rate"), 396.926, rel_tol=1e-6)
    assert math.isclose(v(r, "heat_rate"), 0.8 * SIGMA * 0.5 * (400.0**4 - 300.0**4), rel_tol=1e-12)
    assert math.isclose(v(r, "effective_emissivity"), 0.8, rel_tol=1e-14)
    odd = ok(radiation, configuration="small_body_in_enclosure", temperature_1="400 K", temperature_2="300 K", emissivity_1=0.8, emissivity_2=0.3, area="0.5 m^2")
    assert math.isclose(v(odd, "heat_rate"), v(r, "heat_rate"), rel_tol=1e-14)  # the enclosure emissivity drops out
    assert any("emissivity_2 is not used" in w for w in odd.warnings)


def test_concentric_cylinders_and_spheres():
    # cylinders r1 = 50 mm, r2 = 100 mm, L = 2 m, 500 K / 300 K, e1 = 0.5, e2 = 0.8: A1 = 2 pi 0.05 * 2 = 0.628319 m^2
    #   denominator 1/0.5 + 0.5 (1/0.8 - 1) = 2 + 0.125 = 2.125 ; sigma (5e2^4 - 3e2^4) = 5.670374e-8 * 5.44e10 = 3084.683
    #   q = 3084.683 * 0.628319 / 2.125 = 912.077 W
    cyl = ok(radiation, configuration="concentric_cylinders", temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.8, inner_radius="50 mm", outer_radius="100 mm", length="2 m")
    assert math.isclose(v(cyl, "heat_rate"), 912.077, rel_tol=1e-6)
    assert math.isclose(v(cyl, "area_surface_1"), 0.628319, rel_tol=1e-6)
    assert math.isclose(cyl.data["area_ratio_A1_over_A2"], 0.5, rel_tol=1e-14)
    # spheres, same radii: A1 = 4 pi 0.05^2 = 0.0314159 m^2, A1/A2 = 0.25, denominator 2 + 0.25 * 0.25 = 2.0625 -> q = 3084.683 * 0.0314159 / 2.0625 = 46.9858 W
    sph = ok(radiation, configuration="concentric_spheres", temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.8, inner_radius="50 mm", outer_radius="100 mm")
    assert math.isclose(v(sph, "heat_rate"), 46.9858, rel_tol=1e-5)


def test_large_outer_sphere_tends_to_the_small_body_formula():
    big = ok(radiation, configuration="concentric_spheres", temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.3, inner_radius="50 mm", outer_radius="1000 m")
    small = ok(radiation, configuration="small_body_in_enclosure", temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, area=f"{4 * math.pi * 0.05**2!r} m^2")
    assert math.isclose(v(big, "heat_rate"), v(small, "heat_rate"), rel_tol=1e-8)


def test_equal_temperatures_give_zero_heat_and_a_finite_coefficient():
    r = ok(radiation, **{**PLATES, "temperature_2": "600 K"})
    assert v(r, "heat_rate") == 0.0
    # h_r -> 4 sigma e_eff T^3 = 4 * 5.670374e-8 * 0.521739 * 2.16e8 = 25.5616 W/m^2K
    assert math.isclose(v(r, "radiation_coefficient"), 4 * SIGMA * (1 / 1.9166666666666667) * 600.0**3, rel_tol=1e-12)


def test_temperature_units_celsius_fahrenheit_kelvin_are_absolute():
    k = ok(radiation, **PLATES)
    c = ok(radiation, **{**PLATES, "temperature_1": "326.85 degC", "temperature_2": "26.85 degC"})
    f = ok(radiation, **{**PLATES, "temperature_1": "620.33 degF", "temperature_2": "80.33 degF"})
    assert math.isclose(v(c, "heat_rate"), v(k, "heat_rate"), rel_tol=1e-9)
    assert math.isclose(v(f, "heat_rate"), v(k, "heat_rate"), rel_tol=1e-6)
    # a difference or a Celsius number read as kelvin would give a wildly different answer; the difference unit is refused
    fails(radiation, "input.invalid", "temperature_1", **{**PLATES, "temperature_1": "300 delta_degC"})
    imperial = ok(radiation, **PLATES, unit_system="imperial")
    assert imperial.value.unit == "british_thermal_unit / hour"
    assert math.isclose(v(imperial, "heat_rate"), v(k, "heat_rate") * 3.412141156, rel_tol=1e-7)
    assert imperial.values["radiation_coefficient"].unit == "british_thermal_unit / delta_degree_Fahrenheit / foot ** 2 / hour"


@pytest.mark.parametrize(
    "overrides, family, param",
    [
        ({"emissivity_1": 0.0}, "input.invalid", "emissivity_1"),
        ({"emissivity_1": -0.2}, "input.invalid", "emissivity_1"),
        ({"emissivity_1": 1.5}, "input.invalid", "emissivity_1"),
        ({"emissivity_1": 80}, "input.invalid", "emissivity_1"),
        ({"emissivity_2": 0.0}, "input.invalid", "emissivity_2"),
        ({"temperature_1": "0 K"}, "input.invalid", "temperature_1"),
        ({"temperature_1": "-10 K"}, "input.invalid", "temperature_1"),
        ({"temperature_2": "-500 degC"}, "input.invalid", "temperature_2"),
        ({"temperature_1": "1e9 K"}, "input.out_of_range", "temperature_1"),
        ({"temperature_1": "600"}, "units.bare_number", "temperature_1"),
        ({"temperature_1": "600 m"}, "units.dimension_mismatch", "temperature_1"),
        ({"area": ""}, "input.missing", "area"),
        ({"area": "0 m^2"}, "input.invalid", "area"),
        ({"area": "-1 m^2"}, "input.invalid", "area"),
        ({"area": "1e308 m^2"}, "input.out_of_range", "area"),
        ({"area": "2 m"}, "units.dimension_mismatch", "area"),
        ({"shields": -1}, "input.invalid", "shields"),
        ({"shields": 51}, "input.invalid", "shields"),
        ({"shields": 1, "shield_emissivity": 2.0}, "input.invalid", "shield_emissivity"),
        ({"configuration": "view_factor"}, "input.invalid", "configuration"),
    ],
)
def test_refused_inputs(overrides, family, param):
    fails(radiation, family, param, **{**PLATES, **overrides})


def test_geometry_refusals():
    base = dict(temperature_1="500 K", temperature_2="300 K", emissivity_1=0.5, emissivity_2=0.8, inner_radius="50 mm", outer_radius="100 mm", length="2 m")
    fails(radiation, "input.invalid", "outer_radius", configuration="concentric_spheres", **{**base, "outer_radius": "50 mm"})
    fails(radiation, "input.invalid", "outer_radius", configuration="concentric_spheres", **{**base, "outer_radius": "10 mm"})
    fails(radiation, "input.missing", "inner_radius|outer_radius", configuration="concentric_spheres", **{**base, "inner_radius": ""})
    fails(radiation, "input.missing", "length", configuration="concentric_cylinders", **{**base, "length": ""})
    fails(radiation, "input.invalid", "shields", configuration="concentric_spheres", shields=1, **base)
    short = ok(radiation, configuration="concentric_cylinders", **{**base, "length": "0.1 m"})
    assert any("end effects" in w for w in short.warnings)
