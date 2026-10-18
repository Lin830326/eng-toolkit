"""metrology_fit_limits checked against hand calculations from the ISO 286 tables.

Tables used in the hand calculations (micrometres, ISO 286-1):

* IT values, size range 18-30 mm : IT6 = 13, IT7 = 21
* IT values, size range 6-10 mm  : IT6 = 9,  IT7 = 15
* shaft fundamental deviations, 18-30 mm : g -> es = -7, k -> ei = +2, p -> ei = +22
"""

import json
import math

import pytest

from eng_toolkit.contract import ToolResult
from eng_toolkit.domains.metrology import iso286
from eng_toolkit.domains.metrology.tools import fit_limits


def _call(**kwargs) -> ToolResult:
    fn = getattr(fit_limits, "fn", fit_limits)
    result = fn(**kwargs)
    assert isinstance(result, ToolResult)
    ToolResult.model_validate(json.loads(result.model_dump_json()))
    return result


def _v(r: ToolResult, name: str) -> float:
    return r.values[name].quantity


# --------------------------------------------------------------------------- hand calculations


def test_h7_g6_25mm_is_a_clearance_fit():
    # 25 mm lies in 18-30.  H7: ES = +IT7 = +21, EI = 0           -> hole 25.000 .. 25.021
    # g6: es = -7, ei = es - IT6 = -7 - 13 = -20                  -> shaft 24.980 .. 24.993
    # min clearance = hole_min - shaft_max = 25.000 - 24.993      =  0.007 mm
    # max clearance = hole_max - shaft_min = 25.021 - 24.980      =  0.041 mm
    r = _call(nominal="25 mm", fit="H7/g6")
    assert r.ok, r.error
    assert r.data["fit_type"] == "clearance"
    assert _v(r, "hole_upper_deviation") == pytest.approx(21)
    assert _v(r, "hole_lower_deviation") == pytest.approx(0)
    assert _v(r, "shaft_upper_deviation") == pytest.approx(-7)
    assert _v(r, "shaft_lower_deviation") == pytest.approx(-20)
    assert r.values["hole_upper_deviation"].unit == "micrometer"
    assert _v(r, "hole_max_size") == pytest.approx(25.021, abs=1e-9)
    assert _v(r, "hole_min_size") == pytest.approx(25.000, abs=1e-9)
    assert _v(r, "shaft_max_size") == pytest.approx(24.993, abs=1e-9)
    assert _v(r, "shaft_min_size") == pytest.approx(24.980, abs=1e-9)
    assert r.values["hole_max_size"].unit == "millimeter"
    assert _v(r, "min_clearance") == pytest.approx(0.007, abs=1e-9)
    assert _v(r, "max_clearance") == pytest.approx(0.041, abs=1e-9)
    assert _v(r, "hole_tolerance") == pytest.approx(21)
    assert _v(r, "shaft_tolerance") == pytest.approx(13)
    assert r.value.unit == "millimeter"
    assert r.review_status == "unreviewed"
    assert r.provenance.engine == "eng_toolkit.metrology.iso286"
    assert r.data["size_range_mm"] == [18, 30]  # main range (IT values)
    assert r.data["deviation_range_mm"] == [24, 30]  # finer range (fundamental deviations)


def test_h7_p6_25mm_is_an_interference_fit():
    # p6: ei = +22, es = 22 + 13 = +35 -> shaft 25.022 .. 25.035 ; hole 25.000 .. 25.021
    # min "clearance" = 25.000 - 25.035 = -0.035 (max interference 35 um)
    # max "clearance" = 25.021 - 25.022 = -0.001 (min interference 1 um)
    r = _call(nominal="25 mm", fit="H7/p6")
    assert r.ok, r.error
    assert r.data["fit_type"] == "interference"
    assert _v(r, "min_clearance") == pytest.approx(-0.035, abs=1e-9)
    assert _v(r, "max_clearance") == pytest.approx(-0.001, abs=1e-9)
    assert r.data["max_interference_mm"] == pytest.approx(0.035, abs=1e-9)
    assert r.data["min_interference_mm"] == pytest.approx(0.001, abs=1e-9)


