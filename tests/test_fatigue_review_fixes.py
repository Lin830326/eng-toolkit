"""Regression tests for the nine independent-review findings; each uses the reported failing input."""

import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.fracture import fracture
from eng_toolkit.domains.fatigue.material import material
from eng_toolkit.domains.fatigue.rainflow import rainflow, reversals
from eng_toolkit.domains.fatigue.sn import sn
from tests.fatigue_testing import call, close, ok, v


def refused(tool, param, family=None, **kwargs):
    r = call(tool, **kwargs)
    assert not r.ok
    assert r.error.details["param"] == param, r.error
    if family:
        assert r.error.family == family, r.error
    return r


# 1. A cycle is a count with factor one, never Pint's cycle = turn = 2*pi rad.

COUNT_SPELLINGS = ("cycle", "cycles", "count", "dimensionless", "1")


@pytest.mark.parametrize("unit", COUNT_SPELLINGS)
def test_cycle_count_spellings_in_sn_curve(unit):
    r = ok(sn, mode="curve", basquin_a="1000 MPa", basquin_b=-.1, cycles=f"1e5 {unit}")
    # 1000*(1e5)^-.1 = 1000*10^-.5 = 316.2277660 MPa; the 2*pi defect gave 380.03 MPa.
    close(v(r, "stress_at_life"), 1000 * 10**-.5)
    knee = ok(sn, mode="curve", basquin_a="1000 MPa", basquin_b=-.1, knee_cycles=f"1e6 {unit}")
    # Se = 1000*(1e6)^-.1 = 1000*10^-.6 MPa, knee reported as a plain count.
    close(v(knee, "endurance_limit"), 1000 * 10**-.6)
    close(v(knee, "knee_cycles"), 1e6)
    assert knee.values["knee_cycles"].unit == "count"


@pytest.mark.parametrize("unit", ("rad", "deg", "turn", "revolution", "kilocycle"))
def test_angular_units_are_not_counts(unit):
    refused(sn, "/cycles", "units.dimension_mismatch", mode="curve", basquin_a="1000 MPa", cycles=f"1e5 {unit}")
    refused(rainflow, "/reference_life", "units.dimension_mismatch", history="[0,100,-100,0]", sample_scale="1 MPa",
            basquin_a="1000 MPa", reference_life=f"1e6 {unit}")
    refused(fracture, "/growth_c", "units.dimension_mismatch", mode="growth", crack_size="1 mm", final_crack="10 mm",
            stress_range="100 MPa", growth_c=f"1e-12 m/{unit}/(MPa*m^0.5)^3")


@pytest.mark.parametrize("coefficient", ("1e-12 m/(MPa*m^0.5)^3", "1e-12 m/cycle/(MPa*m^0.5)^3",
                                         "1e-12 m/cycles/(MPa*m^0.5)^3", "1e-12 m/count/(MPa*m^0.5)^3"))
def test_paris_coefficient_with_or_without_per_cycle(coefficient):
    r = ok(fracture, mode="growth", crack_size="1 mm", final_crack="10 mm", stress_range="100 MPa", growth_c=coefficient)
    # Infinite plate, m=3: N=[af^-.5-a0^-.5]/[C*(100*sqrt(pi))^3*(-.5)] = 7,766,344.44 cycles.
    expected = (.01**-.5 - .001**-.5) / (1e-12 * (100 * math.sqrt(math.pi))**3 * -.5)
    close(expected, 7766344.4445, rel=1e-10)
    close(v(r, "cycles_to_limit"), expected, rel=1e-7)
    assert r.values["cycles_to_limit"].unit == "count"


def test_paris_coefficient_rejects_repeated_cycle_factor():
    refused(fracture, "/growth_c", "units.dimension_mismatch", mode="growth", crack_size="1 mm", final_crack="10 mm",
            stress_range="100 MPa", growth_c="1e-12 m/cycle/cycle/(MPa*m^0.5)^3")


