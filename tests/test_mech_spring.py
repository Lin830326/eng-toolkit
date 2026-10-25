"""mech.spring_compression checked against hand calculations."""

from eng_toolkit.errors import json_pointer
import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.mech.springs import spring_compression, wahl_factor

PSI_PA = 6894.757293168
LBF_N = 4.4482216152605
INCH_MM = 25.4


def _call(**kwargs) -> ToolResult:
    fn = getattr(spring_compression, "fn", spring_compression)
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
        assert r.error.details.get("param") == json_pointer(param), r.error
    assert r.warnings == []
    return r


# Hand calculation: d = 2 mm, D = 16 mm, Na = 8, G = 79.3 GPa = 79 300 N/mm^2, squared and ground ends
#   C   = D/d = 8
#   k   = G d^4 / (8 D^3 Na) = 79 300 * 16 / (8 * 4096 * 8) = 1 268 800 / 262 144 = 4.840088 N/mm
#   Kw  = (4C-1)/(4C-4) + 0.615/C = 31/28 + 0.076875 = 1.107143 + 0.076875 = 1.184018
#   Nt  = Na + 2 = 10 ; Hs = Nt d = 20 mm
#   F = 100 N: y = F/k = 20.6608 mm ; tau = Kw 8 F D / (pi d^3) = 1.184018 * 12 800 / 25.13274 = 1.184018 * 509.2958 = 603.015 MPa
#   L0 = 50 mm: stroke to solid = 30 mm ; F_solid = 4.840088 * 30 = 145.2026 N ; tau_solid = 603.015 * 1.452026 = 875.59 MPa
BASE = dict(wire_diameter="2 mm", mean_diameter="16 mm", active_coils=8)
K_N_MM = 79300.0 * 16.0 / (8.0 * 4096.0 * 8.0)


def test_wahl_factor_formula():
    # C = 8: 31/28 + 0.615/8 ; C = 4: 15/12 + 0.615/4 = 1.25 + 0.15375 = 1.40375 ; C = 6: 23/20 + 0.1025 = 1.2525
    assert math.isclose(wahl_factor(8), 1.184018, rel_tol=1e-6)
    assert math.isclose(wahl_factor(4), 1.40375, rel_tol=1e-12)
    assert math.isclose(wahl_factor(6), 1.2525, rel_tol=1e-12)


def test_geometry_only():
    r = _call(**BASE)
    assert r.ok, r.error
    assert r.value.unit == "newton / millimeter"
    assert math.isclose(r.value.quantity, 4.840088, rel_tol=1e-6)
    assert math.isclose(r.value.quantity, K_N_MM, rel_tol=1e-12)
    assert r.values["spring_index"].quantity == 8.0
    assert math.isclose(r.values["wahl_factor"].quantity, 1.184018, rel_tol=1e-6)
    assert r.values["total_coils"].quantity == 10.0
    assert math.isclose(r.values["solid_height"].quantity, 20.0, rel_tol=1e-12)
    assert "shear_stress" not in r.values and "stroke_to_solid" not in r.values
    assert any("Static loading" in w for w in r.warnings)


def test_load_gives_deflection_and_wahl_corrected_stress():
    r = _call(**BASE, load="100 N")
    assert r.ok, r.error
    assert math.isclose(r.values["deflection"].quantity, 20.6608, rel_tol=1e-6)
    assert math.isclose(r.values["shear_stress"].quantity, 603.015, rel_tol=1e-6)
    assert r.values["shear_stress"].unit == "megapascal"
    assert math.isclose(r.values["load"].quantity, 100.0, rel_tol=1e-12)


def test_deflection_gives_load():
    # y = 10 mm -> F = k y = 48.40088 N ; tau = 603.015 * 48.40088/100 = 291.865 MPa
    r = _call(**BASE, deflection="10 mm")
    assert r.ok, r.error
    assert math.isclose(r.values["load"].quantity, 48.40088, rel_tol=1e-6)
    assert math.isclose(r.values["shear_stress"].quantity, 603.015 * 0.4840088, rel_tol=1e-6)


