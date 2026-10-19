"""mech.bolt_check checked against hand calculations."""

import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.mech.fasteners import bolt_check, class_strengths_mpa, tensile_stress_area_mm2

LBF_N = 4.4482216152605  # newton per pound-force (definition)
LBF_FT_NM = 1.3558179483314  # N m per lbf ft
PSI_PA = 6894.757293168


def _call(**kwargs) -> ToolResult:
    fn = getattr(bolt_check, "fn", bolt_check)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    assert result.review_status == "unreviewed"
    return result


def _fails(family: str, param: str | None = None, **kwargs) -> ToolResult:
    r = _call(**kwargs)
    assert r.ok is False and r.value is None
    assert r.error.family == family, r.error
    if param is not None:
        assert r.error.details.get("param") == param, r.error
    assert r.warnings == []
    return r


# Hand calculation, M10 (coarse pitch 1.5 mm), class 8.8, tightening torque 40 N m, K = 0.2:
#   A_s  = pi/4 (d - 0.9382 P)^2 = pi/4 (10 - 1.4073)^2 = pi/4 * 8.5927^2 = pi/4 * 73.8344 = 57.9895 mm^2
#   F_i  = T / (K d) = 40 / (0.2 * 0.010) = 20 000 N
#   Rp   = 10 * 8 * 8 = 640 MPa ; Sp = 0.9 * 640 = 576 MPa (estimate) ; F_p = 576 * 57.9895 = 33 401.9 N
#   preload utilization = 20 000 / 33 401.9 = 0.59877 ; stress = 20 000 / 57.9895 = 344.89 MPa
AS_M10 = 57.98947543
FP_M10 = 576.0 * AS_M10
BASE = dict(diameter="10 mm", torque="40 N*m")


def test_tensile_stress_area_against_published_catalogue_values():
    # Catalogue values of the ISO coarse series: M8 36.6, M10 58.0, M12 84.3, M16 157, M20 245, M24 353 mm^2
    for d, pitch, published in [(8, 1.25, 36.6), (10, 1.5, 58.0), (12, 1.75, 84.3), (16, 2.0, 157.0), (20, 2.5, 245.0), (24, 3.0, 353.0)]:
        assert math.isclose(tensile_stress_area_mm2(d, pitch), published, rel_tol=0.003), (d, tensile_stress_area_mm2(d, pitch))
    # fine thread M10x1.25: pi/4 (10 - 1.17275)^2 = pi/4 * 77.9203 = 61.198 mm^2
    assert math.isclose(tensile_stress_area_mm2(10, 1.25), 61.1985, rel_tol=1e-5)


def test_class_designation_gives_nominal_strengths():
    # a.b -> Rm = 100 a, Rp = 10 a b
    assert class_strengths_mpa("8.8") == (800.0, 640.0)
    assert class_strengths_mpa("10.9") == (1000.0, 900.0)
    assert class_strengths_mpa("12.9") == (1200.0, 1080.0)
    assert class_strengths_mpa("4.6") == (400.0, 240.0)


def test_torque_to_preload_and_utilization_match_hand_calc():
    r = _call(**BASE)
    assert r.ok, r.error
    assert r.data["solved_for"] == "preload"
    assert r.data["pitch_mm"] == 1.5 and r.data["pitch_source"] == "ISO coarse series"
    assert math.isclose(r.values["preload"].quantity, 20.0, rel_tol=1e-12)
    assert r.values["preload"].unit == "kilonewton"
    assert math.isclose(r.values["tensile_stress_area"].quantity, AS_M10, rel_tol=1e-9)
    assert math.isclose(r.values["proof_load"].quantity, FP_M10 / 1000.0, rel_tol=1e-9)  # 33.402 kN
    assert math.isclose(r.values["preload_utilization"].quantity, 20000.0 / FP_M10, rel_tol=1e-9)  # 0.59877
    assert math.isclose(r.value.quantity, 0.59877, rel_tol=1e-4)
    assert math.isclose(r.values["bolt_stress"].quantity, 344.8902, rel_tol=1e-6)
    assert math.isclose(r.values["proof_safety_factor"].quantity, FP_M10 / 20000.0, rel_tol=1e-9)
    assert any("estimated 0.9 x nominal yield" in w for w in r.warnings)
    assert any("Axial tension only" in w for w in r.warnings)


