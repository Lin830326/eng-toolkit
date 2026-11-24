"""The building blocks of the plot domain: nice ticks, log decades, label thinning, escaping, number formatting, decimation."""

import math
import time

import numpy as np
import pytest

from eng_toolkit.domains.plot._axes import linear_axis, log_axis, nice_step, thin
from eng_toolkit.domains.plot._common import NUMBER, clean_text, number_token
from eng_toolkit.domains.plot._decimate import decimate
from eng_toolkit.domains.plot._svg import esc, fit, num, series_style, text_width
from eng_toolkit.errors import ToolFailure
from timing import budget


def nice(t: float) -> bool:
    """t is 0 or m x 10^e with m in 1, 2, 5 (times an integer count of steps is checked separately)."""
    if t == 0:
        return True
    m = abs(t) / 10 ** math.floor(math.log10(abs(t)) + 1e-12)
    return any(abs(m - c) < 1e-6 for c in (1, 2, 5))  # the step comes from a difference of two ticks


@pytest.mark.parametrize("span,target,expected", [
    (10, 8, (2, 0)),      # raw 1.25 -> next of 1, 2, 5 is 2
    (100, 10, (1, 1)),    # raw 10 -> 1 x 10^1
    (3, 10, (5, -1)),     # raw 0.3 -> 0.5
    (7e-6, 7, (1, -6)),   # raw 1e-6
    (2.4e9, 4, (1, 9)),   # raw 6e8 -> 1e9 (10 rolls over to the next decade)
])
def test_nice_step_is_the_smallest_1_2_5_multiple_above_span_over_target(span, target, expected):
    assert nice_step(span, target) == expected


@pytest.mark.parametrize("lo,hi", [(0.0, 10.0), (-3.7, 12.2), (0.1, 0.3), (1e15, 1e15 + 7e6), (-1e-30, 3e-30), (-1e100, 1e100), (17.0, 17.000001)])
def test_linear_ticks_are_nice_equally_spaced_and_cover_the_data(lo, hi):
    ax = linear_axis(lo, hi, 8, None, param="y_range", what="y axis")
    step = ax.ticks[1] - ax.ticks[0]
    assert ax.lo <= lo and ax.hi >= hi  # covers the data
    assert nice(step * (1 + 1e-12)) or nice(step)
    for a, b in zip(ax.ticks, ax.ticks[1:]):
        assert b - a == pytest.approx(step, rel=1e-6)
    e = math.floor(math.log10(step) + 1e-9)
    exact = float(f"{round(step / 10 ** e)}e{e}")  # the step as the exact 1, 2 or 5 x 10^e it stands for
    assert all(abs(t / exact - round(t / exact)) < 1e-6 for t in ax.ticks)  # ticks are multiples of the step
    assert len(set(ax.labels)) == len(ax.labels)  # labels distinct


def test_linear_ticks_are_exact_decimals():
    # 0.1 .. 0.3, target 4: raw 0.05 -> step 0.05; ticks are k*5e-2 parsed from text, so 0.15 is the double nearest 0.15
    ax = linear_axis(0.1, 0.3, 4, None, param="p", what="y")
    assert ax.ticks == [0.1, 0.15, 0.2, 0.25, 0.3]
    assert ax.labels == ["0.10", "0.15", "0.20", "0.25", "0.30"]


def test_equal_min_and_max_are_padded_and_said_so():
    # all values 5: padded by 10 % -> 4.5 .. 5.5; target 4 -> raw 0.25 -> step 0.5
    ax = linear_axis(5.0, 5.0, 4, None, param="p", what="y axis")
    assert ax.lo == 4.5 and ax.hi == 5.5 and ax.ticks == [4.5, 5.0, 5.5]
    assert "padded" in ax.notes[0]
    zero = linear_axis(0.0, 0.0, 4, None, param="p", what="y axis")
    assert zero.lo == -1.0 and zero.hi == 1.0


def test_a_fixed_range_is_kept_exactly_and_ticks_lie_inside():
    # span 85, target 8: raw 10.625 -> step 20; the multiples of 20 inside -80 .. 5
    ax = linear_axis(-80.0, 5.0, 8, (-80.0, 5.0), param="y_range", what="y")
    assert (ax.lo, ax.hi) == (-80.0, 5.0)
    assert ax.ticks == [-80.0, -60.0, -40.0, -20.0, 0.0]


def test_rounding_noise_above_a_tick_does_not_add_a_tick():
    # a magnitude of 1e-15 dB at DC (rounding noise) must not stretch a -300..0 axis to +50
    ax = linear_axis(-300.0, 1e-15, 6, None, param="p", what="y")
    assert ax.ticks[-1] == 0.0 and ax.hi == 1e-15 and ax.lo == -300.0


def test_tiny_but_nonzero_data_is_refused():
    with pytest.raises(ToolFailure) as err:
        linear_axis(1e-120, 3e-120, 5, None, param="series", what="y axis")
    assert err.value.family == "input.out_of_range"


