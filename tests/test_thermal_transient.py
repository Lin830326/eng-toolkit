"""thermal.transient checked against hand calculations and the classical one-term constants."""

import math

import pytest

from eng_toolkit.domains.thermal.transient import bessel_j0, bessel_j1, erfcx, first_eigenvalue, one_term, transient
from tests.thermal_testing import fails, ok, v

STEEL = dict(conductivity="15 W/(m*K)", density="7800 kg/m^3", specific_heat="500 J/(kg*K)")

# ---------------------------------------------------------------------------------------------------- special functions
def test_bessel_functions_against_tabulated_values():
    assert math.isclose(bessel_j0(1.0), 0.7651976866, rel_tol=1e-9)
    assert math.isclose(bessel_j1(1.0), 0.4400505857, rel_tol=1e-9)
    assert math.isclose(bessel_j0(2.0), 0.2238907791, rel_tol=1e-9)
    assert math.isclose(bessel_j1(2.0), 0.5767248078, rel_tol=1e-9)
    assert abs(bessel_j0(2.404825557695773)) < 1e-12  # first zero of J0
    assert bessel_j0(0.0) == 1.0 and bessel_j1(0.0) == 0.0


def test_erfcx_matches_exp_times_erfc_and_the_asymptotic_branch():
    assert erfcx(0.0) == 1.0
    assert math.isclose(erfcx(1.0), math.e * 0.1572992070502851, rel_tol=1e-12)  # e * erfc(1) = 0.4275836
    assert math.isclose(erfcx(20.0), math.exp(400.0) * math.erfc(20.0), rel_tol=1e-10)  # series branch meets the direct branch
    assert math.isclose(erfcx(1e3), 1.0 / (1e3 * math.sqrt(math.pi)) * (1 - 1 / (2 * 1e6)), rel_tol=1e-12)  # no overflow


# ---------------------------------------------------------------------------------------------------- eigenvalues
def test_first_eigenvalues_against_textbook_constants():
    # Bi = 1:  plane wall z tan z = 1 -> z1 = 0.8603, C1 = 1.1191 ; cylinder -> z1 = 1.2558, C1 = 1.2071 ; sphere z1 = pi/2, C1 = 4/pi = 1.2732
    z, c = one_term("plane_wall", 1.0)
    assert math.isclose(z, 0.8603336, abs_tol=1e-6) and math.isclose(c, 1.1191, abs_tol=1e-4)
    assert math.isclose(z * math.tan(z), 1.0, rel_tol=1e-12)  # the eigencondition itself
    assert math.isclose(c, 4 * math.sin(z) / (2 * z + math.sin(2 * z)), rel_tol=1e-14)
    z, c = one_term("cylinder", 1.0)
    assert math.isclose(z, 1.2558, abs_tol=1e-4) and math.isclose(c, 1.2071, abs_tol=1e-4)
    assert math.isclose(z * bessel_j1(z) / bessel_j0(z), 1.0, rel_tol=1e-10)
    z, c = one_term("sphere", 1.0)
    assert math.isclose(z, math.pi / 2, rel_tol=1e-13)  # 1 - z cot z = 1 at z = pi/2 (cot = 0)
    assert math.isclose(c, 4.0 / math.pi, rel_tol=1e-13)  # 4 (sin z - z cos z)/(2 z - sin 2z) = 4/pi


def test_eigenvalue_limits_for_tiny_and_huge_biot():
    # Bi -> infinity: pi/2 (wall), 2.404826 (cylinder), pi (sphere) ; C1 -> 4/pi, 2/(z1 J1(z1)) = 2/(2.404826*0.519147) = 1.601975, 2
    assert math.isclose(first_eigenvalue("plane_wall", 1e12), math.pi / 2, rel_tol=1e-9)
    assert math.isclose(first_eigenvalue("cylinder", 1e12), 2.404825557695773, rel_tol=1e-9)
    assert math.isclose(first_eigenvalue("sphere", 1e12), math.pi, rel_tol=1e-9)
    assert math.isclose(one_term("sphere", 1e12)[1], 2.0, rel_tol=1e-9)
    assert math.isclose(one_term("plane_wall", 1e12)[1], 4 / math.pi, rel_tol=1e-9)
    assert math.isclose(one_term("cylinder", 1e12)[1], 1.601975, rel_tol=1e-6)
    # Bi -> 0: z^2 = Bi (wall), 2 Bi (cylinder), 3 Bi (sphere) ; C1 -> 1
    for shape, factor in (("plane_wall", 1.0), ("cylinder", 2.0), ("sphere", 3.0)):
        z, c = one_term(shape, 1e-9)
        assert math.isclose(z * z, factor * 1e-9, rel_tol=1e-6)
        assert math.isclose(c, 1.0, abs_tol=1e-8)