def test_preload_to_torque_is_the_inverse():
    # T = K F d = 0.2 * 20 000 * 0.010 = 40 N m
    r = _call(diameter="10 mm", preload="20 kN")
    assert r.ok, r.error
    assert r.data["solved_for"] == "tightening_torque"
    assert math.isclose(r.values["tightening_torque"].quantity, 40.0, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 20000.0 / FP_M10, rel_tol=1e-9)


def test_given_proof_strength_replaces_the_estimate():
    # Sp = 580 MPa -> F_p = 580 * 57.9895 = 33 633.9 N ; utilization = 20 000 / 33 633.9 = 0.59464
    r = _call(**BASE, proof_strength="580 MPa")
    assert r.ok, r.error
    assert math.isclose(r.values["proof_load"].quantity, 33.6339, rel_tol=1e-6)
    assert math.isclose(r.value.quantity, 0.59464, rel_tol=1e-4)
    assert r.data["proof_strength_source"] == "given"
    assert not any("estimated" in w for w in r.warnings)


def test_external_load_share():
    # F_b = F_i + C P = 20 + 0.3 * 8 = 22.4 kN ; utilization = 22 400 / 33 401.9 = 0.67062
    r = _call(**BASE, external_load="8 kN", load_share=0.3)
    assert r.ok, r.error
    assert math.isclose(r.values["bolt_load"].quantity, 22.4, rel_tol=1e-12)
    assert math.isclose(r.value.quantity, 22400.0 / FP_M10, rel_tol=1e-9)
    assert math.isclose(r.values["preload_utilization"].quantity, 20000.0 / FP_M10, rel_tol=1e-9)  # preload part unchanged
    assert not any("load_share = 1" in w for w in r.warnings)
    # default share 1.0: all 8 kN on the bolt, with a warning
    r = _call(**BASE, external_load="8 kN")
    assert math.isclose(r.values["bolt_load"].quantity, 28.0, rel_tol=1e-12)
    assert any("load_share = 1" in w for w in r.warnings)


def test_fine_pitch_and_nut_factor():
    # M10x1.25, K = 0.15, T = 30 N m -> F = 30 / (0.15 * 0.010) = 20 000 N ; A_s = 61.1985 mm^2
    r = _call(diameter="10 mm", pitch="1.25 mm", torque="30 N*m", nut_factor=0.15)
    assert r.ok, r.error
    assert r.data["pitch_source"] == "given"
    assert math.isclose(r.values["preload"].quantity, 20.0, rel_tol=1e-12)
    assert math.isclose(r.values["tensile_stress_area"].quantity, 61.1985, rel_tol=1e-5)


def test_other_property_class():
    # 10.9: Rp = 900, Sp = 810 MPa -> F_p = 810 * 57.9895 = 46 971.5 N
    r = _call(**BASE, property_class="10.9")
    assert math.isclose(r.values["proof_load"].quantity, 0.9 * 900 * AS_M10 / 1000.0, rel_tol=1e-9)
    assert r.data["tensile_strength_mpa"] == 1000.0


def test_unit_conversions_in_and_out():
    # 40 N m = 29.50249 lbf ft ; 0.3937008 in = 10 mm
    r = _call(diameter=f"{10 / 25.4} inch", torque=f"{40 / LBF_FT_NM} lbf*ft", unit_system="imperial")
    assert r.ok, r.error
    assert r.values["preload"].unit == "force_pound"
    assert math.isclose(r.values["preload"].quantity, 20000.0 / LBF_N, rel_tol=1e-9)  # 4496.179 lbf
    assert math.isclose(r.values["bolt_stress"].quantity, 344.89017e6 / PSI_PA / 1000.0, rel_tol=1e-8)  # 50.022 ksi
    assert math.isclose(r.values["tensile_stress_area"].quantity, AS_M10 / 645.16, rel_tol=1e-9)  # 0.089884 in^2
    assert math.isclose(r.values["tightening_torque"].quantity, 40.0 / LBF_FT_NM, rel_tol=1e-9)
    # preload given in lbf and psi-based proof strength
    r = _call(diameter="10 mm", preload=f"{20000 / LBF_N} lbf", proof_strength=f"{580e6 / PSI_PA} psi")
    assert math.isclose(r.value.quantity, 0.59464, rel_tol=1e-4)