def test_h7_k6_25mm_is_a_transition_fit():
    # k6: ei = +2, es = 2 + 13 = +15 -> shaft 25.002 .. 25.015 ; hole 25.000 .. 25.021
    # min = 25.000 - 25.015 = -0.015 ; max = 25.021 - 25.002 = +0.019
    r = _call(nominal="25 mm", fit="H7/k6")
    assert r.ok, r.error
    assert r.data["fit_type"] == "transition"
    assert _v(r, "min_clearance") == pytest.approx(-0.015, abs=1e-9)
    assert _v(r, "max_clearance") == pytest.approx(0.019, abs=1e-9)


def test_hole_k7_uses_the_delta_special_rule():
    # 10 mm lies in 6-10.  k ei = +1.  delta = IT7 - IT6 = 15 - 9 = 6
    # K7: ES = -ei + delta = -1 + 6 = +5 ; EI = ES - IT7 = 5 - 15 = -10
    r = _call(nominal="10 mm", fit="K7/h6")
    assert r.ok, r.error
    assert _v(r, "hole_upper_deviation") == pytest.approx(5)
    assert _v(r, "hole_lower_deviation") == pytest.approx(-10)
    # h6: es = 0, ei = -9 -> min "clearance" = (10 - 0.010) - 10.000 = -0.010
    assert _v(r, "min_clearance") == pytest.approx(-0.010, abs=1e-9)


def test_size_range_boundaries_are_inclusive_at_the_upper_end():
    # 18 mm -> range 10-18, IT7 = 18 ; 18.001 mm -> range 18-30, IT7 = 21
    assert _v(_call(nominal="18 mm", fit="H7/h6"), "hole_tolerance") == pytest.approx(18)
    assert _v(_call(nominal="18.001 mm", fit="H7/h6"), "hole_tolerance") == pytest.approx(21)
    # 3 mm belongs to the first range (IT7 = 10), 3.001 mm to 3-6 (IT7 = 12)
    assert _v(_call(nominal="3 mm", fit="H7/h6"), "hole_tolerance") == pytest.approx(10)
    assert _v(_call(nominal="3.001 mm", fit="H7/h6"), "hole_tolerance") == pytest.approx(12)
    # 500 mm belongs to 450-500 (IT7 = 63)
    assert _v(_call(nominal="500 mm", fit="H7/h6"), "hole_tolerance") == pytest.approx(63)


def test_other_length_units_are_converted_to_millimetres():
    # 1 in = 25.4 mm -> range 18-30 ; same hole as 25.4 mm: 25.4 .. 25.421
    r = _call(nominal="1 inch", fit="H7/g6")
    assert r.ok, r.error
    assert _v(r, "hole_max_size") == pytest.approx(25.421, abs=1e-9)


def test_js_deviation_is_symmetric_and_not_rounded():
    # JS7, 6-10 mm: IT7 = 15 -> +/-7.5 um (ISO 286-1 allows rounding odd IT7..IT11, we keep it exact)
    r = _call(nominal="8 mm", fit="JS7/h6")
    assert r.ok, r.error
    assert _v(r, "hole_upper_deviation") == pytest.approx(7.5)
    assert _v(r, "hole_lower_deviation") == pytest.approx(-7.5)
    # js6 shaft, 6-10: IT6 = 9 (grade 6, plain IT/2) -> +/-4.5 um
    r = _call(nominal="8 mm", fit="H7/js6")
    assert _v(r, "shaft_upper_deviation") == pytest.approx(4.5)
    assert _v(r, "shaft_lower_deviation") == pytest.approx(-4.5)


def test_it_grades_from_it01_to_it18_are_available():
    # 50 mm lies in 30-50: IT01 = 0.6, IT0 = 1, IT1 = 1.5, IT18 = 3900 um
    assert iso286.it_tolerance_um(50, "01") == pytest.approx(0.6)
    assert iso286.it_tolerance_um(50, "0") == pytest.approx(1.0)
    assert iso286.it_tolerance_um(50, "1") == pytest.approx(1.5)
    assert iso286.it_tolerance_um(50, "18") == pytest.approx(3900)
    r = _call(nominal="50 mm", fit="H01/h01")
    assert r.ok, r.error
    assert _v(r, "hole_tolerance") == pytest.approx(0.6)


