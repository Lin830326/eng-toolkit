"""The polynomial layer of the ``control`` domain: the text parser, input limits, and the exact Routh / Cauchy / Jury counts."""

import random

import numpy as np
import pytest

from eng_toolkit.domains.control import _poly as P
from eng_toolkit.units import UnitsError


def coeffs(text, var="s"):
    return [float(x) for x in P.parse_polynomial(text, param="p", var=var)]


# ------------------------------------------------------------------------------------------------ the parser: what it accepts


@pytest.mark.parametrize(
    "text, expected",
    [
        ("s^2 + 3 s + 2", [1, 3, 2]),
        ("[1, 3, 2]", [1, 3, 2]),
        ("(s+1)(s+2)", [1, 3, 2]),  # s^2 + 2s + s + 2
        ("(s+1)*(s+2)^2", [1, 5, 8, 4]),  # (s+1)(s^2 + 4s + 4) = s^3 + 5 s^2 + 8 s + 4
        ("(s+1)^3", [1, 3, 3, 1]),
        ("2e-1 s^2 + 3*s + .5", [0.2, 3, 0.5]),
        ("-s + 1", [-1, 1]),
        ("s**2 - 1", [1, 0, -1]),
        ("3", [3]),
        ("2 (s + 1)", [2, 2]),
        ("1e-3 s + 1", [1e-3, 1]),
        ("s (s + 4) - s^2", [4, 0]),  # the s^2 terms cancel: order drops
        ("+s", [1, 0]),
        ("2 - -s", [1, 2]),
        ("[0, 1, 2]", [0, 1, 2]),  # a list keeps its leading zero (the denominator rule refuses it elsewhere)
        ("  [ 1 ,\n 2 ]  ", [1, 2]),
        ("4 s^0", [4]),
    ],
)
def test_parser_accepts_and_expands_by_hand(text, expected):
    assert coeffs(text) == pytest.approx(expected, rel=1e-14, abs=0)


def test_parser_variable_z_for_discrete_models():
    assert coeffs("z^2 - 1.2 z + 0.35", var="z") == pytest.approx([1, -1.2, 0.35])


# ------------------------------------------------------------------------------------------------ the parser: what it refuses


@pytest.mark.parametrize(
    "text",
    [
        "s + sin(s)",
        "__import__('os').system('echo hi')",
        "s.real",
        "lambda: 1",
        "open('f')",
        "s + abs(s)",
        "a + 1",
        "e",
        "pi s",
        "s__class__",
        "1; 2",
        "1, 2, 3",
        "1 3 2",  # two numbers in a row: would silently be 6
        "s^2.5",
        "s^-1",
        "s^s",
        "s^2^3",
        "s**2**3",
        "(s + 1",
        "s + 1)",
        "()",
        "s +",
        "* s",
        "",
        "   ",
        "z + 1",  # z in a continuous model
        "s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s s".replace(" s", " z"),
        "s % 2",
        "s // 2",
        "s @ s",
        "s²",
        "ѕ + 1",  # a Cyrillic look-alike of s
        "[1, 2",
        "[1, 'a']",
        "[1, [2]]",
        "[1, true]",
        "[1, null]",
        "[]",
        "[NaN]",
        "[Infinity, 1]",
        "[-Infinity]",
        '{"a": 1}',
    ],
)
def test_parser_refuses_with_input_invalid(text):
    with pytest.raises(UnitsError) as info:
        P.parse_polynomial(text, param="p", var="s")
    assert info.value.family == "input.invalid", (text, info.value.message)
    assert len(info.value.message) < 600  # never echoes a huge input


def test_variable_s_in_a_discrete_model_is_refused():
    with pytest.raises(UnitsError) as info:
        P.parse_polynomial("s + 1", param="p", var="z")
    assert info.value.family == "input.invalid" and "continuous" in info.value.message


