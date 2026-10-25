"""metrology_fit_search checked against hand calculations from the ISO 286 tables.

Tables used in the hand calculations (micrometres, ISO 286-1), size range 18-30 mm, deviations 24-30 mm:

* IT values   : IT6 = 13, IT7 = 21, IT8 = 33, IT9 = 52, IT11 = 130
* shaft es    : c = -110, d = -65, f = -20, g = -7
* shaft ei    : k = +2, n = +15, p = +22, s = +35, u = +48
* H holes     : EI = 0, ES = +IT

Clearance = hole size - shaft size (interference negative).  min = EI - es, max = ES - ei.
Preferred hole-basis fits at 25 mm, (min, max) clearance in micrometres:

    H11/c11  EI 0, ES 130 ; c11 es -110, ei -110-130 = -240     -> (110, 370)
    H9/d9    ES 52        ; d9 es -65, ei -65-52 = -117         -> ( 65, 169)
    H8/f7    ES 33        ; f7 es -20, ei -20-21 = -41          -> ( 20,  74)
    H7/g6    ES 21        ; g6 es -7, ei -7-13 = -20            -> (  7,  41)
    H7/h6    ES 21        ; h6 es 0, ei -13                     -> (  0,  34)
    H7/k6    ES 21        ; k6 ei +2, es 2+13 = 15              -> (-15,  19)
    H7/n6    ES 21        ; n6 ei +15, es 15+13 = 28            -> (-28,   6)
    H7/p6    ES 21        ; p6 ei +22, es 22+13 = 35            -> (-35,  -1)
    H7/s6    ES 21        ; s6 ei +35, es 35+13 = 48            -> (-48, -14)
    H7/u6    ES 21        ; u6 ei +48, es 48+13 = 61            -> (-61, -27)

The shaft-basis counterparts C11/h11 ... U7/h6 give the same ten pairs of numbers.
"""

import json

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology import fit_search as fs
from eng_toolkit.domains.metrology import iso286
from eng_toolkit.domains.metrology.tools import fit_search

PREFERRED_AT_25MM = [
    # hole-basis fit, shaft-basis fit, min clearance um, max clearance um
    ("H11/c11", "C11/h11", 110, 370),
    ("H9/d9", "D9/h9", 65, 169),
    ("H8/f7", "F8/h7", 20, 74),
    ("H7/g6", "G7/h6", 7, 41),
    ("H7/h6", "H7/h6", 0, 34),
    ("H7/k6", "K7/h6", -15, 19),
    ("H7/n6", "N7/h6", -28, 6),
    ("H7/p6", "P7/h6", -35, -1),
    ("H7/s6", "S7/h6", -48, -14),
    ("H7/u6", "U7/h6", -61, -27),
]


def _call(**kwargs) -> ToolResult:
    fn = getattr(fit_search, "fn", fit_search)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def _codes(r: ToolResult) -> list[str]:
    return [c["fit"] for c in r.data["candidates"]]


def _um(mm: float) -> float:
    return round(mm * 1000, 6)


# --------------------------------------------------------------------------- the fit tables


def test_preferred_table_lists_ten_fits_per_basis_in_the_documented_order():
    assert fs.PREFERRED["hole"] == tuple(hole for hole, _, _, _ in PREFERRED_AT_25MM)
    assert fs.PREFERRED["shaft"] == tuple(shaft for _, shaft, _, _ in PREFERRED_AT_25MM)


@pytest.mark.parametrize("hole_fit, shaft_fit, cmin, cmax", PREFERRED_AT_25MM)
def test_preferred_fits_at_25mm_match_the_hand_calculation(hole_fit, shaft_fit, cmin, cmax):
    for code in (hole_fit, shaft_fit):
        limits = iso286.fit_limits(25, code)
        assert (limits.min_clearance_um, limits.max_clearance_um) == (cmin, cmax), code


@pytest.mark.parametrize("basis", ["hole", "shaft"])
def test_common_set_contains_every_preferred_fit(basis):
    assert set(fs.PREFERRED[basis]) <= set(fs.FIT_SETS[basis]["common"])