def test_shaft_basis_fit_g7_h6_at_25mm():
    # G7: EI = -es(g) = +7, ES = 7 + 21 = +28 ; h6: es = 0, ei = -13
    r = _call(nominal="25 mm", fit="G7/h6")
    assert r.ok, r.error
    assert _v(r, "hole_lower_deviation") == pytest.approx(7)
    assert _v(r, "hole_upper_deviation") == pytest.approx(28)
    assert _v(r, "min_clearance") == pytest.approx(0.007, abs=1e-9)
    assert _v(r, "max_clearance") == pytest.approx(0.041, abs=1e-9)


def test_whitespace_in_the_fit_code_is_tolerated():
    assert _call(nominal="25 mm", fit=" H7 / g6 ").ok


# Known values published as ground truth in the MIT-licensed mcp-tolerance project
# (tests/fixtures/iso286_table_values.json, ISO 286-1:2010).  Only cells that agree with a hand
# calculation from the standard are used: that project's K and P holes omit the delta rule and its
# k shaft ignores the +1/+2 um table value, so those entries are deliberately not borrowed.
# Not a runtime dependency; the numbers are copied here as plain test data.
MCP_TOLERANCE_FITS = [
    # description, hole, shaft, size mm, (ES, EI, es, ei) um, fit type, min and max clearance um
    ("H7/g6 25", "H7", "g6", 25, (21, 0, -7, -20), "clearance", 7, 41),
    ("H7/p6 25", "H7", "p6", 25, (21, 0, 35, 22), "interference", -35, -1),
    ("H7/h6 25", "H7", "h6", 25, (21, 0, 0, -13), "clearance", 0, 34),
    ("G7/h6 25", "G7", "h6", 25, (28, 7, 0, -13), "clearance", 7, 41),
    ("H7/n6 25", "H7", "n6", 25, (21, 0, 28, 15), "transition", -28, 6),
    ("C7/c6 40.001", "C7", "c6", 40.001, (155, 130, -130, -146), "clearance", 260, 301),
]


@pytest.mark.parametrize("name, hole, shaft, size, devs, fit_type, cmin, cmax", MCP_TOLERANCE_FITS)
def test_values_agree_with_mcp_tolerance_reference_fits(name, hole, shaft, size, devs, fit_type, cmin, cmax):
    r = _call(nominal=f"{size} mm", fit=f"{hole}/{shaft}")
    assert r.ok, r.error
    es, ei, sh_es, sh_ei = devs
    assert (
        _v(r, "hole_upper_deviation"),
        _v(r, "hole_lower_deviation"),
        _v(r, "shaft_upper_deviation"),
        _v(r, "shaft_lower_deviation"),
    ) == pytest.approx((es, ei, sh_es, sh_ei))
    assert r.data["fit_type"] == fit_type
    assert _v(r, "min_clearance") * 1000 == pytest.approx(cmin)
    assert _v(r, "max_clearance") * 1000 == pytest.approx(cmax)


def test_hand_checked_holes_that_use_the_special_rule():
    # K7, 18-30 mm: k ei = +2, delta = IT7 - IT6 = 21 - 13 = 8 -> ES = -2 + 8 = +6, EI = 6 - 21 = -15
    k7 = iso286.zone_deviations_um(25, "K7")
    assert (k7.upper_um, k7.lower_um) == (6, -15)
    # P7, 18-30 mm: p ei = +22, delta = 8 -> ES = -22 + 8 = -14, EI = -14 - 21 = -35
    p7 = iso286.zone_deviations_um(25, "P7")
    assert (p7.upper_um, p7.lower_um) == (-14, -35)
    # S7, 18-30 mm: s ei = +35, delta = 8 -> ES = -35 + 8 = -27, EI = -27 - 21 = -48
    s7 = iso286.zone_deviations_um(25, "S7")
    assert (s7.upper_um, s7.lower_um) == (-27, -48)
    # P8 is above IT7: no delta. 6-10 mm: p ei = +15 -> ES = -15, EI = -15 - 22 = -37
    p8 = iso286.zone_deviations_um(8, "P8")
    assert (p8.upper_um, p8.lower_um) == (-15, -37)
    # up to 3 mm delta is 0: P7 at 3 mm: ES = -6, EI = -6 - 10 = -16
    p7_small = iso286.zone_deviations_um(3, "P7")
    assert (p7_small.upper_um, p7_small.lower_um) == (-6, -16)


