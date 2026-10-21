"""dsp_window: window values and properties against the closed forms; the numbers below are derived by hand."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp.window import window
from tests.dsp_testing import fails, ok, table, v

# ---------------------------------------------------------------------------------------------------- values


def test_window_values_by_hand():
    # periodic Hann N = 4: 0.5 - 0.5 cos(2 pi n/4) = [0, 0.5, 1, 0.5];  symmetric Hann N = 5: cos(2 pi n/4) -> [0, 0.5, 1, 0.5, 0]
    assert win.window_values("hann", 4, periodic=True) == pytest.approx([0, 0.5, 1, 0.5], abs=1e-15)
    assert win.window_values("hann", 5) == pytest.approx([0, 0.5, 1, 0.5, 0], abs=1e-15)
    # Hamming N = 5: 0.54 - 0.46 cos(n pi/2) = [0.08, 0.54, 1.0, 0.54, 0.08]
    assert win.window_values("hamming", 5) == pytest.approx([0.08, 0.54, 1.0, 0.54, 0.08], abs=1e-15)
    # Blackman N = 5: n = 1: 0.42 - 0.5 cos(pi/2) + 0.08 cos(pi) = 0.34;  n = 0: 0.42 - 0.5 + 0.08 = 0
    assert win.window_values("blackman", 5) == pytest.approx([0, 0.34, 1.0, 0.34, 0], abs=1e-15)
    assert win.window_values("rectangular", 4).tolist() == [1, 1, 1, 1]
    # Kaiser beta = 8, N = 3: [1/I0(8), 1, 1/I0(8)] with I0(8) = 427.564116
    k = win.window_values("kaiser", 3, beta=8.0)
    assert k[1] == 1.0 and k[0] == pytest.approx(1 / 427.564116, rel=1e-6) and k[0] == k[2]
    assert win.window_values("kaiser", 9, beta=0.0).tolist() == [1.0] * 9
    # flat top at n = 0 is a0 - a1 + a2 - a3 + a4 = -0.0004210; at the centre sum a_k = 1.0000000
    f = win.window_values("flattop", 8, periodic=True)
    assert f[0] == pytest.approx(0.21557895 - 0.41663158 + 0.277263158 - 0.083578947 + 0.006947368, abs=1e-15)
    assert f[4] == pytest.approx(0.21557895 + 0.41663158 + 0.277263158 + 0.083578947 + 0.006947368, abs=1e-15)


@pytest.mark.parametrize("name", ["hann", "hamming", "blackman", "flattop", "kaiser", "rectangular"])
@pytest.mark.parametrize("n", [8, 9, 64, 1001])
def test_symmetric_windows_are_exactly_symmetric(name, n):
    w = win.window_values(name, n, beta=5.0)
    assert np.array_equal(w, w[::-1])


# ---------------------------------------------------------------------------------------------------- properties against the textbook


@pytest.mark.parametrize(
    "name, cg, enbw, scallop",
    [
        # coherent gain = a0;  ENBW = (a0^2 + sum a_k^2/2)/a0^2;  scalloping = -20 log10(W(0.5)/W(0)), W(d) = a0 sinc(d) + sum (a_k/2)(sinc(d-k) + sinc(d+k))
        ("rectangular", 1.0, 1.0, 3.92240),     # sinc(0.5) = 2/pi: 20 log10(pi/2) = 3.9224
        ("hann", 0.5, 1.5, 1.42362),            # (0.25 + 0.125)/0.25 = 1.5;  W(.5)/W(0) = [0.5(.63662) + 0.25(.63662 - .21221)]/0.5 = 0.84883 -> 1.4236 dB
        ("hamming", 0.54, 1.362826, 1.75143),   # (0.2916 + 0.1058)/0.2916
        ("blackman", 0.42, 1.726757, 1.09888),  # (0.1764 + 0.1282)/0.1764
        ("flattop", 0.21557895, 3.770246, 0.00978),
    ],
)
def test_properties_match_the_closed_forms(name, cg, enbw, scallop):
    # N = 1024 periodic against the N -> infinity values: tolerance 1e-6 (coherent gain), 1e-5 (ENBW), 1e-4 dB (scalloping)
    r = ok(window, window=name, length=1024)
    assert v(r, "coherent_gain") == pytest.approx(cg, abs=1e-6)
    assert v(r, "enbw_bins") == pytest.approx(enbw, abs=1e-5)
    assert v(r, "scalloping_loss_db") == pytest.approx(scallop, abs=1e-4)
    assert r.data["agrees_with_textbook"] is True
    assert r.data["amplitude_correction_factor"] == pytest.approx(1 / cg, rel=1e-6)


@pytest.mark.parametrize(
    "name, sidelobe, tol, bw3",
    [
        ("rectangular", -13.2623, 1e-3, 0.8859),  # first side lobe of sinc: |sinc(1.4303)| = 0.21723; sinc(0.44295) = 0.7071 -> 3 dB width 0.8859
        ("hann", -31.467, 5e-3, 1.4406),          # solves sinc(d)/(1 - d^2) = 0.70711 at d = 0.7203
        ("hamming", -42.67, 0.05, 1.3030),        # quoted as about -43 dB in the literature
        ("blackman", -58.11, 0.05, 1.6437),       # quoted as about -58 dB
    ],
)
def test_sidelobe_level_and_3db_bandwidth(name, sidelobe, tol, bw3):
    r = ok(window, window=name, length=2048)
    assert v(r, "highest_sidelobe_db") == pytest.approx(sidelobe, abs=tol)
    assert v(r, "bandwidth_3db_bins") == pytest.approx(bw3, abs=5e-4)
    # the FFT-based numbers agree with the independent closed form to the tolerances the tool states
    assert r.data["reference_comparison"]["highest_sidelobe_db"]["agrees"] is True


def test_flattop_sidelobes_are_below_minus_90_db_and_scalloping_is_negligible():
    r = ok(window, window="flattop", length=1024)
    assert v(r, "highest_sidelobe_db") < -90.0 and v(r, "scalloping_loss_db") < 0.01 and v(r, "enbw_bins") == pytest.approx(3.77, abs=0.005)


def test_kaiser_window_properties_grow_with_beta():
    a = ok(window, window="kaiser", length=1024, kaiser_beta=4.0)
    b = ok(window, window="kaiser", length=1024, kaiser_beta=10.0)
    assert v(b, "enbw_bins") > v(a, "enbw_bins") and v(b, "highest_sidelobe_db") < v(a, "highest_sidelobe_db") < -25.0
    assert a.data["reference_comparison"].startswith("no closed textbook")
    # beta = 0 is the rectangular window
    z = ok(window, window="kaiser", length=256, kaiser_beta=0.0)
    assert v(z, "enbw_bins") == pytest.approx(1.0, abs=1e-12) and v(z, "highest_sidelobe_db") == pytest.approx(-13.26, abs=0.01)


def test_short_and_symmetric_windows_differ_from_the_long_window_values_and_say_so():
    r = ok(window, window="hann", length=32)
    assert any("short window" in w for w in r.warnings)
    s = ok(window, window="hann", length=1025, periodic=False)
    assert s.data["form"] == "symmetric" and v(s, "enbw_bins") == pytest.approx(1.5, abs=0.01)


def test_tables_are_artifacts_with_bounded_rows():
    r = ok(window, window="hann", length=64)
    w = table(r, "window.csv")
    assert len(w["value"]) == 64 and w["value"][32] == pytest.approx(1.0, abs=1e-15)
    t = table(r, "window_response.csv")
    assert t["offset_bins"][0] == 0.0 and t["magnitude_db_rel_peak"][0] == pytest.approx(0.0, abs=1e-12)
    assert t["offset_bins"][-1] == 16.0 and len(t["offset_bins"]) == 513
    # Hann has nulls at integer offsets >= 2 (floored at -300 dB)
    i = int(np.argmin(np.abs(t["offset_bins"] - 3.0)))
    assert t["magnitude_db_rel_peak"][i] < -250


@pytest.mark.parametrize(
    "kwargs, family, param",
    [
        (dict(window="triangle"), "input.invalid", "window"),
        (dict(window="hann", length=7), "input.out_of_range", "length"),
        (dict(window="hann", length=65537), "input.out_of_range", "length"),
        (dict(window="hann", length=1000000000), "input.out_of_range", "length"),
        (dict(window="hann", length=100.5), "input.invalid", "length"),
        (dict(window="hann", kaiser_beta=3.0), "input.invalid", "kaiser_beta"),
        (dict(window="kaiser", kaiser_beta=51.0), "input.invalid", "kaiser_beta"),
        (dict(window="kaiser", kaiser_beta=float("nan")), "input.invalid", "kaiser_beta"),
        (dict(window="kaiser", kaiser_beta=-1.0), "input.invalid", "kaiser_beta"),
        (dict(window="hann", periodic="yes"), "input.invalid", "periodic"),
    ],
)
def test_bad_requests_are_refused(kwargs, family, param):
    fails(window, family, param, **kwargs)