def test_imperial_per_cycle_coefficient_is_spelling_independent():
    args = dict(mode="growth", crack_size=".1 inch", final_crack="1 inch", stress_range="10 ksi")
    with_cycle = ok(fracture, **args, growth_c="1e-8 inch/cycle/(ksi sqrt(inch))^3")
    without = ok(fracture, **args, growth_c="1e-8 inch/(ksi sqrt(inch))^3")
    # C_SI = 1e-8*.0254/(6.894757293*sqrt(.0254))^3 m/cycle/(MPa sqrt(m))^3, independent of the /cycle spelling.
    c_si = 1e-8 * .0254 / (6.894757293168361 * math.sqrt(.0254))**3
    ds = 10 * 6.894757293168361
    expected = (.0254**-.5 - .00254**-.5) / (c_si * (ds * math.sqrt(math.pi))**3 * -.5)
    close(v(with_cycle, "cycles_to_limit"), expected, rel=1e-7)
    close(v(without, "cycles_to_limit"), v(with_cycle, "cycles_to_limit"))


@pytest.mark.parametrize("unit", COUNT_SPELLINGS)
def test_rainflow_reference_life_count(unit):
    r = ok(rainflow, history="[0,100,-100,0]", sample_scale="1 MPa", basquin_a="1000 MPa", basquin_b=-.1,
           reference_life=f"1e6 {unit}")
    # Half cycles: amplitudes 50, 100, 50 MPa; N=(Sa/1000)^-10 -> 20^10 and 1e10.
    damage = .5 / 20**10 * 2 + .5 / 1e10
    expected = 2 * 1000 * (1e6 / damage)**-.1
    close(expected, 46.8826, rel=1e-5)
    close(v(r, "equivalent_range"), expected)


# 2. Torsion estimate uses the shear ultimate; the 1e3-cycle anchor depends on the load type.

@pytest.mark.parametrize("load,extra,anchor", [
    ("torsion", {"diameter": "20 mm"}, .9 * .67 * 600),   # .9 Ssu, Ssu=.67*600=402 MPa -> 361.8 MPa
    ("bending", {"diameter": "20 mm"}, .9 * 600),         # 540 MPa
    ("axial", {}, .75 * 600),                             # 450 MPa
])
def test_estimate_anchor_by_load_type(load, extra, anchor):
    r = ok(sn, mode="estimate", ultimate_strength="600 MPa", load_type=load, cycles="1000 cycle", **extra)
    close(v(r, "stress_at_life"), anchor)
    assert r.data["anchor_1000_cycles"]["amplitude_MPa"] == pytest.approx(anchor)


def test_torsion_goodman_uses_shear_ultimate():
    r = ok(sn, mode="estimate", ultimate_strength="600 MPa", load_type="torsion", size_factor=1,
           alternating_stress="100 MPa", mean_stress="100 MPa", mean_method="goodman")
    # Ssu=.67*600=402 MPa; 100/(1-100/402)=133.1126 MPa (the tensile-Su value was 120 MPa).
    close(v(r, "shear_ultimate_strength"), 402)
    close(v(r, "equivalent_amplitude"), 100 / (1 - 100 / 402))
    close(v(r, "equivalent_amplitude"), 133.1126, rel=1e-6)


def test_torsion_caller_shear_ultimate_and_soderberg_shear_yield():
    r = ok(sn, mode="estimate", ultimate_strength="600 MPa", shear_ultimate_strength="480 MPa", load_type="torsion",
           size_factor=1, cycles="1000 cycle", alternating_stress="100 MPa", mean_stress="50 MPa",
           yield_strength="500 MPa", mean_method="soderberg")
    # Anchor .9*480=432 MPa; Ssy=.577*500=288.5 MPa; Soderberg 100/(1-50/288.5).
    close(v(r, "stress_at_life"), 432)
    close(v(r, "shear_yield_strength"), 288.5)
    close(v(r, "equivalent_amplitude"), 100 / (1 - 50 / 288.5))


def test_torsion_refusals():
    base = dict(mode="estimate", ultimate_strength="600 MPa", size_factor=1)
    # Shear peak 150+100=250 MPa exceeds Ssy=.577*400=230.8 MPa although it is below tensile Sy.
    refused(sn, "/alternating_stress", **base, load_type="torsion", alternating_stress="150 MPa", mean_stress="100 MPa",
            yield_strength="400 MPa")
    refused(sn, "/shear_ultimate_strength", **base, load_type="torsion", shear_ultimate_strength="700 MPa")
    refused(sn, "/shear_ultimate_strength", **base, shear_ultimate_strength="400 MPa")
    refused(sn, "/shear_ultimate_strength", mode="curve", basquin_a="1000 MPa", shear_ultimate_strength="400 MPa")