HUGE = [
        "x" * 100_000,
        "[" + "1," * 60_000 + "1]",
        "s^41",
        "(s+1)^41",
        "(s+1)^40 * s",
        "1e999",
        "1e13 s",
        "[1e13]",
        "[1e-13, 1]",
        "[1e308]",
        "[1, 0, 1e-300]",
        "[" + ",".join(["1"] * 42) + "]",  # order 41
        "(" * 5000 + "s" + ")" * 5000,
        "[" * 5000 + "]" * 5000,
]


@pytest.mark.parametrize("text", HUGE, ids=[f"case{i}" for i in range(len(HUGE))])
def test_parser_refuses_absurd_sizes_and_magnitudes_as_out_of_range_or_invalid(text):
    with pytest.raises(UnitsError) as info:
        P.parse_polynomial(text, param="p", var="s")
    assert info.value.family in {"input.out_of_range", "input.invalid"}, (text[:30], info.value.family)
    if len(text) > P.MAX_TEXT:
        assert info.value.family == "input.out_of_range"


def test_order_limit_is_exactly_forty():
    ok_text = "[" + ",".join(["1"] * 41) + "]"  # order 40
    assert len(P.parse_polynomial(ok_text, param="p")) == 41
    assert len(P.parse_polynomial("s^40 + 1", param="p")) == 41
    with pytest.raises(UnitsError) as info:
        P.parse_polynomial("[" + ",".join(["1"] * 42) + "]", param="p")
    assert info.value.family == "input.out_of_range"


def test_coefficient_magnitude_rule_is_zero_or_1e_minus_12_to_1e12():
    assert coeffs("[1e12, 1e-12, 0, -1e12]") == pytest.approx([1e12, 1e-12, 0, -1e12])
    for bad in ("[1.0000001e12]", "[1e-13]", "[1, -1e13]"):
        with pytest.raises(UnitsError) as info:
            P.parse_polynomial(bad, param="p")
        assert info.value.family == "input.out_of_range"


# ------------------------------------------------------------------------------------------------ arithmetic and text


def test_arithmetic_helpers():
    a, b = np.array([1.0, 2.0]), np.array([1.0, -3.0, 0.0])
    assert list(P.mul(a, b)) == [1.0, -1.0, -6.0, 0.0]  # (s+2)(s^2-3s)
    assert list(P.add(a, b)) == [1.0, -2.0, 2.0]  # s^2 - 3s + s + 2
    assert list(P.add(np.array([1.0, 1.0]), np.array([-1.0, 0.0]))) == [1.0]  # leading terms cancel
    assert P.polyval(np.array([1.0, 3.0, 2.0]), 2j).real == pytest.approx(-2.0)  # (2j)^2 + 3 (2j) + 2 = -4 + 6j + 2 = -2 + 6j
    assert P.polyval(np.array([1.0, 3.0, 2.0]), 2j).imag == pytest.approx(6.0)
    assert P.to_text(np.array([1.0, -3.0, 0.0, 2.5]), "s") == "s^3 - 3 s^2 + 2.5"
    assert P.trailing_zeros(np.array([1.0, 0.0, 0.0])) == 2
    assert P.lowest_order(np.array([2.0, 3.0, 0.0])) == (1, 3.0)
    assert P.multiplicity_at(np.poly([1.0, 1.0, 0.5]), 1.0) == 2  # (z-1)^2 (z-0.5)


# ------------------------------------------------------------------------------------------------ Routh: hand arrays


def test_routh_array_by_hand():
    # s^3 + 4 s^2 + s - 6 = (s-1)(s+2)(s+3).  rows: s^3 [1, 1]; s^2 [4, -6]; s^1 [(4*1 - 1*(-6))/4 = 2.5, 0]; s^0 [(2.5*(-6) - 4*0)/2.5 = -6]
    r = P.routh_counts([1, 4, 1, -6])
    assert r["array"] == [[1.0, 1.0], [4.0, -6.0], [2.5, 0.0], [-6.0, 0.0]]
    assert r["first_column"] == [1.0, 4.0, 2.5, -6.0]
    assert (r["rhp"], r["imag"], r["lhp"]) == (1, 0, 2)  # one sign change: the root at +1
    assert P.half_plane_counts([1, 4, 1, -6]) == {"rhp": 1, "imag": 0, "imag_distinct": 0, "lhp": 2, "exact": True}