# Spot values for the positions isofits does not cover, taken from RoyMech's published ISO 286
# fundamental-deviation tables (0-500 mm), which the full one-off cell-for-cell comparison also used.
# (size mm, class, ES or es, EI or ei) in micrometres; IT6 = 13 and IT7 = 21 in 18-30 mm, etc.
SECOND_SOURCE_SPOT_VALUES = [
    (100, "b7", -220, -220 - 35),  # 80-100: es = -220 ; IT7 = 35
    (25, "c7", -110, -110 - 21),
    (3, "cd6", -34, -34 - 6),  # up to 3 mm: es = -34 ; IT6 = 6
    (5, "ef6", -14, -14 - 8),
    (8, "fg6", -8, -8 - 9),
    (25, "s6", 35 + 13, 35),  # 24-30: ei = +35
    (28, "t6", 41 + 13, 41),
    (16, "u6", 33 + 11, 33),  # 14-18: ei = +33
    (16, "v6", 39 + 11, 39),
    (450, "x6", 740 + 40, 740),  # 400-450: ei = +740 ; IT6 = 40
    (60, "y6", 144 + 19, 144),  # 50-65: ei = +144 ; IT6 = 19
    (60, "z6", 172 + 19, 172),
    (300, "za6", 1000 + 32, 1000),  # 280-315: ei = +1000 ; IT6 = 32
    (300, "zb6", 1300 + 32, 1300),  # two independent tables and the ISO formula (IT9 + 4 D) agree on 1300
    (500, "zc6", 2600 + 40, 2600),
    (28, "T7", -33, -33 - 21),  # 24-30: ES = -ei(41) + delta(8)
    (60, "Z7", -161, -161 - 30),  # 50-65: ES = -172 + delta(11)
    (3, "U6", -18, -18 - 6),  # up to 3 mm, delta 0
    (25, "M9", -8, -8 - 52),  # M above IT8: ES = -ei = -8
    (25, "N9", 0, -52),  # N above IT8 over 3 mm: ES = 0
]


@pytest.mark.parametrize("size, cls, upper, lower", SECOND_SOURCE_SPOT_VALUES)
def test_positions_without_an_isofits_oracle_match_a_second_published_table(size, cls, upper, lower):
    zone = iso286.zone_deviations_um(size, cls)
    assert (zone.upper_um, zone.lower_um) == (upper, lower)


# --------------------------------------------------------------------------- error paths


@pytest.mark.parametrize("bare", ["25", "25.0"])
def test_bare_number_nominal_is_rejected(bare):
    r = _call(nominal=bare, fit="H7/g6")
    assert not r.ok
    assert r.error.family == "units.bare_number"
    assert r.warnings == []


def test_nominal_with_wrong_dimension_is_rejected():
    r = _call(nominal="25 bar", fit="H7/g6")
    assert not r.ok
    assert r.error.family == "units.dimension_mismatch"


@pytest.mark.parametrize("size", ["2.99 mm", "2 mm", "500.01 mm", "501 mm", "1 m", "0 mm", "-5 mm"])
def test_size_outside_3_to_500_mm_is_an_explicit_error(size):
    r = _call(nominal=size, fit="H7/g6")
    assert not r.ok
    assert r.error.family == "input.out_of_range"
    assert "3" in r.error.message and "500" in r.error.message
    assert r.error.details["supported_range_mm"] == [3, 500]


@pytest.mark.parametrize(
    "code",
    [
        "H7",  # no shaft half
        "H7/",  # empty shaft half
        "H7/g6/x",  # too many parts
        "7/g6",  # no letter
        "H/g6",  # no grade
        "H19/g6",  # IT19 does not exist
        "H7/g99",
        "Q7/g6",  # no letter Q
        "H7/q6",
        "h7/g6",  # hole must be upper case
        "H7/G6",  # shaft must be lower case
        "Z7/g6extra",
        "",
        "fit please",
    ],
)
def test_unknown_or_malformed_fit_codes_are_rejected(code):
    r = _call(nominal="25 mm", fit=code)
    assert not r.ok
    assert r.error.family == "input.unknown_fit_code"
    assert "H7/g6" in r.error.message  # tells the caller what a valid code looks like