# 3. Basquin fit: ASTM E739 regresses log N on log S by default.

POINTS = ("400 MPa,1000;400 MPa,3000;300 MPa,2e4;300 MPa,8e4;250 MPa,1e5;250 MPa,5e5;"
          "200 MPa,1e6;200 MPa,4e6;150 MPa,1e7,runout")
STRESS = np.array([400, 400, 300, 300, 250, 250, 200, 200.])
LIFE = np.array([1e3, 3e3, 2e4, 8e4, 1e5, 5e5, 1e6, 4e6])


def test_e739_default_fit():
    r = ok(sn, mode="fit", points=POINTS, alternating_stress="250 MPa")
    slope, intercept = np.polyfit(np.log10(STRESS), np.log10(LIFE), 1)
    residual = np.log10(LIFE) - (intercept + slope * np.log10(STRESS))
    # S=A*N^b with b=1/slope, A=10^(-intercept/slope); runout excluded.
    close(v(r, "basquin_b"), 1 / slope)
    close(v(r, "basquin_a"), 10**(-intercept / slope))
    close(v(r, "basquin_a"), 841.1744, rel=1e-6)
    close(v(r, "basquin_b"), -.0985778, rel=1e-6)
    close(v(r, "life"), 10**(intercept + slope * math.log10(250)))
    close(v(r, "life"), 2.215566e5, rel=1e-6)
    close(r.data["fit"]["standard_error_log10_life"], math.sqrt(residual @ residual / 6))
    assert r.data["fit"]["method"] == "e739" and len(r.data["excluded_runouts"]) == 1


def test_s_on_n_fit_option():
    r = ok(sn, mode="fit", points=POINTS, alternating_stress="250 MPa", fit_method="s_on_n")
    slope, intercept = np.polyfit(np.log10(LIFE), np.log10(STRESS), 1)
    residual = np.log10(STRESS) - (intercept + slope * np.log10(LIFE))
    close(v(r, "basquin_b"), slope)
    close(v(r, "basquin_a"), 780.1168, rel=1e-6)
    close(v(r, "basquin_b"), -.0918616, rel=1e-5)
    close(v(r, "life"), 2.399080e5, rel=1e-6)
    close(r.data["fit"]["standard_error_log10_stress"], math.sqrt(residual @ residual / 6))


def test_fit_method_refusals():
    refused(sn, "/fit_method", mode="fit", points=POINTS, fit_method="ols")
    refused(sn, "/fit_method", mode="curve", basquin_a="1000 MPa", fit_method="s_on_n")
    # Equal lives at two stresses give zero log N slope: no decreasing curve.
    refused(sn, "/points", mode="fit", points="100 MPa,1000;200 MPa,1000")


# 4. Single-edge net section carries the free-end bending moment.

def test_single_edge_net_section_includes_bending():
    args = dict(geometry="edge", width="0.1 m", crack_size="50 mm", stress="120 MPa")
    # Membrane 120*.1/.05=240 MPa; peak 240*(1+3*.05/.05)=960 MPa > .8*400.
    refused(fracture, "/yield_strength", **args, yield_strength="400 MPa")
    r = ok(fracture, **args, yield_strength="1500 MPa")
    close(v(r, "net_section_membrane_stress"), 240)
    close(v(r, "net_section_stress"), 960)


def test_symmetric_sections_have_no_bending():
    r = ok(fracture, geometry="double_edge", width="100 mm", crack_size="10 mm", stress="100 MPa")
    close(v(r, "net_section_stress"), 100 / .8)
    close(v(r, "net_section_membrane_stress"), 100 / .8)