def test_common_set_follows_the_generation_rule():
    # Hole basis: hole grades 6..11 (6 grades) x 15 shaft letters x 2 shaft grades (equal, one finer),
    # every shaft grade 5..11 is inside the 5..11 band -> 6 * 15 * 2 = 180.
    hole_basis = fs.FIT_SETS["hole"]["common"]
    assert len(hole_basis) == 180
    assert len(set(hole_basis)) == 180
    assert {"H6/g5", "H6/g6", "H11/c10", "H11/c11", "H7/js6", "H8/u7"} <= set(hole_basis)
    assert "H6/g4" not in hole_basis  # grade 4 is below the band
    assert "H7/g7" in hole_basis and "H7/g8" not in hole_basis  # shaft grade is never coarser than the hole's
    assert "H7/j6" not in hole_basis  # only the letter set c d e f g h js k m n p r s t u
    # Shaft basis: shaft grades 5..11 (7 grades) x 15 hole letters x 2 hole grades (equal, one coarser);
    # shaft grade 11 would need hole grade 12, which is above the band -> 7 * 15 * 2 - 15 = 195.
    shaft_basis = fs.FIT_SETS["shaft"]["common"]
    assert len(shaft_basis) == 195
    assert len(set(shaft_basis)) == 195
    assert {"G6/h5", "G5/h5", "C11/h11", "JS8/h7", "U8/h7"} <= set(shaft_basis)
    assert "C12/h11" not in shaft_basis
    assert "G6/h7" not in shaft_basis  # hole grade is never finer than the shaft's


# --------------------------------------------------------------------------- hand-checked searches


@pytest.mark.parametrize("hole_fit, shaft_fit, cmin, cmax", PREFERRED_AT_25MM)
@pytest.mark.parametrize("basis", ["hole", "shaft"])
def test_a_window_equal_to_a_preferred_fit_finds_that_fit(basis, hole_fit, shaft_fit, cmin, cmax):
    # The window edges are inclusive, so the fit's own limits as the window must qualify it.
    r = _call(nominal="25 mm", min_clearance=f"{cmin} um", max_clearance=f"{cmax} um", basis=basis)
    assert r.ok, r.error
    fit = hole_fit if basis == "hole" else shaft_fit
    assert fit in _codes(r)
    found = next(c for c in r.data["candidates"] if c["fit"] == fit)
    assert _um(found["min_clearance_mm"]) == cmin
    assert _um(found["max_clearance_mm"]) == cmax
    assert found["preferred"] is True


def test_window_0_to_0_05_mm_gives_g6_then_h6():
    # H7/g6 (7, 41) and H7/h6 (0, 34) are the only preferred fits inside 0..50 um: H8/f7 reaches 74,
    # H7/k6 starts at -15.  Tolerance sums: g6 21 + 13 = 34, h6 21 + 13 = 34 (a tie).
    # Window centre (0 + 50) / 2 = 25.  Fit centres: g6 (7 + 41) / 2 = 24 -> 1 away, h6 (0 + 34) / 2 = 17 -> 8 away.
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm")
    assert r.ok, r.error
    assert _codes(r) == ["H7/g6", "H7/h6"]
    assert r.values["candidate_count"].quantity == 2
    assert r.values["candidate_count"].unit == "dimensionless"
    assert r.value.unit == "dimensionless"
    assert r.values["best_min_clearance"].quantity == pytest.approx(0.007, abs=1e-9)
    assert r.values["best_max_clearance"].quantity == pytest.approx(0.041, abs=1e-9)
    assert r.values["best_min_clearance"].unit == "millimeter"
    assert "nearest" not in r.data
    best = r.data["candidates"][0]
    assert best == {
        "fit": "H7/g6",
        "hole": "H7",
        "shaft": "g6",
        "preferred": True,
        "fit_type": "clearance",
        "min_clearance_mm": pytest.approx(0.007, abs=1e-9),
        "max_clearance_mm": pytest.approx(0.041, abs=1e-9),
        "hole_tolerance_um": 21,
        "shaft_tolerance_um": 13,
    }
    assert r.data["basis"] == "hole"
    assert r.data["fit_set"] == "preferred"
    assert r.data["window_mm"] == [0.0, 0.05]
    assert r.data["size_range_mm"] == [18, 30]
    assert r.data["searched"] == 10
    assert r.data["skipped_undefined"] == 0