@pytest.mark.parametrize(
    "size, code",
    [
        ("10 mm", "H7/t6"),  # t starts at 24 mm
        ("50 mm", "H7/cd6"),  # cd, ef, fg only up to 10 mm
        ("25 mm", "H7/j9"),  # j exists for IT5..IT7 only
    ],
)
def test_classes_the_standard_does_not_define_are_explicit_errors(size, code):
    r = _call(nominal=size, fit=code)
    assert not r.ok
    assert r.error.family == "input.undefined_class"


# --------------------------------------------------------------------------- table self-consistency


def test_it_values_follow_the_iso_286_formulas():
    """IT5..IT18 = k * i with i = 0.45 * cbrt(D) + 0.001 * D (D = geometric mean of the range).

    The published table is rounded by rule, so allow 1 um or 5 %.
    """
    mult = {"5": 7, "6": 10, "7": 16, "8": 25, "9": 40, "10": 64, "11": 100, "12": 160,
            "13": 250, "14": 400, "15": 640, "16": 1000, "17": 1600, "18": 2500}
    tables = iso286.tables()
    for (lo, hi), row in zip(tables["it_ranges_mm"], tables["it_um"]):
        if lo == 0:
            continue  # the formula applies from 3 mm; the first range has its own special values
        D = math.sqrt(lo * hi)
        i = 0.45 * D ** (1 / 3) + 0.001 * D
        for grade, k in mult.items():
            tab = row[tables["it_grades"].index(grade)]
            assert abs(tab - k * i) <= max(1.0, 0.05 * tab), (lo, hi, grade, tab, k * i)


def test_shaft_deviation_table_matches_iso_286_formulas():
    """Recompute the formula-based fundamental deviations; a mistyped table cell fails here.

    d, e, f, g, k, n use the geometric mean of the *main* size range (the table repeats one
    value across its sub-ranges); the others use the sub-range itself.
    """
    t = iso286.tables()
    rng, main = t["size_ranges_mm"], t["it_ranges_mm"]
    main_of = t["it_range_of_size_range"]

    def d_main(i):
        lo, hi = main[main_of[i]]
        return math.sqrt(max(lo, 1) * hi)

    def d_sub(i):
        lo, hi = rng[i]
        return math.sqrt(max(lo, 1) * hi)

    def it(i, grade):
        return t["it_um"][main_of[i]][t["it_grades"].index(str(grade))]

    es, ei = t["shaft_upper_deviation_um"], t["shaft_lower_deviation_um"]
    checks = {
        "d": (es, lambda i: -16 * d_main(i) ** 0.44),
        "e": (es, lambda i: -11 * d_main(i) ** 0.41),
        "f": (es, lambda i: -5.5 * d_main(i) ** 0.41),
        "g": (es, lambda i: -2.5 * d_main(i) ** 0.34),
        "m": (ei, lambda i: it(i, 7) - it(i, 6)),
        "u": (ei, lambda i: it(i, 7) + d_sub(i)),
        "v": (ei, lambda i: it(i, 7) + 1.25 * d_sub(i)),
        "y": (ei, lambda i: it(i, 7) + 2 * d_sub(i)),
        "za": (ei, lambda i: it(i, 8) + 3.15 * d_sub(i)),
        "zb": (ei, lambda i: it(i, 9) + 4 * d_sub(i)),
        "zc": (ei, lambda i: it(i, 10) + 5 * d_sub(i)),
    }
    first_big = next(i for i, (lo, _) in enumerate(rng) if lo >= 14)  # small sizes use special values
    for letter, (source, formula) in checks.items():
        for i, value in enumerate(source[letter]):
            if value is None or i < first_big:
                continue
            f = formula(i)
            assert abs(value - f) <= max(3.0, 0.04 * abs(value)), (letter, rng[i], value, f)
    for i, value in enumerate(t["shaft_k_lower_deviation_um_it4_to_it7"]):
        assert abs(value - 0.6 * d_main(i) ** (1 / 3)) <= 1.0, (rng[i], value)


# --------------------------------------------------------------------------- isofits cross-check