def test_surface_net_section_against_strip_integration():
    # a/c=.4375, a/t=.7, 2c/W=.4: inside the Newman-Raju limits.
    a, c, t, w, s = .0035, .008, .005, .04, 100.
    r = ok(fracture, geometry="surface", crack_size=f"{a} m", surface_half_length=f"{c} m", width=f"{w} m",
           thickness=f"{t} m", stress=f"{s} MPa")
    # Independent midpoint strips through the thickness, y from the cracked face.
    y = (np.arange(400000) + .5) * t / 400000
    depth = np.clip(y / a, 0, 1)
    strip = np.where(y < a, w - 2 * c * np.sqrt(1 - depth**2), w) * (t / 400000)
    area = strip.sum()
    centroid = (strip * y).sum() / area
    inertia = (strip * (y - centroid)**2).sum()
    force = s * w * t
    peak = force / area + force * (centroid - t / 2) * centroid / inertia
    close(v(r, "net_section_membrane_stress"), force / area, rel=1e-6)
    close(v(r, "net_section_stress"), peak, rel=1e-5)
    assert v(r, "net_section_stress") > v(r, "net_section_membrane_stress")


# 5. Larson-Miller parameter is a temperature scale: K and degR only.

@pytest.mark.parametrize("parameter", ["32008.41 degF", "17509.3 degC", "17509.3 celsius", "17509.3 delta_degC"])
def test_larson_parameter_refuses_offset_units(parameter):
    refused(material, "/larson_parameter", "units.dimension_mismatch", mode="larson_miller", temperature="500 degC",
            larson_parameter=parameter)


@pytest.mark.parametrize("parameter", ["32008.41 degR", "17782.45 K"])
def test_larson_parameter_scale_units(parameter):
    r = ok(material, mode="larson_miller", temperature="500 degC", larson_parameter=parameter)
    # 32008.41 degR*5/9=17782.45 K=773.15*(20+3) -> 1000 h.
    close(v(r, "larson_miller_parameter"), 17782.45)
    close(v(r, "exposure_time"), 1000)


# 6. sn_points obeys the same b>=-1 limit as basquin_b.

def test_sn_points_slope_limit():
    args = dict(history="[0,100,-100,0]", sample_scale="1 MPa", endurance_limit="200 MPa")
    # log10(31.6227766/1000)/log10(10) = -1.5 < -1.
    refused(rainflow, "/sn_points", "input.out_of_range", **args, sn_points="1000 MPa,1;31.6227766 MPa,10", below_knee="haibach")
    refused(rainflow, "/basquin_b", "input.out_of_range", **args, basquin_a="1000 MPa", basquin_b=-1.5, below_knee="haibach")
    # b=-1 exactly is accepted on both paths: m=1, Haibach 2m-1=1 equals elementary.
    pts = ok(rainflow, **args, sn_points="1000 MPa,1;100 MPa,10", below_knee="haibach")
    direct = ok(rainflow, **args, basquin_a="1000 MPa", basquin_b=-1, below_knee="haibach")
    close(v(pts, "damage_per_block"), v(direct, "damage_per_block"))


def test_sn_points_errors_point_at_sn_points():
    refused(rainflow, "/sn_points/1/stress", history="[0,100,-100,0]", sample_scale="1 MPa", sn_points="400 MPa,1000;200 kg,1e6")


# 7. Hysteresis gate keeps the true first reversal of the undetermined phase.

def test_gate_initial_valley():
    assert reversals([5, 3.5, 10], 2.0) == [5, 3.5, 10]
    r = ok(rainflow, history="[5,3.5,10]", sample_scale="1 MPa", hysteresis="2 MPa")
    # Half cycles 1.5 (endpoint excursion retained) and 6.5: weighted 1, sum(range*count)=(1.5+6.5)/2.
    close(v(r, "weighted_cycles"), 1)
    close(v(r, "range_count_sum"), 4)