# ---------------------------------------------------------------------------------------------------- lumped
# Body V = 1e-4 m^3, As = 0.03 m^2, steel (rho 7800, cp 500, k 15), h = 200 W/m^2K, 500 degC -> 20 degC fluid:
#   tau = rho V cp/(h As) = 7800 * 1e-4 * 500/(200 * 0.03) = 390/6 = 65 s ;  Lc = V/As = 3.3333e-3 m ; Bi = 200 * 3.3333e-3/15 = 0.04444
#   at t = tau: T = 20 + 480/e = 20 + 176.5821 = 196.5821 degC ;  to reach 100 degC: t = tau ln(480/80) = 65 ln 6 = 116.4644 s
LUMPED = dict(model="lumped", initial_temperature="500 degC", ambient_temperature="20 degC", **STEEL, h="200 W/(m^2*K)", volume="1e-4 m^3", surface_area="0.03 m^2")


def test_lumped_at_one_time_constant_the_difference_is_one_over_e():
    r = ok(transient, **LUMPED, time="65 s")
    assert math.isclose(v(r, "time_constant"), 65.0, rel_tol=1e-12)
    assert math.isclose(v(r, "remaining_fraction"), math.exp(-1.0), rel_tol=1e-12)
    assert math.isclose(v(r, "temperature"), 20.0 + 480.0 / math.e, rel_tol=1e-12)
    assert math.isclose(v(r, "temperature"), 196.5821, abs_tol=1e-4)
    assert math.isclose(v(r, "biot"), 0.0444444, rel_tol=1e-6)
    assert math.isclose(v(r, "characteristic_length"), 1e-4 / 0.03, rel_tol=1e-12)
    # energy given up = rho V cp (Ti - T) = 390 J/K * 303.4179 K ;  heat rate now = h As (Tinf - T) = 6 * (20 - 196.5821)
    assert math.isclose(v(r, "energy_transferred"), 390.0 * (480.0 - 480.0 / math.e), rel_tol=1e-12)
    assert math.isclose(v(r, "heat_rate_now"), 6.0 * (20.0 - 196.5821), rel_tol=1e-6)
    assert not any("Biot" in w for w in r.warnings)


def test_lumped_time_to_a_target_temperature():
    r = ok(transient, **LUMPED, target_temperature="100 degC")
    assert math.isclose(v(r, "time"), 65.0 * math.log(6.0), rel_tol=1e-12)
    assert math.isclose(v(r, "time"), 116.4644, abs_tol=1e-4)
    assert r.value.unit == "second" and math.isclose(v(r, "temperature"), 100.0, abs_tol=1e-9)
    heating = ok(transient, **{**LUMPED, "initial_temperature": "20 degC", "ambient_temperature": "500 degC"}, target_temperature="400 degC")
    assert math.isclose(v(heating, "time"), 65.0 * math.log(480.0 / 100.0), rel_tol=1e-12)  # heating works the same way


def test_lumped_warns_when_biot_exceeds_the_limit():
    # h = 1000: Bi = 1000 * 3.3333e-3/15 = 0.2222 > 0.1
    r = ok(transient, **{**LUMPED, "h": "1000 W/(m^2*K)"}, time="10 s")
    assert any("NOT valid" in w and "0.222" in w for w in r.warnings)
    edge = ok(transient, **{**LUMPED, "h": "450 W/(m^2*K)"}, time="10 s")  # Bi = 0.1 exactly is not below the limit
    assert any("NOT valid" in w for w in edge.warnings)


# ---------------------------------------------------------------------------------------------------- one-term series
# Sphere, k = 10, rho cp = 1e6 (alpha = 1e-5), radius 0.1 m, h = 100 -> Bi = 1, 200 -> 20 degC fluid, initially 200 degC; t = 1000 s -> Fo = 1
#   z1 = pi/2, C1 = 4/pi = 1.273240 ;  theta0 = C1 exp(-z1^2) = 1.273240 * 0.0848 ... exp(-2.467401) = 0.0847999 -> 0.107977
#   centre: 20 + 180 * 0.107977 = 39.4359 ; surface: theta_s = theta0 sin(z1)/z1 = 0.107977 * 0.636620 = 0.0687403 -> 32.3733
#   Q/Q0 = 1 - 3 (sin z1 - z1 cos z1)/z1^3 * theta0 = 1 - 3/3.875785 * 0.107977 = 0.916422 ; Q0 = 4/3 pi 0.1^3 1e6 180 = 753 982 J
SPHERE = dict(model="sphere", initial_temperature="200 degC", ambient_temperature="20 degC", conductivity="10 W/(m*K)", density="1000 kg/m^3", specific_heat="1000 J/(kg*K)", h="100 W/(m^2*K)", size="0.1 m")


