"""dsp_filter_design, FIR branch: window-method taps by hand, Kaiser formulas against the textbook example, measured ripple and attenuation."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp.filter_design import filter_design
from tests.dsp_testing import fails, ok, table, v

FS = "8 kHz"


def taps(r):
    return np.array(r.data["coefficients"])


def test_rectangular_window_lowpass_taps_by_hand():
    # 5 taps, cutoff fs/4 (wc = pi/2): ideal h[m] = (wc/pi) sinc(wc m/pi) = 0.5 sinc(m/2) = [0, 1/pi, 0.5, 1/pi, 0]  (sinc(1) = 0)
    # sum = 0.5 + 2/pi; scaled to unit DC gain: h0 = 0.5/(0.5 + 2/pi) = pi/(pi + 4) = 0.439901,  h1 = (1/pi)/(0.5 + 2/pi) = 2/(pi + 4) = 0.280050.
    # Tolerance 1e-14 (a handful of double-precision operations).
    r = ok(filter_design, family="fir_rectangular", response="lowpass", sample_rate=FS, order=5, cutoff="2 kHz")
    h = taps(r)
    assert h == pytest.approx([0.0, 2 / (math.pi + 4), math.pi / (math.pi + 4), 2 / (math.pi + 4), 0.0], abs=1e-14)
    assert v(r, "num_taps") == 5 and v(r, "group_delay_samples") == 2.0


def test_hamming_window_lowpass_taps_by_hand():
    # Hamming w = 0.54 - 0.46 cos(2 pi n/4) = [0.08, 0.54, 1, 0.54, 0.08];  h_ideal*w = [0, 0.54/pi, 0.5, 0.54/pi, 0] (tap 0 and 4 are 0*0.08)
    # S = 0.5 + 1.08/pi = 0.843775;  h = [0, 0.203717, 0.592566, 0.203717, 0].  Tolerance 1e-14.
    r = ok(filter_design, family="fir_hamming", response="lowpass", sample_rate=FS, order=5, cutoff="2 kHz")
    s = 0.5 + 1.08 / math.pi
    assert taps(r) == pytest.approx([0.0, 0.54 / math.pi / s, 0.5 / s, 0.54 / math.pi / s, 0.0], abs=1e-14)


@pytest.mark.parametrize("family", ["fir_rectangular", "fir_hann", "fir_hamming", "fir_blackman", "fir_kaiser"])
@pytest.mark.parametrize("response, cut", [("lowpass", "1.5 kHz"), ("highpass", "1.5 kHz"), ("bandpass", "1 kHz, 2 kHz"), ("bandstop", "1 kHz, 2 kHz")])
def test_taps_are_exactly_symmetric_and_the_gain_is_one_where_it_should_be(family, response, cut):
    n = 61 if response in ("lowpass", "bandpass") else 63
    r = ok(filter_design, family=family, response=response, sample_rate=FS, order=n, cutoff=cut, stopband_attenuation_db=60.0)
    h = taps(r)
    assert np.array_equal(h, h[::-1])  # exact symmetry, not just to rounding
    lp = r.data["linear_phase"]
    assert lp["symmetric"] and lp["linear_phase"] and lp["type"] == 1 and lp["group_delay_samples"] == (n - 1) / 2
    assert lp["phase_residual"] < 1e-12  # H(w) exp(j w (N-1)/2) is real to 1e-12
    k = np.arange(n)
    center = {"lowpass": 0.0, "bandstop": 0.0, "highpass": math.pi, "bandpass": 2 * math.pi * 1500 / 8000}[response]
    gain = abs(np.sum(h * np.exp(-1j * center * k)))
    assert gain == pytest.approx(1.0, abs=1e-12)  # scaled to unit gain at DC / Nyquist / band centre
    if response == "lowpass":
        assert v(r, "num_taps") == n and r.data["dc_gain"] == pytest.approx(1.0, abs=1e-12)


def test_kaiser_formulas_reproduce_the_textbook_example():
    # Oppenheim & Schafer example: wp = 0.4 pi, ws = 0.6 pi, delta = 0.001  ->  A = -20 log10(0.001) = 60 dB, d_omega = 0.2 pi.
    # beta = 0.1102 (A - 8.7) = 0.1102 x 51.3 = 5.65326;  M = (A - 7.95)/(2.285 d_omega) = 52.05/1.435708 = 36.2539 -> 37, i.e. 38 taps.
    # (fs = 2 Hz makes the edges 0.4 Hz and 0.6 Hz = 0.4 pi and 0.6 pi rad/sample.)   beta to 1e-9, the integers exactly.
    r = ok(filter_design, family="fir_kaiser", response="lowpass", sample_rate="2 Hz", passband_edge="0.4 Hz", stopband_edge="0.6 Hz", stopband_attenuation_db=60.0)
    assert r.data["beta"] == pytest.approx(5.65326, abs=1e-9)
    assert r.data["num_taps"] == 38 and r.data["filter_order"] == 37
    assert r.data["cutoff_minus_6db_hz"] == pytest.approx([0.5], abs=1e-12)  # centre of the transition band
    m = r.data["measurement"]
    assert m["stopband_attenuation_db"] >= 60.0 and m["stopband_attenuation_db"] < 61.0  # measured 60.35 dB
    assert m["passband_max_deviation_db"] <= 20 * math.log10(1.001)  # delta = 0.001 -> 0.00868 dB (measured 0.00837)


@pytest.mark.parametrize("attenuation, beta", [(60.0, 5.65326), (80.0, 7.85726), (40.0, 3.3953210522614574), (21.0, 0.0), (10.0, 0.0)])
def test_kaiser_beta_for_the_three_attenuation_ranges(attenuation, beta):
    # A > 50: 0.1102 (A - 8.7)      -> 60: 5.65326, 80: 7.85726
    # 21 <= A <= 50: 0.5842 (A - 21)^0.4 + 0.07886 (A - 21) -> 40: 0.5842 x 19^0.4 + 0.07886 x 19 = 0.5842 x 3.247143 + 1.49834 = 3.39532
    # A < 21: 0.     Tolerance 1e-9.
    assert win.kaiser_beta(attenuation) == pytest.approx(beta, abs=1e-9)
    assert win.kaiser_attenuation(win.kaiser_beta(attenuation)) == pytest.approx(max(attenuation, 21.0), abs=1e-6) or attenuation < 21.0


@pytest.mark.parametrize("family, expected_taps", [("fir_rectangular", 16), ("fir_hann", 51), ("fir_hamming", 54), ("fir_blackman", 89)])
def test_fixed_window_orders_from_the_transition_width_table(family, expected_taps):
    # fs = 8 kHz, transition 1 kHz -> 1.5 kHz: d_omega = 2 pi 500/8000 = pi/8.  M = c pi/d_omega = 8 c with c = 1.8, 6.2, 6.6, 11
    # (Oppenheim & Schafer table): 14.4 -> 15, 49.6 -> 50, 52.8 -> 53, 88 -> 88; taps = M + 1 = 16, 51, 54, 89.
    r = ok(filter_design, family=family, response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="1.5 kHz")
    assert r.data["num_taps"] == expected_taps
    assert r.data["cutoff_minus_6db_hz"] == pytest.approx([1250.0], abs=1e-9)
    # the table promises about 21, 44, 53, 74 dB of stopband attenuation; measured values are within a few dB of that
    cap = {"fir_rectangular": 21, "fir_hann": 44, "fir_hamming": 53, "fir_blackman": 74}[family]
    assert r.data["measurement"]["stopband_attenuation_db"] > cap - 5.0


def test_measured_ripple_and_attenuation_of_a_hamming_design_match_a_direct_evaluation():
    r = ok(filter_design, family="fir_hamming", response="lowpass", sample_rate=FS, order=101, cutoff="1.5 kHz", passband_edge="1 kHz", stopband_edge="2 kHz")
    h = taps(r)
    w = np.linspace(0, math.pi, 400001)
    mag = np.abs(np.exp(-1j * np.outer(w[::400], np.arange(101))) @ h)  # coarse direct evaluation for the independent check
    f = w[::400] * 8000 / (2 * math.pi)
    stop = mag[f >= 2000].max()
    assert r.data["measurement"]["stopband_attenuation_db"] == pytest.approx(-20 * math.log10(stop), abs=0.05)  # 0.05 dB: the direct grid is coarser
    assert r.data["measurement"]["passband_bands_hz"] == [[0.0, 1000.0]] and r.data["measurement"]["stopband_bands_hz"] == [[2000.0, 4000.0]]


def test_highpass_bandpass_and_bandstop_specifications():
    r = ok(filter_design, family="fir_kaiser", response="highpass", sample_rate=FS, passband_edge="2 kHz", stopband_edge="1.5 kHz", stopband_attenuation_db=50.0)
    assert r.data["num_taps"] % 2 == 1 and r.data["measurement"]["stopband_attenuation_db"] > 49.0
    assert r.data["nyquist_gain"] == pytest.approx(1.0, abs=1e-12)
    r = ok(filter_design, family="fir_kaiser", response="bandpass", sample_rate=FS, passband_edge="1 kHz, 2 kHz", stopband_edge="800 Hz, 2.2 kHz", stopband_attenuation_db=50.0)
    assert r.data["measurement"]["stopband_attenuation_db"] > 49.0
    r = ok(filter_design, family="fir_blackman", response="bandstop", sample_rate=FS, passband_edge="500 Hz, 3 kHz", stopband_edge="1 kHz, 2 kHz")
    assert r.data["num_taps"] % 2 == 1 and r.data["measurement"]["stopband_attenuation_db"] > 65.0


def test_an_even_number_of_taps_for_a_highpass_is_refused_but_the_spec_path_rounds_up_with_a_warning():
    fails(filter_design, "input.invalid", "order", family="fir_hamming", response="highpass", sample_rate=FS, order=64, cutoff="1 kHz")
    r = ok(filter_design, family="fir_hamming", response="bandstop", sample_rate=FS, passband_edge="500 Hz, 3 kHz", stopband_edge="1 kHz, 2 kHz")
    assert r.data["num_taps"] % 2 == 1


def test_fir_limits_and_missing_parameters():
    fails(filter_design, "input.out_of_range", "order", family="fir_hamming", response="lowpass", sample_rate=FS, order=4096, cutoff="1 kHz")
    ok(filter_design, family="fir_hamming", response="lowpass", sample_rate=FS, order=4095, cutoff="1 kHz")
    fails(filter_design, "input.out_of_range", "order", family="fir_hamming", response="lowpass", sample_rate=FS, order=2, cutoff="1 kHz")
    fails(filter_design, "input.missing", "kaiser_beta", family="fir_kaiser", response="lowpass", sample_rate=FS, order=31, cutoff="1 kHz")
    fails(filter_design, "input.out_of_range", "stopband_edge", family="fir_kaiser", response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="1.0001 kHz", stopband_attenuation_db=120.0)
    fails(filter_design, "input.invalid", "stopband_edge", family="fir_kaiser", response="lowpass", sample_rate=FS, passband_edge="2 kHz", stopband_edge="1 kHz", stopband_attenuation_db=60.0)
    fails(filter_design, "input.invalid", "cutoff", family="fir_hann", response="lowpass", sample_rate=FS, order=21, cutoff="4 kHz")


def test_the_fir_table_covers_zero_to_nyquist_with_the_group_delay_phase_slope():
    r = ok(filter_design, family="fir_hamming", response="lowpass", sample_rate=FS, order=41, cutoff="1.5 kHz", points=401)
    t = table(r, "filter_response.csv")
    # linear phase: phase = -w (N-1)/2 in the passband, so at f the phase in degrees is -360 f/fs x 20
    i = int(np.argmin(np.abs(t["frequency_hz"] - 500.0)))
    assert t["phase_deg"][i] == pytest.approx(-360.0 * t["frequency_hz"][i] / 8000.0 * 20.0, abs=1e-6)
    assert t["magnitude_db"][0] == pytest.approx(0.0, abs=1e-9)
    assert r.data["group_delay"] == {"samples": 20.0, "seconds": 20.0 / 8000.0, "constant": True}