def test_interference_window_gives_s6_then_p6():
    # Window -50..-1 um.  s6 (-48, -14) and p6 (-35, -1) fit; u6 starts at -61, n6 reaches +6.
    # Tolerance sums tie at 34.  Window centre (-50 - 1) / 2 = -25.5.
    # s6 centre (-48 - 14) / 2 = -31 -> 5.5 away ; p6 centre (-35 - 1) / 2 = -18 -> 7.5 away.
    r = _call(nominal="25 mm", min_clearance="-0.05 mm", max_clearance="-0.001 mm")
    assert r.ok, r.error
    assert _codes(r) == ["H7/s6", "H7/p6"]
    assert {c["fit_type"] for c in r.data["candidates"]} == {"interference"}
    assert r.values["best_min_clearance"].quantity == pytest.approx(-0.048, abs=1e-9)
    assert r.values["best_max_clearance"].quantity == pytest.approx(-0.014, abs=1e-9)


def test_shaft_basis_window_gives_g7_h6_only():
    # Window 7..41 um: G7/h6 is exactly (7, 41).  H7/h6 starts at 0, F8/h7 reaches 74.
    r = _call(
        nominal="25 mm", min_clearance="0.007 mm", max_clearance="0.041 mm", basis="shaft"
    )
    assert r.ok, r.error
    assert _codes(r) == ["G7/h6"]
    assert r.data["basis"] == "shaft"
    assert r.data["candidates"][0]["hole"] == "G7"
    assert r.data["candidates"][0]["shaft"] == "h6"
    assert r.values["best_min_clearance"].quantity == 0.007
    assert r.values["best_max_clearance"].quantity == 0.041


def test_window_nothing_fits_lists_the_nearest_misses():
    # Window 30..33 um is only 3 um wide, narrower than any preferred fit (smallest span is 34 um).
    # violation = max(0, 30 - min) + max(0, max - 33), in um:
    #   H7/g6 (7, 41):   23 + 8  = 31        H7/h6 (0, 34):  30 + 1  = 31   (tie -> list order: g6 first)
    #   H7/k6 (-15, 19): 45 + 0  = 45        H8/f7 (20, 74): 10 + 41 = 51   H7/n6 (-28, 6): 58 + 0 = 58
    # every other preferred fit is further away (H9/d9 (65, 169): 0 + 136, H7/p6 (-35, -1): 65 + 0, ...).
    r = _call(nominal="25 mm", min_clearance="0.030 mm", max_clearance="0.033 mm")
    assert r.ok, r.error
    assert r.data["candidates"] == []
    assert r.values["candidate_count"].quantity == 0
    assert "best_min_clearance" not in r.values
    assert "best_max_clearance" not in r.values
    near = [(c["fit"], c["violation_um"]) for c in r.data["nearest"]]
    assert near == [("H7/g6", 31), ("H7/h6", 31), ("H7/k6", 45)]
    assert r.data["nearest"][0]["fit_type"] == "clearance"
    assert r.data["nearest"][0]["min_clearance_mm"] == pytest.approx(0.007, abs=1e-9)
    assert any("no" in w.lower() and "nearest" in w for w in r.warnings)


def test_window_with_a_one_sided_miss_counts_only_the_missed_side():
    # Window -15..19 um is exactly H7/k6.  Shrinking the top to 18 um leaves k6 missing only the top by
    # 19 - 18 = 1 um (its min -15 is inside).  H7/h6 (0, 34) misses by 0 + 16 = 16, H7/n6 (-28, 6) by 13 + 0 = 13.
    r = _call(nominal="25 mm", min_clearance="-0.015 mm", max_clearance="0.018 mm")
    assert r.ok, r.error
    assert r.data["candidates"] == []
    assert r.data["nearest"][0]["fit"] == "H7/k6"
    assert r.data["nearest"][0]["violation_um"] == 1