def test_sphere_one_term_hand_values_at_biot_one_fourier_one():
    r = ok(transient, **SPHERE, time="1000 s")
    assert math.isclose(v(r, "biot"), 1.0, rel_tol=1e-12) and math.isclose(v(r, "fourier"), 1.0, rel_tol=1e-12)
    assert math.isclose(v(r, "first_eigenvalue"), math.pi / 2, rel_tol=1e-12)
    assert math.isclose(v(r, "c1"), 4 / math.pi, rel_tol=1e-12)
    assert math.isclose(v(r, "theta_ratio"), 0.107977, abs_tol=1e-6)
    assert math.isclose(v(r, "temperature"), 39.4359, abs_tol=1e-3)
    assert math.isclose(v(r, "energy_fraction"), 0.916422, abs_tol=1e-6)
    assert math.isclose(v(r, "energy_transferred"), 0.916422 * 753982.2, rel_tol=1e-6)
    surface = ok(transient, **SPHERE, time="1000 s", position=1.0)
    assert math.isclose(v(surface, "theta_ratio"), 0.0687403, abs_tol=1e-6)
    assert math.isclose(v(surface, "temperature"), 32.3733, abs_tol=1e-3)
    assert math.isclose(v(surface, "centre_temperature"), v(r, "temperature"), rel_tol=1e-12)
    # the surface satisfies the convective boundary condition: -k dT/dr = h (Ts - Tinf) is consistent with Bi: theta_s/theta_0 = sin z/z
    assert math.isclose(v(surface, "theta_ratio") / v(r, "theta_ratio"), math.sin(math.pi / 2) / (math.pi / 2), rel_tol=1e-12)


def test_one_term_time_for_a_target_inverts_the_temperature():
    r = ok(transient, **SPHERE, target_temperature="39.435868 degC")
    assert math.isclose(v(r, "time"), 1000.0, rel_tol=2e-5)
    early = ok(transient, **SPHERE, target_temperature="190 degC")  # centre, 94 % of the step left: Fo = 0.121, series flagged
    assert any("Fourier number" in w for w in early.warnings)
    # at the surface the one-term series never exceeds C1 sin(z)/z = 1.273240 * 0.636620 = 0.810569: a target above that would need Fo < 0
    fails(transient, "input.invalid", "target_temperature", **SPHERE, target_temperature="190 degC", position=1.0)
    fails(transient, "input.invalid", "target_temperature", **SPHERE, target_temperature="10 degC")  # beyond the fluid temperature
    fails(transient, "input.invalid", "target_temperature", **SPHERE, target_temperature="200 degC")  # equal to the initial value


def test_plane_wall_and_cylinder_energy_basis():
    wall = ok(transient, **{**SPHERE, "model": "plane_wall"}, time="1000 s")
    cyl = ok(transient, **{**SPHERE, "model": "cylinder"}, time="1000 s")
    # wall: Bi = 1 -> z = 0.8603336, C1 = 1.1191 ; theta0 = C1 exp(-z^2 * 1) = 1.119 * 0.4772 = 0.5340 ; Q0 per m^2 = 2 * 0.1 * 1e6 * 180 = 3.6e7 J/m^2
    z, c1 = 0.8603336, 4 * math.sin(0.8603336) / (2 * 0.8603336 + math.sin(2 * 0.8603336))
    theta0 = c1 * math.exp(-z * z)
    assert math.isclose(v(wall, "theta_ratio"), theta0, rel_tol=1e-6)
    assert math.isclose(v(wall, "energy_fraction"), 1 - theta0 * math.sin(z) / z, rel_tol=1e-6)
    assert math.isclose(v(wall, "energy_transferred"), 3.6e7 * v(wall, "energy_fraction"), rel_tol=1e-9)
    assert wall.values["energy_transferred"].unit == "joule / meter ** 2"
    assert cyl.values["energy_transferred"].unit == "joule / meter"
    assert math.isclose(v(cyl, "energy_transferred"), math.pi * 0.01 * 1e6 * 180 * v(cyl, "energy_fraction"), rel_tol=1e-9)
    # cylinder centre: J0(0) = 1; position 1 gives J0(z1) < 1
    edge = ok(transient, **{**SPHERE, "model": "cylinder"}, time="1000 s", position=1.0)
    assert math.isclose(v(edge, "theta_ratio"), v(cyl, "theta_ratio") * bessel_j0(v(cyl, "first_eigenvalue")), rel_tol=1e-12)