def test_warnings_over_proof_low_preload_and_nut_factor():
    # T = 80 N m -> F = 40 kN -> 40 000 / 33 401.9 = 1.1975 (> 1)
    r = _call(diameter="10 mm", torque="80 N*m")
    assert r.ok and math.isclose(r.value.quantity, 1.19754, rel_tol=1e-4)
    assert any("permanent elongation" in w for w in r.warnings)
    # T = 70 N m -> F = 35 kN -> 1.0478 ; also above 90 % of proof
    r = _call(diameter="10 mm", torque="70 N*m")
    assert any("90 %" in w for w in r.warnings)
    # T = 10 N m -> F = 5 kN -> 0.1497 < 0.25
    r = _call(diameter="10 mm", torque="10 N*m")
    assert any("only 15% of the proof load" in w for w in r.warnings)
    # K = 0.4 outside 0.1-0.3
    r = _call(diameter="10 mm", torque="40 N*m", nut_factor=0.4)
    assert any("nut factor K = 0.4" in w for w in r.warnings)
    # not a tabulated size (e.g. an inch thread): stress-area formula caveat
    r = _call(diameter="12.7 mm", pitch="1.954 mm", torque="60 N*m")
    assert r.ok and any("not one of the tabulated ISO coarse sizes" in w for w in r.warnings)


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"torque": "", "preload": ""}, "input.missing", None),
        ({"preload": "20 kN"}, "input.invalid", None),  # both torque and preload
        ({"diameter": "10"}, "units.bare_number", "diameter"),
        ({"diameter": 10}, "units.bare_number", "diameter"),
        ({"diameter": "0 mm"}, "input.invalid", "diameter"),
        ({"diameter": "-10 mm"}, "input.invalid", "diameter"),
        ({"diameter": "10 N"}, "units.dimension_mismatch", "diameter"),
        ({"diameter": "10 bolts"}, "units.parse_error", "diameter"),
        ({"diameter": "1000 mm"}, "input.out_of_range", "diameter"),
        ({"diameter": "0.1 mm"}, "input.out_of_range", "diameter"),
        ({"diameter": "11 mm"}, "input.invalid", "pitch"),  # M11 is not a tabulated size: pitch is required
        ({"pitch": "5 mm"}, "input.invalid", "pitch"),  # pitch >= d/2
        ({"pitch": "0 mm"}, "input.invalid", "pitch"),
        ({"pitch": "1.5 N"}, "units.dimension_mismatch", "pitch"),
        ({"torque": "0 N*m"}, "input.invalid", "torque"),
        ({"torque": "-40 N*m"}, "input.invalid", "torque"),
        ({"torque": "40 N"}, "units.dimension_mismatch", "torque"),
        ({"torque": "1e12 N*m"}, "input.out_of_range", "torque"),
        ({"torque": "", "preload": "0 kN"}, "input.invalid", "preload"),
        ({"torque": "", "preload": "20 N*m"}, "units.dimension_mismatch", "preload"),
        ({"torque": "", "preload": "1e12 kN"}, "input.out_of_range", "preload"),
        ({"property_class": "13.9"}, "input.invalid", "property_class"),
        ({"property_class": "strong"}, "input.invalid", "property_class"),
        ({"nut_factor": 0.0}, "input.invalid", "nut_factor"),
        ({"nut_factor": 2.0}, "input.invalid", "nut_factor"),
        ({"nut_factor": "0.2"}, "input.invalid", "nut_factor"),
        ({"external_load": "-1 kN"}, "input.invalid", "external_load"),
        ({"external_load": "8 mm"}, "units.dimension_mismatch", "external_load"),
        ({"load_share": 1.5}, "input.invalid", "load_share"),
        ({"load_share": -0.1}, "input.invalid", "load_share"),
        ({"proof_strength": "580 mm"}, "units.dimension_mismatch", "proof_strength"),
        ({"proof_strength": "0 MPa"}, "input.invalid", "proof_strength"),
        ({"proof_strength": "1e9 MPa"}, "input.out_of_range", "proof_strength"),
        ({"unit_system": "cubits"}, "input.invalid", "unit_system"),
    ],
)
def test_bolt_check_invalid_inputs(override, family, param):
    _fails(family, param, **{**BASE, **override})