def test_log_axis_decades_are_equally_spaced():
    ax = log_axis(3.0, 4000.0, 8, None, param="p", what="x")
    assert (ax.lo, ax.hi) == (1.0, 10000.0)  # whole decades around 3 .. 4000
    assert ax.ticks == [1.0, 10.0, 100.0, 1000.0, 10000.0]
    assert ax.labels == ["1", "10", "100", "1000", "10000"]
    assert 2.0 in ax.minor and 9000.0 in ax.minor
    ax.set_pixels(100.0, 500.0)
    px = [ax.px(t) for t in ax.ticks]
    # 4 decades over 400 px: 100 px per decade
    assert px == pytest.approx([100.0, 200.0, 300.0, 400.0, 500.0], abs=1e-9)


def test_log_axis_many_decades_labels_every_other_or_fifth_decade():
    ax = log_axis(1e-12, 1e12, 6, None, param="p", what="x")
    # 24 decades, target 6: stride 5 (24/5 = 4.8 <= 6), labels at 1e-10, 1e-5, 1, 1e5, 1e10
    assert ax.ticks == [1e-10, 1e-5, 1.0, 1e5, 1e10]
    assert ax.labels == ["1e-10", "1e-5", "1", "100000", "1e10"]  # plain decimals for 1e-4 .. 1e5 only
    assert ax.minor == []


def test_log_axis_inside_one_decade_and_constant_values():
    ax = log_axis(200.0, 500.0, 6, (200.0, 500.0), param="p", what="y")
    assert ax.ticks[0] >= 200 and ax.ticks[-1] <= 500 and len(ax.ticks) >= 2
    const = log_axis(100.0, 100.0, 6, None, param="p", what="y")
    assert (const.lo, const.hi) == (10.0, 1000.0) and "padded" in const.notes[0]


def test_log_axis_refuses_a_nonpositive_fixed_range():
    with pytest.raises(ToolFailure):
        log_axis(1.0, 10.0, 5, (0.0, 10.0), param="x_range", what="x")


def test_thinning_keeps_every_kth_label_without_overlap():
    # labels 30 px wide every 20 px: neighbours overlap (20 < 30 + 8); every 2nd: 40 >= 38
    shown = thin([0, 20, 40, 60, 80], [30] * 5, 8)
    assert shown == [True, False, True, False, True]
    assert thin([0, 100], [30, 30], 8) == [True, True]


def test_escaping_and_text_fitting():
    assert esc('<script>alert("x")</script> & \'') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#39;"
    assert text_width("abc", 12) == pytest.approx(3 * 7.2)
    assert text_width("漢字", 12) == pytest.approx(24.0)
    shown, cut = fit("x" * 6000, 100.0, 12)
    assert cut and shown.endswith("…") and text_width(shown, 12) <= 100.0 + 7.2


def test_control_and_bidi_characters_are_replaced():
    text, changed = clean_text("a‮b\x07c\td\ud800e")
    assert changed and text == "a�b�c d�e"
    assert clean_text("plain ünï 漢字")[0] == "plain ünï 漢字"


def test_number_formatting_is_fixed_and_trimmed():
    assert [num(v) for v in (0.0, -0.0, 1.0, 1.5, 2.345, -0.004, 1e30, float("nan"))] == ["0", "0", "1", "1.5", "2.35", "0", "10000000", "0"]


def test_strict_numbers_and_linear_time_on_hostile_tokens():
    for good in ("1", "-1.5", "+.5", "2.", "1e3", "-7.25E-4"):
        assert NUMBER.fullmatch(good)
    for bad in ("nan", "inf", "1_000", "0x10", "１２", "1e", "--1", "1,5", " ", "1e+"):
        assert NUMBER.fullmatch(bad) is None, bad
    start = time.perf_counter()
    for token in ("1" * 6000 + "x", "9" * 6000, "(" * 6000):
        with pytest.raises(ToolFailure):
            number_token(token, "p", "token")
    assert time.perf_counter() - start < budget(0.1)
    with pytest.raises(ToolFailure) as big:
        number_token("1e308", "p", "token")
    assert big.value.family == "input.out_of_range"


def test_series_styles_differ_for_twenty_series():
    styles = [series_style(i) for i in range(20)]
    assert len(set(styles)) == 20
    assert len({(c, d) for c, d, _ in styles}) == 20  # colour + dash alone already differ


def test_decimation_keeps_extrema_and_order():
    rng = np.random.default_rng(7)
    x = np.linspace(0.0, 1.0, 100_000)
    y = np.cumsum(rng.normal(size=x.size))
    px = 50.0 + x * 500.0  # 500 px plot
    keep, method = decimate(x, y, px, 50.0, 500.0)
    assert method is not None and "pixel column" in method
    assert len(keep) <= 4 * 501 and np.all(np.diff(keep) > 0)
    assert y[keep].min() == y.min() and y[keep].max() == y.max()  # exactly, not approximately
    assert keep[0] == 0 and keep[-1] == x.size - 1
    small, none = decimate(x[:1000], y[:1000], px[:1000], 50.0, 500.0)
    assert none is None and len(small) == 1000


def test_decimation_of_unsorted_x_keeps_both_extrema():
    t = np.linspace(0, 40 * np.pi, 50_000)
    x, y = np.cos(t) * t, np.sin(t) * t  # a spiral: x goes back and forth
    keep, method = decimate(x, y, 100 + x, 100.0, 300.0)
    assert "not sorted" in method
    for v in (x, y):
        assert v[keep].min() == v.min() and v[keep].max() == v.max()