def test_routh_zero_row_uses_the_auxiliary_polynomial():
    # s^4 + 5 s^2 + 4 = (s^2+1)(s^2+4): the s^3 row is zero; auxiliary s^4 + 5 s^2 + 4, derivative 4 s^3 + 10 s -> row [4, 10]
    #   s^2: (4*5 - 1*10)/4 = 2.5, (4*4 - 0)/4 = 4 ; s^1: (2.5*10 - 4*4)/2.5 = 3.6 ; s^0: 4 -> no sign change, four roots on the axis
    r = P.routh_counts([1, 0, 5, 0, 4])
    assert r["first_column"] == [1.0, 4.0, 2.5, 3.6, 4.0]
    assert (r["rhp"], r["imag"], r["lhp"]) == (0, 4, 0)
    # (s^2+1)(s-1)(s+2) = s^4 + s^3 - s^2 + s - 2: one RHP root, two on the axis, one LHP
    r = P.routh_counts([1, 1, -1, 1, -2])
    assert (r["rhp"], r["imag"], r["lhp"]) == (1, 2, 1)
    assert P.half_plane_counts([1, 1, -1, 1, -2])["imag"] == 2


def test_the_array_can_be_degenerate_but_the_count_is_not():
    # s (s^2 + 1)^... : the zero row is followed by a row whose first element is zero (needs a symbolic epsilon in Routh's array)
    p = [2, 16, 100, 316, 744, 820, -100, -316, -746, -836]  # roots include +1, +-j
    c = P.half_plane_counts(p)
    roots = np.roots(p)
    assert c["exact"] and c["rhp"] == int(np.sum(roots.real > 1e-6)) == 1
    assert c["imag"] == int(np.sum(np.abs(roots.real) <= 1e-6)) == 2
    r = P.routh_counts(p)
    assert r["exact"] is False  # the array alone cannot decide this one; the Cauchy-index count can


def test_repeated_axis_roots_are_counted_with_multiplicity_and_distinct():
    c = P.half_plane_counts([1, 0, 2, 0, 1])  # (s^2+1)^2
    assert (c["rhp"], c["imag"], c["imag_distinct"], c["lhp"]) == (0, 4, 2, 0)
    c = P.half_plane_counts([1, 2, 1, 0, 0])  # s^2 (s+1)^2
    assert (c["rhp"], c["imag"], c["imag_distinct"], c["lhp"]) == (0, 2, 1, 2)


def test_constant_and_linear_polynomials():
    assert P.half_plane_counts([5])["lhp"] == 0
    assert P.half_plane_counts([2, 6]) == {"rhp": 0, "imag": 0, "imag_distinct": 0, "lhp": 1, "exact": True}
    assert P.half_plane_counts([-2, 6])["rhp"] == 1
    assert P.half_plane_counts([1, 0])["imag"] == 1


# ------------------------------------------------------------------------------------------------ Routh against numpy roots


def _random_polynomial(rng: random.Random):
    """A real polynomial of order 2..12 built from random factors: stable and unstable real roots, complex pairs, simple imaginary pairs, a root at 0."""
    n = rng.randint(2, 12)
    poly = np.array([1.0])
    used: set = set()
    m = 0
    while m < n:
        kind = rng.choice(["lhp", "lhp", "rhp", "imag", "origin", "pair", "rpair"])
        if kind == "lhp":
            f = (1, rng.randint(1, 4))
            m += 1
        elif kind == "rhp":
            f = (1, -rng.randint(1, 4))
            m += 1
        elif kind == "origin":
            f = (1, 0)
            m += 1
        elif kind == "imag" and m <= n - 2:
            f = (1, 0, rng.randint(1, 3) ** 2)
            m += 2
        elif kind == "pair" and m <= n - 2:
            f = (1, 2 * rng.randint(1, 3), rng.randint(5, 20))
            m += 2
        elif kind == "rpair" and m <= n - 2:
            f = (1, -2 * rng.randint(1, 3), rng.randint(5, 20))
            m += 2
        else:
            continue
        if kind in {"rhp", "imag", "origin", "rpair"}:  # distinct, so numpy's roots are reliable
            if f in used:
                continue
            used.add(f)
        poly = np.convolve(poly, f)
    return poly * rng.choice([1.0, 1.0, -1.0, 2.0, 0.5])