# --------------------------------------------------------------------------- the common set


def test_common_set_finds_fits_outside_the_preferred_list():
    # Window 7..41 um, hole basis.  H6/g5: H6 = 0..13, g5 = es -7, ei -7 - 9 = -16 -> (7, 29): inside, not preferred.
    r = _call(nominal="25 mm", min_clearance="7 um", max_clearance="41 um", fit_set="common")
    assert r.ok, r.error
    assert "H7/g6" in _codes(r)
    h6_g5 = next(c for c in r.data["candidates"] if c["fit"] == "H6/g5")
    assert h6_g5["preferred"] is False
    assert _um(h6_g5["max_clearance_mm"]) == 29
    assert next(c for c in r.data["candidates"] if c["fit"] == "H7/g6")["preferred"] is True


@pytest.mark.parametrize("basis", ["hole", "shaft"])
@pytest.mark.parametrize(
    "lo, hi",
    [("0 mm", "0.05 mm"), ("-0.05 mm", "-0.001 mm"), ("-0.03 mm", "0.04 mm"), ("0.02 mm", "0.4 mm"), ("-0.1 mm", "0.1 mm")],
)
def test_every_common_candidate_satisfies_the_window_and_is_ranked(basis, lo, hi):
    r = _call(nominal="25 mm", min_clearance=lo, max_clearance=hi, basis=basis, fit_set="common", max_results=50)
    assert r.ok, r.error
    wmin, wmax = (float(s.split()[0]) for s in (lo, hi))
    cands = r.data["candidates"]
    assert 0 < len(cands) <= 50
    for c in cands:
        assert c["min_clearance_mm"] >= wmin - 1e-9, c
        assert c["max_clearance_mm"] <= wmax + 1e-9, c
        assert c["preferred"] == (c["fit"] in fs.PREFERRED[basis]), c
    # Ranking: tolerance sum falls, and among equal sums the fit centre moves away from the window centre.
    keys = [
        (
            -(c["hole_tolerance_um"] + c["shaft_tolerance_um"]),
            round(abs((c["min_clearance_mm"] + c["max_clearance_mm"]) / 2 - (wmin + wmax) / 2), 6),
        )
        for c in cands
    ]
    assert keys == sorted(keys)


def test_results_never_exceed_max_results_and_truncation_is_reported():
    wide = dict(nominal="25 mm", min_clearance="-0.2 mm", max_clearance="0.5 mm", fit_set="common")
    everything = _call(**wide, max_results=50)
    assert len(everything.data["candidates"]) > 3
    r = _call(**wide, max_results=3)
    assert r.ok, r.error
    assert len(r.data["candidates"]) == 3
    assert r.values["candidate_count"].quantity == 3
    assert _codes(r) == _codes(everything)[:3]
    assert any("max_results" in w for w in r.warnings)
    untruncated = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm")
    assert not any("max_results" in w for w in untruncated.warnings)


def test_classes_the_standard_does_not_define_are_skipped_and_counted():
    # 10 mm lies in 6-10: shaft t starts at 24 mm, so every t pairing is undefined.
    # Hole basis: t with 6 hole grades x 2 shaft grades = 12 skipped.  Shaft basis: hole T likewise, 13 skipped
    # (7 shaft grades x 2, minus the shaft grade 11 pairing that would need hole grade 12).
    hole = _call(nominal="10 mm", min_clearance="-0.2 mm", max_clearance="0.5 mm", fit_set="common")
    assert hole.ok, hole.error
    assert hole.data["skipped_undefined"] == 12
    assert hole.data["searched"] == 180 - 12
    assert not any("/t" in c for c in _codes(hole))
    shaft = _call(nominal="10 mm", min_clearance="-0.2 mm", max_clearance="0.5 mm", fit_set="common", basis="shaft")
    assert shaft.data["skipped_undefined"] == 13
    assert shaft.data["searched"] == 195 - 13