isofits = pytest.importorskip("isofits", reason="isofits is a test-only oracle (dev dependency group)")


def _iso_um(body: str, size: float, cls: str) -> tuple[float, float]:
    upper, lower = isofits.isotol(body, size, cls, "both")
    return float(upper), float(lower)


# isofits' own tables disagree with the standard in a handful of cells.  Each was recomputed by hand
# from the ISO 286-1 rules and our value is the one the rules give; the sweeps below pin exactly this
# list so any other disagreement, in either direction, fails.
#
#   E7, 315-400 mm: e = +125 um, IT7 = 57 um  -> ES = 125 + 57 = 182 (isofits has 185; the same
#                   source gives E6 +161 = 125 + 36 and E11 +485 = 125 + 360, so 185 is a typo)
#   K6, 6-10 mm:    k = +1, delta = IT6 - IT5 = 9 - 6 = 3 -> ES = -1 + 3 = +2, EI = 2 - 9 = -7 (isofits -6;
#                   RoyMech's copy of the table was corrected to -7)
#   M6, 250-315 mm: m = +20, delta = IT6 - IT5 = 32 - 23 = 9 -> ES = -20 + 9 = -11, EI = -11 - 32 = -43
#                   (isofits and RoyMech -9/-41; the same table's N6 at 250-315 is -25 = -34 + 9, i.e.
#                   it uses delta 9 there, so M6 -9 is a typo; M7, -20 + 20 = 0, agrees)
#   f6, 120-180 mm: f = -43, IT6 = 25 -> ei = -43 - 25 = -68 (isofits -48, not even self-consistent;
#                   RoyMech's copy was corrected to -68)
KNOWN_ISOFITS_ERRORS = {
    ("hole", "E7", 355), ("hole", "E7", 400),
    ("hole", "K6", 10),
    ("hole", "M6", 280), ("hole", "M6", 315),
    ("shaft", "f6", 140), ("shaft", "f6", 160), ("shaft", "f6", 180),
}  # fmt: skip


def _sweep(body: str, table: dict) -> set[tuple[str, str, int]]:
    mismatches = set()
    for cls in (k for k in table if k not in ("over", "inc.")):
        for over, inc in zip(table["over"], table["inc."]):
            for size in (float(inc), (float(over) + float(inc)) / 2 + 0.01):
                if not (float(over) < size <= float(inc)):
                    continue
                theirs = _iso_um(body, size, cls)
                ours = iso286.zone_deviations_um(size, cls)
                if (ours.upper_um, ours.lower_um) != pytest.approx(theirs, abs=1e-9):
                    # The same wrong cell covers a whole isofits range; key it by the range's upper bound.
                    mismatches.add((body, cls, int(float(inc))))
    return mismatches


def test_every_isofits_hole_class_matches_our_table():
    from data import hole_data

    found = _sweep("hole", hole_data)
    assert found == {m for m in KNOWN_ISOFITS_ERRORS if m[0] == "hole"}


def test_every_isofits_shaft_class_matches_our_table():
    from data import shaft_data

    found = _sweep("shaft", shaft_data)
    assert found == {m for m in KNOWN_ISOFITS_ERRORS if m[0] == "shaft"}


def test_isofits_fit_classification_agrees_with_ours():
    for size, hole, shaft in [(25.0, "H7", "g6"), (25.0, "H7", "p6"), (25.0, "H7", "k6"), (60.0, "H8", "f7")]:
        mmc, lmc = isofits.isofit(size, hole, shaft)  # um, mmc = min clearance
        ours = iso286.fit_limits(size, f"{hole}/{shaft}")
        assert ours.min_clearance_um == pytest.approx(mmc)
        assert ours.max_clearance_um == pytest.approx(lmc)
def test_isofits_fit_classification_agrees_with_ours():
    for size, hole, shaft in [(25.0, "H7", "g6"), (25.0, "H7", "p6"), (25.0, "H7", "k6"), (60.0, "H8", "f7")]:
        mmc, lmc = isofits.isofit(size, hole, shaft)  # um, mmc = min clearance
        ours = iso286.fit_limits(size, f"{hole}/{shaft}")
        assert ours.min_clearance_um == pytest.approx(mmc)
        assert ours.max_clearance_um == pytest.approx(lmc)