def test_routh_matches_numpy_roots_on_200_random_polynomials():
    rng = random.Random(20261020)
    zero_rows = axis = 0
    for _ in range(200):
        poly = _random_polynomial(rng)
        roots = np.roots(poly)
        expected = (int(np.sum(roots.real > 1e-6)), int(np.sum(np.abs(roots.real) <= 1e-6)), int(np.sum(roots.real < -1e-6)))
        exact = P.half_plane_counts(poly)
        assert exact["exact"]
        assert (exact["rhp"], exact["imag"], exact["lhp"]) == expected, poly
        array = P.routh_counts(poly)
        if array["exact"]:  # the array agrees with the count whenever it is conclusive
            assert (array["rhp"], array["imag"], array["lhp"]) == expected, poly
        if exact["imag"]:
            axis += 1
        if array["zero_rows"]:
            zero_rows += 1
    assert axis >= 30, "the generator must include roots on the imaginary axis"
    assert zero_rows >= 10, "the generator must include zero rows"


def test_cauchy_count_and_array_agree_on_2000_random_polynomials():
    rng = random.Random(7)
    conclusive = 0
    for _ in range(2000):
        poly = _random_polynomial(rng)
        roots = np.roots(poly)
        expected = (int(np.sum(roots.real > 1e-6)), int(np.sum(np.abs(roots.real) <= 1e-6)), int(np.sum(roots.real < -1e-6)))
        exact = P.half_plane_counts(poly)
        assert (exact["rhp"], exact["imag"], exact["lhp"]) == expected, poly
        array = P.routh_counts(poly)
        if array["exact"]:
            conclusive += 1
            assert (array["rhp"], array["imag"], array["lhp"]) == expected, poly
    assert conclusive > 1800  # the array is conclusive in nearly all cases


def test_general_random_coefficients_order_2_to_12():
    rng = np.random.default_rng(3)
    for _ in range(300):
        n = int(rng.integers(2, 13))
        poly = rng.normal(size=n + 1)
        poly[0] = abs(poly[0]) + 0.1
        roots = np.roots(poly)
        if np.min(np.abs(roots.real)) < 1e-5:
            continue  # too close to the axis for the floating-point reference
        exact = P.half_plane_counts(poly)
        assert exact["rhp"] == int(np.sum(roots.real > 0)) and exact["imag"] == 0
        array = P.routh_counts(poly)
        assert array["exact"] and array["rhp"] == exact["rhp"]


# ------------------------------------------------------------------------------------------------ Jury and the unit-circle count


def test_jury_by_hand():
    # z^2 - 1.2 z + 0.35 (roots 0.7 and 0.5): p(1) = 1 - 1.2 + 0.35 = 0.15 > 0 ; p(-1) = 1 + 1.2 + 0.35 = 2.55 > 0 ; |a0| = 0.35 < 1
    j = P.jury_table([1, -1.2, 0.35])
    assert j["stable"] and [c["holds"] for c in j["conditions"]] == [True, True, True]
    assert j["conditions"][0]["lhs"] == pytest.approx(0.15, abs=1e-15)
    assert j["conditions"][1]["lhs"] == pytest.approx(2.55, abs=1e-15)
    # z^2 - 2 z + 1.01: roots 1 +- 0.1 j, |z| = sqrt(1.01) > 1.  p(1) = 0.01 > 0, p(-1) = 4.01 > 0, but |a0| = 1.01 > 1 fails
    j = P.jury_table([1, -2, 1.01])
    assert not j["stable"] and [c["holds"] for c in j["conditions"]] == [True, True, False]
    # root exactly at z = 1: p(1) = 0 is not > 0
    assert not P.jury_table([1, -1.5, 0.5])["stable"]
    # a negative leading coefficient is normalised
    assert P.jury_table([-1, 1.2, -0.35])["stable"]