def test_small_biot_one_term_agrees_with_lumped():
    # sphere r = 10 mm, k = 100, rho cp = 4e6, h = 1: Bi = h r/k = 1e-4 ; lumped tau = rho cp r/(3h) = 4e6 * 0.01/3 = 13 333.3 s
    props = dict(initial_temperature="300 degC", ambient_temperature="20 degC", conductivity="100 W/(m*K)", density="8000 kg/m^3", specific_heat="500 J/(kg*K)", h="1 W/(m^2*K)")
    r_ = 0.01
    lumped = ok(
        transient, model="lumped", **props, volume=f"{4 / 3 * math.pi * r_**3!r} m^3", surface_area=f"{4 * math.pi * r_**2!r} m^2", time="13333.333333333334 s"
    )
    series = ok(transient, model="sphere", **props, size="10 mm", time="13333.333333333334 s")
    assert math.isclose(v(lumped, "remaining_fraction"), math.exp(-1.0), rel_tol=1e-12)
    assert math.isclose(v(series, "theta_ratio"), v(lumped, "remaining_fraction"), rel_tol=5e-4)
    assert math.isclose(v(series, "temperature"), v(lumped, "temperature"), abs_tol=0.05)
    assert any("lumped" in w for w in series.warnings)  # Bi < 0.1: the note that lumped is also valid


def test_fourier_number_warning():
    early = ok(transient, **SPHERE, time="100 s")  # Fo = 0.1
    assert any("Fourier number" in w and "below 0.2" in w for w in early.warnings)
    assert not any("Fourier number" in w for w in ok(transient, **SPHERE, time="1000 s").warnings)
    zero = ok(transient, **SPHERE, time="0 s")
    assert any("Fourier" in w for w in zero.warnings)


# ---------------------------------------------------------------------------------------------------- semi-infinite
# k = 1, rho = cp = 1000 -> alpha = 1e-6 m^2/s ; t = 100 s -> sqrt(alpha t) = 0.01 m ; depth 0.02 m -> eta = x/(2 sqrt(alpha t)) = 1
SEMI = dict(model="semi_infinite", initial_temperature="20 degC", conductivity="1 W/(m*K)", density="1000 kg/m^3", specific_heat="1000 J/(kg*K)", time="100 s")