def gate_reference(x, h):
    """Brute-force h-significant extrema. A peak needs, on each side, a fall of at least h before
    the signal exceeds it; one side may instead be the record boundary when the other side is real."""
    n = len(x)

    def side(i, sign, indices):
        for k in indices:
            if sign * (x[i] - x[k]) >= h:
                return True
            if sign * (x[k] - x[i]) > 0:
                return False
        return False

    def boundary(i, sign, indices):
        return all(sign * (x[i] - x[k]) >= 0 for k in indices)

    out = [x[0]]
    for i in range(n):
        for sign in (1, -1):
            left, right = range(i - 1, -1, -1), range(i + 1, n)
            real_l, real_r = side(i, sign, left), side(i, sign, right)
            if (real_l or (boundary(i, sign, left) and real_r)) and (real_r or (boundary(i, sign, right) and real_l)):
                out.append(x[i])
    out.append(x[-1])
    return [value for j, value in enumerate(out) if j == 0 or value != out[j - 1]]


def test_gate_against_brute_force_reference():
    rng = np.random.default_rng(7)
    for trial in range(1500):
        n = int(rng.integers(2, 26))
        x = (rng.integers(-12, 13, n) if trial % 2 else np.round(rng.normal(0, 5, n), 2)).astype(float).tolist()
        h = float(rng.choice([.5, 1, 2, 3, 4.5, 7, 12]))
        assert reversals(x, h) == gate_reference(x, h), (x, h)
    for x, h in [([5, 3.5, 10], 2.), ([5, 6, 3.5, 10], 2.), ([5, 3.5, 6, 3, 10], 2.), ([0, 1, .5], 2.), ([0, 10, 9, 11, 0], 2.)]:
        assert reversals(x, h) == gate_reference(x, h), (x, h)


def test_gate_rainflow_history_with_reference():
    rng = np.random.default_rng(11)
    for _ in range(40):
        x = rng.integers(-50, 51, 40).tolist()
        if max(x) == min(x):
            continue
        filtered = gate_reference([float(s) for s in x], 9.)
        if len(filtered) < 2 or max(filtered) == min(filtered):
            continue
        r = ok(rainflow, history=json.dumps(x), sample_scale="1 MPa", hysteresis="9 MPa")
        close(v(r, "filtered_total_variation"), sum(abs(b - a) for a, b in zip(filtered, filtered[1:])))


# 8. Irwin radius r_y versus plastic zone size r_p=2 r_y.

def test_plastic_zone_size_is_twice_irwin_radius():
    # Reported case K=34.557 MPa sqrt(m), Sy=500 MPa: infinite plate a=K^2/(pi*100^2) under 100 MPa.
    a = 34.557**2 / (math.pi * 1e4)
    r = ok(fracture, crack_size=f"{a!r} m", stress="100 MPa", yield_strength="500 MPa")
    close(v(r, "stress_intensity"), 34.557)
    # rp=(34.557/500)^2/pi=1.5205 mm (the old output labelled this zone .7602 mm, which is r_y).
    rp = (34.557 / 500)**2 / math.pi
    close(rp, .0015205, rel=1e-4)
    close(v(r, "rp_plane_stress"), rp)
    close(v(r, "ry_plane_stress"), rp / 2)
    close(v(r, "rp_plane_strain"), rp / 3)
    close(v(r, "ry_plane_strain"), rp / 6)
    assert "plastic_zone_plane_stress" not in r.values


# 9. Peterson's steel constant is limited to Dowling's Su>=550 MPa.

def test_peterson_automatic_constant_range():
    args = dict(mode="notch", kt=2, notch_radius="1 mm")
    refused(sn, "/ultimate_strength", "input.out_of_range", **args, notch_method="peterson", ultimate_strength="500 MPa")
    r = ok(sn, **args, notch_method="peterson", ultimate_strength="550 MPa")
    # a=.0254*(2070/550)^1.8 mm; q=1/(1+a/1 mm).
    close(v(r, "notch_sensitivity"), 1 / (1 + .0254 * (2070 / 550)**1.8))
    # Shigley's Neuber fit keeps its own 345 MPa lower bound.
    neuber = ok(sn, **args, notch_method="neuber", ultimate_strength="500 MPa")
    root_a = 1.24 - 2.25e-3 * 500 + 1.60e-6 * 500**2 - 4.11e-10 * 500**3
    close(v(neuber, "notch_sensitivity"), 1 / (1 + root_a))
    # A measured constant bypasses the correlation at any strength.
    ok(sn, **args, notch_method="peterson", notch_material_length=".1 mm")