def test_free_length_gives_stroke_solid_load_and_utilization():
    r = _call(**BASE, load="100 N", free_length="50 mm", allowable_shear="800 MPa")
    assert r.ok, r.error
    assert math.isclose(r.values["stroke_to_solid"].quantity, 30.0, rel_tol=1e-12)
    assert math.isclose(r.values["solid_load"].quantity, 145.2026, rel_tol=1e-6)
    assert math.isclose(r.values["solid_shear_stress"].quantity, 875.59, rel_tol=1e-5)
    assert math.isclose(r.values["length_under_load"].quantity, 50 - 20.6608, rel_tol=1e-5)
    assert math.isclose(r.values["utilization"].quantity, 603.015 / 800.0, rel_tol=1e-6)  # 0.7538
    assert math.isclose(r.values["utilization_at_solid"].quantity, 875.59 / 800.0, rel_tol=1e-5)  # 1.0945
    assert any("at solid height" in w for w in r.warnings)
    # L0/D = 50/16 = 3.125: above 2.63 but below 5.26
    assert any("L0/D = 3.12" in w and "held flat and parallel" in w for w in r.warnings)


def test_end_types():
    # squared (closed, not ground): Nt = 10, Hs = (Nt+1) d = 22 mm ; plain & ground: Nt = 9, Hs = 9*2 = 18 ; plain: Nt = 8, Hs = (8+1)*2 = 18
    for end, nt, hs in [("squared_ground", 10, 20), ("squared", 10, 22), ("plain_ground", 9, 18), ("plain", 8, 18)]:
        r = _call(**BASE, end_type=end)
        assert r.values["total_coils"].quantity == nt, end
        assert math.isclose(r.values["solid_height"].quantity, hs), end
        assert math.isclose(r.value.quantity, K_N_MM, rel_tol=1e-12)  # the rate depends on active coils only
    assert _call(**BASE, end_type="Squared-Ground").ok  # spelling is normalised


def test_buckling_warning_for_a_slender_spring():
    # L0 = 100 mm, D = 16 -> L0/D = 6.25 > 5.26
    r = _call(**BASE, free_length="100 mm")
    assert any("L0/D = 6.25" in w and "can buckle even" in w for w in r.warnings)
    # L0 = 30 mm -> 1.875 < 2.63: no buckling warning
    r = _call(**BASE, free_length="30 mm")
    assert not any("buckle" in w for w in r.warnings)


def test_unit_conversions():
    # d = 0.002 m, D = 16 mm, G = 11.5e6 psi = 79 289.71 MPa -> k = 79 289.71 * 16 / 262 144 = 4.83946 N/mm
    r = _call(wire_diameter="0.002 m", mean_diameter=f"{16 / INCH_MM} inch", active_coils=8, shear_modulus="11.5e6 psi")
    assert r.ok, r.error
    assert math.isclose(r.value.quantity, 4.83946, rel_tol=1e-5)
    # imperial output: 4.840088 N/mm = 4.840088 / 0.1751268 = 27.6385 lbf/in ; 603.015 MPa = 87.457 ksi ; 20 mm = 0.7874 in
    r = _call(**BASE, load="100 N", unit_system="imperial")
    assert r.value.unit == "force_pound / inch"
    assert math.isclose(r.value.quantity, K_N_MM * INCH_MM / LBF_N, rel_tol=1e-9)
    assert math.isclose(r.values["shear_stress"].quantity, 603.015e6 / PSI_PA / 1000.0, rel_tol=1e-6)
    assert math.isclose(r.values["solid_height"].quantity, 20 / INCH_MM, rel_tol=1e-9)
    assert math.isclose(r.values["load"].quantity, 100 / LBF_N, rel_tol=1e-9)
    # load given in lbf
    r = _call(**BASE, load=f"{100 / LBF_N} lbf")
    assert math.isclose(r.values["deflection"].quantity, 20.6608, rel_tol=1e-6)