def test_semi_infinite_imposed_surface_temperature_erf_profile():
    # Ts = 100 degC, Ti = 20 degC: T = Ts + (Ti - Ts) erf(1) = 100 - 80 * 0.8427008 = 32.58394 degC
    r = ok(transient, **SEMI, ambient_temperature="100 degC", depth="2 cm")
    assert math.isclose(v(r, "temperature"), 32.58394, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature"), 100 - 80 * math.erf(1.0), rel_tol=1e-12)
    assert math.isclose(r.data["eta"], 1.0, rel_tol=1e-12)
    # surface flux into the body k (Ts - Ti)/sqrt(pi alpha t) = 80/0.01772454 = 4513.5 W/m^2 ; penetration depth 4 sqrt(alpha t) = 0.04 m
    assert math.isclose(v(r, "surface_heat_flux_into_body"), 80 / math.sqrt(math.pi * 1e-4), rel_tol=1e-12)
    assert math.isclose(v(r, "thermal_penetration_depth"), 0.04, rel_tol=1e-12)
    surface = ok(transient, **SEMI, ambient_temperature="100 degC")
    assert math.isclose(v(surface, "temperature"), 100.0, abs_tol=1e-12)


def test_semi_infinite_contact_temperature_from_effusivities():
    # A: k = 4, rho cp = 1e6 -> e_A = sqrt(4e6) = 2000 ; B: k = 1 -> e_B = 1000 ; T_A = 90, T_B = 30:
    #   Ts = (2000*90 + 1000*30)/3000 = 70 degC ; alpha_A = 4e-6, t = 100 s -> sqrt(alpha t) = 0.02 ; depth 0.04 m -> eta = 1
    #   T = 70 + (90 - 70) erf(1) = 86.85402 ; flux into A = 4 (70 - 90)/sqrt(pi 4e-4) = -2256.76 W/m^2 (A loses heat)
    r = ok(
        transient,
        model="semi_infinite",
        initial_temperature="90 degC",
        ambient_temperature="30 degC",
        conductivity="4 W/(m*K)", density="1000 kg/m^3", specific_heat="1000 J/(kg*K)", time="100 s", depth="4 cm",
        surface_condition="contact", other_temperature="30 degC", other_conductivity="1 W/(m*K)", other_density="1000 kg/m^3", other_specific_heat="1000 J/(kg*K)",
    )
    assert math.isclose(v(r, "surface_temperature"), 70.0, rel_tol=1e-12)
    assert math.isclose(v(r, "temperature"), 86.85402, abs_tol=1e-5)
    assert math.isclose(v(r, "surface_heat_flux_into_body"), -80 / math.sqrt(math.pi * 4e-4), rel_tol=1e-12)
    assert math.isclose(r.data["effusivity_this_W_s05_per_m2_K"], 2000.0, rel_tol=1e-12)
    assert math.isclose(r.data["effusivity_other_W_s05_per_m2_K"], 1000.0, rel_tol=1e-12)
    fails(transient, "input.missing", "other_density", model="semi_infinite", initial_temperature="90 degC", ambient_temperature="30 degC", **{k: SEMI[k] for k in ("conductivity", "density", "specific_heat", "time")}, surface_condition="contact", other_temperature="30 degC", other_conductivity="1 W/(m*K)", other_specific_heat="1000 J/(kg*K)")


def test_semi_infinite_convective_surface_erfc_profile():
    # h = 100 W/m^2K: beta = h sqrt(alpha t)/k = 1.  Tinf = 100, Ti = 20.
    #   surface: (Ts - Ti)/(Tinf - Ti) = 1 - exp(1) erfc(1) = 1 - 2.718282 * 0.1572992 = 0.5724164 -> 65.79331 degC ; flux h (Tinf - Ts) = 3420.67 W/m^2
    #   x = 0.02 m (eta = 1): erfc(1) - exp(hx/k + beta^2) erfc(eta + beta) = 0.1572992 - e^3 * erfc(2) = 0.1572992 - 20.08554 * 0.004677735 = 0.0633444
    #   -> 20 + 80 * 0.0633444 = 25.06755 degC
    r = ok(transient, **SEMI, ambient_temperature="100 degC", surface_condition="convection", h="100 W/(m^2*K)", depth="2 cm")
    assert math.isclose(v(r, "surface_temperature"), 65.79331, abs_tol=1e-5)
    assert math.isclose(v(r, "temperature"), 25.06755, abs_tol=1e-5)
    assert math.isclose(v(r, "surface_heat_flux_into_body"), 100 * (100 - 65.79331), rel_tol=1e-6)
    assert math.isclose(r.data["biot_like_beta"], 1.0, rel_tol=1e-12)
    # h -> infinity recovers the imposed-temperature solution; h -> 0 leaves the body at its initial temperature
    stiff = ok(transient, **SEMI, ambient_temperature="100 degC", surface_condition="convection", h="1e7 W/(m^2*K)", depth="2 cm")
    assert math.isclose(v(stiff, "temperature"), 100 - 80 * math.erf(1.0), abs_tol=1e-3)
    weak = ok(transient, **SEMI, ambient_temperature="100 degC", surface_condition="convection", h="0.01 W/(m^2*K)", depth="2 cm")
    assert math.isclose(v(weak, "temperature"), 20.0, abs_tol=2e-3)


def test_semi_infinite_extremes_stay_finite():
    far = ok(transient, **SEMI, ambient_temperature="100 degC", depth="100 m")
    assert math.isclose(v(far, "temperature"), 20.0, abs_tol=1e-12)
    huge_beta = ok(transient, **{**SEMI, "time": "1e9 s"}, ambient_temperature="100 degC", surface_condition="convection", h="1e7 W/(m^2*K)", depth="1 m")
    assert 20.0 <= v(huge_beta, "temperature") <= 100.0
    t0 = ok(transient, **{**SEMI, "time": "0 s"}, ambient_temperature="100 degC", depth="1 cm")
    assert math.isclose(v(t0, "temperature"), 20.0, abs_tol=1e-12)
    t0c = ok(transient, **{**SEMI, "time": "0 s"}, ambient_temperature="100 degC", surface_condition="convection", h="10 W/(m^2*K)", depth="1 cm")
    assert math.isclose(v(t0c, "temperature"), 20.0, abs_tol=1e-12)


def test_semi_infinite_validity_warning_for_a_thin_body():
    r = ok(transient, **SEMI, ambient_temperature="100 degC", depth="1 cm", body_thickness="3 cm")  # 4 sqrt(alpha t) = 0.04 m > 0.03
    assert any("semi-infinite model is not valid" in w for w in r.warnings)
    assert not any("semi-infinite model is not valid" in w for w in ok(transient, **SEMI, ambient_temperature="100 degC", body_thickness="1 m").warnings)


# ---------------------------------------------------------------------------------------------------- temperatures and refusals
def test_temperature_units_are_absolute_and_a_difference_is_refused():
    a = ok(transient, **LUMPED, time="65 s")
    b = ok(transient, **{**LUMPED, "initial_temperature": "773.15 K", "ambient_temperature": "293.15 K"}, time="65 s")
    c = ok(transient, **{**LUMPED, "initial_temperature": "932 degF", "ambient_temperature": "68 degF"}, time="65 s")
    for other in (b, c):
        assert math.isclose(v(other, "temperature"), v(a, "temperature"), rel_tol=1e-9)
    fails(transient, "input.invalid", "initial_temperature", **{**LUMPED, "initial_temperature": "480 delta_degC"}, time="65 s")
    fails(transient, "input.invalid", "ambient_temperature", **{**LUMPED, "ambient_temperature": "-274 degC"}, time="65 s")
    fails(transient, "units.dimension_mismatch", "ambient_temperature", **{**LUMPED, "ambient_temperature": "20 s"}, time="65 s")
    # degC / degF per unit inside a property is read as a difference (not 1/273)
    per_degc = ok(transient, **{**LUMPED, "specific_heat": "500 J/(kg*degC)", "conductivity": "15 W/(m*degC)"}, time="65 s")
    assert math.isclose(v(per_degc, "temperature"), v(a, "temperature"), rel_tol=1e-12)


@pytest.mark.parametrize(
    "overrides, family, param",
    [
        ({"time": ""}, "input.invalid", "time|target_temperature"),
        ({"time": "5 s", "target_temperature": "100 degC"}, "input.invalid", "time|target_temperature"),
        ({"time": "-5 s"}, "input.invalid", "time"),
        ({"time": "1e308 s"}, "input.out_of_range", "time"),
        ({"time": "5 m"}, "units.dimension_mismatch", "time"),
        ({"time": "5"}, "units.bare_number", "time"),
        ({"model": "slab"}, "input.invalid", "model"),
        ({"time": "5 s", "h": ""}, "input.missing", "h"),
        ({"time": "5 s", "h": "-1 W/(m^2*K)"}, "input.invalid", "h"),
        ({"time": "5 s", "volume": "0 m^3"}, "input.invalid", "volume"),
        ({"time": "5 s", "volume": "1e308 m^3"}, "input.out_of_range", "volume"),
        ({"time": "5 s", "surface_area": "1e-4 m^2"}, "input.invalid", "surface_area|volume"),  # smaller than a sphere's skin
        ({"time": "5 s", "conductivity": "0 W/(m*K)"}, "input.invalid", "conductivity"),
        ({"time": "5 s", "density": "-7800 kg/m^3"}, "input.invalid", "density"),
        ({"time": "5 s", "specific_heat": "500 J/kg"}, "units.dimension_mismatch", "specific_heat"),
    ],
)
def test_lumped_refusals(overrides, family, param):
    args = {**LUMPED, **overrides}
    fails(transient, family, param, **args)


def test_series_refusals():
    fails(transient, "input.invalid", "position", **SPHERE, time="10 s", position=1.5)
    fails(transient, "input.invalid", "position", **SPHERE, time="10 s", position=-0.1)
    fails(transient, "input.missing", "size", **{k: v_ for k, v_ in SPHERE.items() if k != "size"}, time="10 s")
    fails(transient, "input.invalid", "size", **{**SPHERE, "size": "0 mm"}, time="10 s")
    fails(transient, "input.invalid", "target_temperature", **{**SEMI, "time": "", "ambient_temperature": "100 degC"}, target_temperature="50 degC")
    fails(transient, "input.invalid", "surface_condition", **SEMI, ambient_temperature="100 degC", surface_condition="flux")
    fails(transient, "input.missing", "h", **SEMI, ambient_temperature="100 degC", surface_condition="convection")