def test_unit_circle_counts_by_hand():
    # (z-2)(z-0.5)(z+1) = z^3 - 1.5 z^2 - 1.5 z + 1 : one outside, one on the circle (z = -1), one inside
    c = P.unit_circle_counts([1, -1.5, -1.5, 1])
    assert (c["outside"], c["on_circle"], c["inside"], c["on_circle_distinct"]) == (1, 1, 1, 1)
    # (z-1)^2 (z-0.5): a double root on the circle
    c = P.unit_circle_counts(np.poly([1.0, 1.0, 0.5]))
    assert (c["outside"], c["on_circle"], c["on_circle_distinct"], c["inside"]) == (0, 2, 1, 1)
    # z^2: both roots at the origin, inside
    assert P.unit_circle_counts([1, 0, 0])["inside"] == 2


def test_jury_and_unit_circle_agree_with_numpy_on_random_dyadic_roots():
    rng = random.Random(11)
    menu = [0.5, -0.5, 0.25, 0.0, 0.75, -0.75, 1.0, -1.0, 2.0, -2.0, 3.0]
    for _ in range(1000):
        n = rng.randint(1, 8)
        roots = [rng.choice(menu) for _ in range(n)]
        poly = np.poly(roots)  # dyadic roots: the coefficients are exact in binary
        mags = np.abs(roots)
        expected = (int(np.sum(mags > 1)), int(np.sum(mags == 1)), int(np.sum(mags < 1)))
        c = P.unit_circle_counts(poly)
        assert (c["outside"], c["on_circle"], c["inside"]) == expected, roots
        assert P.jury_table(poly)["stable"] == (expected[0] == 0 and expected[1] == 0), roots


def test_numbers_beyond_float_range_in_the_array_are_text_not_infinity():
    # 40th order with coefficients spanning 1e24: the Routh entries are exact fractions that can exceed a float
    poly = np.poly(np.arange(1, 41) * 0.3)
    poly = np.where(np.abs(poly) > 1e12, np.sign(poly) * 1e12, poly)
    r = P.routh_counts(poly)
    for row in r["array"]:
        for x in row:
            assert isinstance(x, (float, str))
            if isinstance(x, float):
                assert np.isfinite(x)


# ------------------------------------------------------------------------------------------------ high order: exact and fast


def test_array_and_cauchy_count_agree_for_orders_13_to_40():
    rng = np.random.default_rng(5)
    for n in range(13, 41, 3):
        for _ in range(5):
            poly = rng.normal(size=n + 1)
            poly[0] = abs(poly[0]) + 0.5
            exact = P.half_plane_counts(poly)
            assert exact["exact"] and exact["rhp"] + exact["imag"] + exact["lhp"] == n
            array = P.routh_counts(poly)
            if array["exact"]:
                assert (array["rhp"], array["imag"], array["lhp"]) == (exact["rhp"], exact["imag"], exact["lhp"]), n


def test_the_jury_table_is_capped_but_the_unit_circle_count_still_decides():
    import time

    poly = np.poly([0.5] * 7 + [-0.25] * 7)  # order 14, all inside
    j = P.jury_table(poly)
    assert j["stable"] is None and j["omitted"] is True and "order 12" in j["reason"]
    assert P.unit_circle_counts(poly)["inside"] == 14
    rng = np.random.default_rng(8)
    start = time.time()
    for _ in range(5):
        c = P.unit_circle_counts(rng.normal(size=41))  # order 40 with full 53-bit coefficients
        assert c["exact"] and c["outside"] + c["on_circle"] + c["inside"] == 40
    assert time.time() - start < 20.0