def test_searched_counts_every_class_evaluated_at_25mm():
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm", fit_set="common")
    assert r.data["searched"] == 180
    assert r.data["skipped_undefined"] == 0


def test_other_length_units_are_converted():
    # 1 in = 25.4 mm lies in 18-30 (deviations 24-30), so the limits equal the 25 mm ones.
    # 0.002 in = 0.0508 mm = 50.8 um: H8/f7 (20, 74) still exceeds it, so the same two fits qualify.
    r = _call(nominal="1 inch", min_clearance="0 mm", max_clearance="0.002 inch")
    assert r.ok, r.error
    assert r.data["window_mm"] == [0.0, 0.0508]
    assert _codes(r) == ["H7/g6", "H7/h6"]


# --------------------------------------------------------------------------- contract


def test_result_follows_the_contract_and_states_the_ranking_caveat():
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm")
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.metrology.fit_search"
    assert any("cost" in w and "suitab" in w for w in r.warnings)


# --------------------------------------------------------------------------- error paths


def _assert_error(r: ToolResult, family: str) -> None:
    assert not r.ok
    assert r.error.family == family
    assert r.warnings == []


@pytest.mark.parametrize("bare", ["25", "25.0"])
def test_bare_number_nominal_is_rejected(bare):
    _assert_error(_call(nominal=bare, min_clearance="0 mm", max_clearance="0.05 mm"), "units.bare_number")


@pytest.mark.parametrize("field", ["min_clearance", "max_clearance"])
def test_bare_number_clearance_is_rejected(field):
    args = {"nominal": "25 mm", "min_clearance": "0 mm", "max_clearance": "0.05 mm", field: "0.01"}
    _assert_error(_call(**args), "units.bare_number")


@pytest.mark.parametrize("field", ["nominal", "min_clearance", "max_clearance"])
def test_wrong_dimension_is_rejected(field):
    args = {"nominal": "25 mm", "min_clearance": "0 mm", "max_clearance": "0.05 mm", field: "5 bar"}
    _assert_error(_call(**args), "units.dimension_mismatch")


@pytest.mark.parametrize("size", ["2.99 mm", "500.01 mm", "1 m", "0 mm", "-5 mm"])
def test_nominal_outside_3_to_500_mm_is_an_explicit_error(size):
    r = _call(nominal=size, min_clearance="0 mm", max_clearance="0.05 mm")
    _assert_error(r, "input.out_of_range")
    assert r.error.details["supported_range_mm"] == [3, 500]


def test_min_clearance_above_max_clearance_is_rejected():
    r = _call(nominal="25 mm", min_clearance="0.05 mm", max_clearance="0 mm")
    _assert_error(r, "input.invalid")
    assert "min_clearance" in r.error.message


@pytest.mark.parametrize("basis", ["", "both", "Hole", "holes"])
def test_bad_basis_is_rejected(basis):
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm", basis=basis)
    _assert_error(r, "input.invalid")
    assert r.error.details["param"] == "/basis"


@pytest.mark.parametrize("fit_set", ["", "all", "Preferred", "full"])
def test_bad_fit_set_is_rejected(fit_set):
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm", fit_set=fit_set)
    _assert_error(r, "input.invalid")
    assert r.error.details["param"] == "/fit_set"


@pytest.mark.parametrize("n", [0, 51, -1])
def test_max_results_outside_1_to_50_is_rejected(n):
    r = _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm", max_results=n)
    _assert_error(r, "input.invalid")
    assert r.error.details["param"] == "/max_results"


@pytest.mark.parametrize("n", [1, 50])
def test_max_results_boundaries_are_accepted(n):
    assert _call(nominal="25 mm", min_clearance="0 mm", max_clearance="0.05 mm", max_results=n).ok