def test_spring_index_and_coil_count_warnings():
    # d = 4, D = 12 -> C = 3 < 4
    assert any("spring index C = 3.00" in w for w in _call(wire_diameter="4 mm", mean_diameter="12 mm", active_coils=8).warnings)
    # d = 1, D = 14 -> C = 14 > 12
    assert any("spring index C = 14.00" in w for w in _call(wire_diameter="1 mm", mean_diameter="14 mm", active_coils=8).warnings)
    assert any("only 2 active coils" in w for w in _call(**{**BASE, "active_coils": 2}).warnings)
    assert not any("spring index" in w for w in _call(**BASE).warnings)


@pytest.mark.parametrize(
    ("override", "family", "param"),
    [
        ({"wire_diameter": "0 mm"}, "input.invalid", "wire_diameter"),
        ({"wire_diameter": "-2 mm"}, "input.invalid", "wire_diameter"),
        ({"wire_diameter": "2"}, "units.bare_number", "wire_diameter"),
        ({"wire_diameter": "2 N"}, "units.dimension_mismatch", "wire_diameter"),
        ({"wire_diameter": "5 m"}, "input.out_of_range", "wire_diameter"),
        ({"mean_diameter": "2.5 mm"}, "input.out_of_range", "spring index D/d"),  # C = 1.25
        ({"mean_diameter": "120 mm"}, "input.out_of_range", "spring index D/d"),  # C = 60
        ({"mean_diameter": "0 mm"}, "input.invalid", "mean_diameter"),
        ({"mean_diameter": "16 kg"}, "units.dimension_mismatch", "mean_diameter"),
        ({"active_coils": 0}, "input.invalid", "active_coils"),
        ({"active_coils": -3}, "input.invalid", "active_coils"),
        ({"active_coils": "8"}, "input.invalid", "active_coils"),
        ({"active_coils": 0.2}, "input.out_of_range", "active_coils"),
        ({"active_coils": 1e6}, "input.invalid", "active_coils"),
        ({"shear_modulus": "79.3"}, "units.bare_number", "shear_modulus"),
        ({"shear_modulus": "79.3 mm"}, "units.dimension_mismatch", "shear_modulus"),
        ({"shear_modulus": "1e6 GPa"}, "input.out_of_range", "shear_modulus"),
        ({"shear_modulus": "0 GPa"}, "input.invalid", "shear_modulus"),
        ({"end_type": "ragged"}, "input.invalid", "end_type"),
        ({"load": "100 N", "deflection": "10 mm"}, "input.invalid", "load|deflection"),
        ({"load": "0 N"}, "input.invalid", "load"),
        ({"load": "-5 N"}, "input.invalid", "load"),
        ({"load": "100 mm"}, "units.dimension_mismatch", "load"),
        ({"deflection": "0 mm"}, "input.invalid", "deflection"),
        ({"deflection": "10 N"}, "units.dimension_mismatch", "deflection"),
        ({"free_length": "15 mm"}, "input.invalid", "free_length"),  # shorter than the solid height 20 mm
        ({"free_length": "20 mm"}, "input.invalid", "free_length"),  # equal: no stroke
        ({"free_length": "50 mm", "deflection": "35 mm"}, "input.out_of_range", "load|deflection"),  # stroke is 30 mm
        ({"free_length": "50 mm", "load": "200 N"}, "input.out_of_range", "load|deflection"),  # 41.3 mm > 30 mm
        ({"free_length": "1e9 mm"}, "input.out_of_range", "free_length"),
        ({"allowable_shear": "800"}, "units.bare_number", "allowable_shear"),
        ({"allowable_shear": "800 N"}, "units.dimension_mismatch", "allowable_shear"),
        ({"allowable_shear": "0 MPa"}, "input.invalid", "allowable_shear"),
        ({"unit_system": "cubits"}, "input.invalid", "unit_system"),
    ],
)
def test_spring_invalid_inputs(override, family, param):
    _fails(family, param, **{**BASE, **override})
