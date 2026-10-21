"""dsp_filter_design: expected numbers derived by hand (arithmetic in the comments), tolerances stated at each assertion."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _iir as iir
from eng_toolkit.domains.dsp.filter_design import filter_design
from tests.dsp_testing import fails, ok, table, v

FS = "8 kHz"
DB3 = 10.0 * math.log10(0.5)  # |H|^2 = 1/2  ->  -3.0102999566 dB


def gain_db(result, f_hz, fs=8000.0):
    sos = np.array(result.data["sos"])
    return 20.0 * math.log10(abs(iir.sos_response(sos, np.array([2 * math.pi * f_hz / fs]))[0]))


# ---------------------------------------------------------------------------------------------------- Butterworth


@pytest.mark.parametrize("order", [1, 2, 3, 4, 5, 8])
@pytest.mark.parametrize("response", ["lowpass", "highpass"])
def test_butterworth_is_exactly_minus_3_0103_db_at_the_prewarped_cutoff(order, response):
    # |H(jw)|^2 = 1/(1 + (w/wc)^(2n)) -> 1/2 at w = wc for every n; the bilinear map with wc = tan(pi fc/fs) puts that point at fc.
    # 10 log10(1/2) = -3.01029995664 dB.  Tolerance 1e-9 dB (rounding of the SOS evaluation is ~1e-14).
    r = ok(filter_design, family="butterworth", response=response, sample_rate=FS, order=order, cutoff="1 kHz")
    assert gain_db(r, 1000.0) == pytest.approx(DB3, abs=1e-9)
    assert v(r, "gain_at_cutoff_db") == pytest.approx(DB3, abs=1e-9)
    assert r.data["edges_hz"][0] == pytest.approx(1000.0, abs=1e-9)


@pytest.mark.parametrize("response", ["bandpass", "bandstop"])
def test_butterworth_band_edges_are_both_minus_3_0103_db(response):
    r = ok(filter_design, family="butterworth", response=response, sample_rate=FS, order=3, cutoff="900 Hz, 1700 Hz")
    assert gain_db(r, 900.0) == pytest.approx(DB3, abs=1e-9)
    assert gain_db(r, 1700.0) == pytest.approx(DB3, abs=1e-9)
    assert r.data["digital_order"] == 6


def test_butterworth_analog_poles_lie_on_the_left_half_plane_circle_before_the_bilinear_transform():
    # prewarped cutoff wc = tan(pi * 1000/8000) = tan(pi/8) = sqrt(2) - 1 = 0.414213562373; the n poles of 1/(1+(s/j wc)^2n) are wc exp(j pi (2k+n-1)/(2n)).
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=5, cutoff="1 kHz")
    poles = [complex(p["real"], p["imag"]) for p in r.data["analog_poles_before_bilinear"]]
    assert len(poles) == 5
    wc = math.sqrt(2.0) - 1.0
    for p in poles:
        assert abs(p) == pytest.approx(wc, abs=1e-14)  # on the circle |s| = wc
        assert p.real < 0  # left half plane
    angles = sorted(math.degrees(math.atan2(p.imag, -p.real)) for p in poles)
    assert angles == pytest.approx([-72, -36, 0, 36, 72], abs=1e-9)  # spaced pi/n = 36 degrees apart; the real pole at -wc


def test_the_zeros_of_a_butterworth_lowpass_are_all_at_minus_one():
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=4, cutoff="1 kHz")
    assert [z["real"] for z in r.data["zeros"]] == pytest.approx([-1.0] * 4, abs=1e-12)


def test_first_order_butterworth_by_hand():
    # n = 1, wc = tan(pi/8) = 0.41421356: H(s) = wc/(s + wc); s = (z-1)/(z+1):  H(z) = wc (z+1)/((1+wc) z - (1-wc)).
    # b = wc/(1+wc) [1, 1] = 0.29289322 [1, 1];  a = [1, -(1-wc)/(1+wc)] = [1, -0.41421356].   Tolerance 1e-12.
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=1, cutoff="1 kHz")
    wc = math.tan(math.pi / 8)
    assert r.data["b"] == pytest.approx([wc / (1 + wc)] * 2, abs=1e-12)
    assert r.data["a"] == pytest.approx([1.0, -(1 - wc) / (1 + wc)], abs=1e-12)
    assert r.data["sos"][0] == pytest.approx([wc / (1 + wc), wc / (1 + wc), 0.0, 1.0, -(1 - wc) / (1 + wc), 0.0], abs=1e-12)


# ---------------------------------------------------------------------------------------------------- order equations


def test_order_equation_for_a_worked_butterworth_specification():
    # fs = 8 kHz, passband edge 1 kHz with 1 dB, stopband edge 2 kHz with 40 dB.
    # wp = tan(pi/8) = 0.414214, ws = tan(pi/4) = 1, r = ws/wp = 1 + sqrt(2) = 2.414214
    # A = 10^4 - 1 = 9999, E = 10^0.1 - 1 = 0.258925, A/E = 38617.3, ln(A/E) = 10.56146, ln r = 0.881374
    # n = ln(A/E)/(2 ln r) = 10.56146/1.762747 = 5.99147 -> 6.   Exact order tolerance 1e-5, the integer exactly.
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    eq = r.data["order_equation"]
    assert eq["stop_over_pass_ratio"] == pytest.approx(1 + math.sqrt(2), abs=1e-12)
    assert eq["A"] == pytest.approx(9999.0, abs=1e-9) and eq["E"] == pytest.approx(0.258925, abs=1e-6)
    assert eq["exact_order"] == pytest.approx(5.99147, abs=1e-5)
    assert r.data["order"] == 6 and v(r, "order") == 6.0
    # the passband edge is met exactly: -1 dB; the stopband with margin: |H|^2 = 1/(1 + (ws/wc)^12), wc = wp E^(-1/12)
    assert v(r, "worst_passband_edge_gain_db") == pytest.approx(-1.0, abs=1e-9)
    wc = math.tan(math.pi / 8) * (10 ** 0.1 - 1) ** (-1 / 12)
    expected_stop = -10 * math.log10(1 + (1.0 / wc) ** 12)  # = -40.0653 dB
    assert v(r, "worst_stopband_edge_gain_db") == pytest.approx(expected_stop, abs=1e-9)
    assert expected_stop < -40.0


def test_chebyshev_order_equation_for_the_same_specification_is_4():
    # acosh(sqrt(A/E)) = acosh(196.5) = ln(196.5 + sqrt(196.5^2 - 1)) = 5.97387;  acosh(r) = acosh(2.414214) = ln(2.414214 + 2.197368) = 1.52857
    # n = 5.97387/1.52857 = 3.9081 -> 4.   Tolerance 1e-4 on the exact order.
    for fam in ("chebyshev1", "chebyshev2"):
        r = ok(filter_design, family=fam, response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0)
        assert r.data["order_equation"]["exact_order"] == pytest.approx(3.9081, abs=1e-4)
        assert r.data["order"] == 4


def test_chebyshev1_specification_meets_the_passband_exactly_and_the_stopband_with_margin():
    # -|H(ws)|^2 = 1 + E cosh^2(4 acosh r)  ->  gain = -10 log10(1 + 0.258925 cosh^2(4 x 1.528571)) = -41.2195 dB (< -40)
    r = ok(filter_design, family="chebyshev1", response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    assert v(r, "worst_passband_edge_gain_db") == pytest.approx(-1.0, abs=1e-9)
    expected = -10 * math.log10(1 + (10 ** 0.1 - 1) * math.cosh(4 * math.acosh(1 + math.sqrt(2))) ** 2)
    assert v(r, "worst_stopband_edge_gain_db") == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx(-41.2195, abs=1e-4)


def test_chebyshev2_specification_meets_the_passband_exactly_like_scipy_and_the_stopband_with_margin():
    # Chebyshev II prototype with its stopband edge at x_u: loss Rp at the pass edge needs T_n(x_u) = sqrt(A/E), x_u = cosh(acosh(sqrt(A/E))/n)
    # = cosh(5.97387/4) = cosh(1.493467) = (4.4529 + 0.2246)/2 = 2.3387 < r = 2.4142: the stopband edge is then more than 40 dB down (closed form below, tolerance 1e-9)
    r = ok(filter_design, family="chebyshev2", response="lowpass", sample_rate=FS, passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    assert v(r, "worst_passband_edge_gain_db") == pytest.approx(-1.0, abs=1e-9)
    xu = math.cosh(math.acosh(math.sqrt(9999.0 / (10 ** 0.1 - 1))) / 4)
    assert xu == pytest.approx(2.3387, abs=1e-3)
    # |H(w)|^2 = eps^2 T_n^2(1/w)/(1 + eps^2 T_n^2(1/w)), eps^2 = 1/9999, w = x/x_u; at the stop edge x = r = 1 + sqrt(2): 1/w = x_u/r < 1, T_4 = cos(4 acos(x_u/r))
    t = math.cos(4 * math.acos(xu / (1 + math.sqrt(2))))
    e2t2 = t * t / 9999.0
    assert v(r, "worst_stopband_edge_gain_db") == pytest.approx(10 * math.log10(e2t2 / (1 + e2t2)), abs=1e-9)
    assert v(r, "worst_stopband_edge_gain_db") < -40.0


@pytest.mark.parametrize("response, pass_hz, stop_hz", [("highpass", "2 kHz", "1 kHz"), ("bandpass", "1 kHz, 2 kHz", "500 Hz, 2.5 kHz"), ("bandstop", "500 Hz, 3 kHz", "1 kHz, 2 kHz")])
@pytest.mark.parametrize("family", ["butterworth", "chebyshev1", "chebyshev2"])
def test_every_specification_is_met_for_every_response(family, response, pass_hz, stop_hz):
    r = ok(filter_design, family=family, response=response, sample_rate=FS, passband_edge=pass_hz, stopband_edge=stop_hz, passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    assert v(r, "worst_passband_edge_gain_db") >= -1.0 - 1e-9
    assert v(r, "worst_stopband_edge_gain_db") <= -40.0 + 1e-9
    assert all(c["met"] for c in r.data["spec_checks"] if "met" in c)
    # minimal: one order less misses at least one specification (checked by building it and looking at the edges)
    n = r.data["order"]
    assert n >= 1 and r.data["order_equation"]["exact_order"] > n - 1


# ---------------------------------------------------------------------------------------------------- Chebyshev


@pytest.mark.parametrize("order", [3, 4, 5, 6])
def test_chebyshev1_ripple_equals_the_requested_ripple(order):
    # eps^2 = 10^(0.5/10) - 1 = 0.122018;  the passband edge gain is 1/sqrt(1 + eps^2) = 0.944061 (-0.5 dB) exactly
    # and the largest gain in the passband is 1 (0 dB): ripple 10 log10(1 + eps^2) = 0.5 dB.  Tolerances: edge 1e-9 dB, ripple 1e-6 dB (dense grid).
    r = ok(filter_design, family="chebyshev1", response="lowpass", sample_rate="10 kHz", order=order, cutoff="1 kHz", passband_ripple_db=0.5)
    sos = np.array(r.data["sos"])
    edge = abs(iir.sos_response(sos, np.array([2 * math.pi * 1000 / 10000]))[0])
    assert edge == pytest.approx(1 / math.sqrt(1 + (10 ** 0.05 - 1)), abs=1e-12)
    assert edge == pytest.approx(0.9440608763, abs=1e-9)
    grid = np.linspace(1e-6, 2 * math.pi * 1000 / 10000, 200001)
    mag = np.abs(iir.sos_response(sos, grid))
    assert 20 * math.log10(mag.max()) == pytest.approx(0.0, abs=1e-6)
    assert 20 * math.log10(mag.min()) == pytest.approx(-0.5, abs=1e-6)
    assert 20 * math.log10(mag.max() / mag.min()) == pytest.approx(0.5, abs=1e-6)
    # DC gain: 1 for odd n, 1/sqrt(1 + eps^2) for even n
    dc = mag[0]
    assert dc == pytest.approx(1.0 if order % 2 else 0.9440608763, abs=1e-6)


def test_chebyshev2_has_attenuation_rs_at_its_stopband_edge_and_unit_dc_gain():
    r = ok(filter_design, family="chebyshev2", response="lowpass", sample_rate="10 kHz", order=5, cutoff="2 kHz", stopband_attenuation_db=50.0)
    assert gain_db(r, 2000.0, 10000.0) == pytest.approx(-50.0, abs=1e-9)
    assert gain_db(r, 1e-3, 10000.0) == pytest.approx(0.0, abs=1e-9)
    sos = np.array(r.data["sos"])
    beyond = np.abs(iir.sos_response(sos, np.linspace(2 * math.pi * 2000 / 10000, math.pi - 1e-3, 5000)))
    assert 20 * math.log10(beyond.max()) <= -50.0 + 1e-6  # equiripple stopband never exceeds -Rs
    zeros = [complex(z["real"], z["imag"]) for z in r.data["zeros"]]
    assert all(abs(abs(z) - 1.0) < 1e-12 for z in zeros)  # transmission zeros on the unit circle


# ---------------------------------------------------------------------------------------------------- representation


@pytest.mark.parametrize("family", ["butterworth", "chebyshev1", "chebyshev2"])
@pytest.mark.parametrize("response, cut", [("lowpass", "1 kHz"), ("highpass", "1 kHz"), ("bandpass", "900 Hz, 1700 Hz"), ("bandstop", "900 Hz, 1700 Hz")])
def test_the_sos_cascade_equals_the_polynomial_ratio(family, response, cut):
    r = ok(filter_design, family=family, response=response, sample_rate=FS, order=4, cutoff=cut, passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    w = np.linspace(0.01, 3.1, 400)
    h_sos = iir.sos_response(np.array(r.data["sos"]), w)
    h_ba = iir.ba_response(np.array(r.data["b"]), np.array(r.data["a"]), w)
    assert np.max(np.abs(h_sos - h_ba)) < 1e-10  # tolerance 1e-10 (stated); about 1e-14 observed
    assert r.data["stability"]["stable"] and r.data["stability"]["max_pole_radius"] < 1.0
    assert r.data["a"][0] == 1.0


def test_sos_rows_are_normalised_and_poles_zeros_agree_with_the_polynomials():
    r = ok(filter_design, family="chebyshev1", response="bandpass", sample_rate=FS, order=3, cutoff="900 Hz, 1700 Hz", passband_ripple_db=1.0)
    sos = np.array(r.data["sos"])
    assert sos.shape == (3, 6) and np.all(sos[:, 3] == 1.0)  # six poles -> three sections
    poles = np.array([complex(p["real"], p["imag"]) for p in r.data["poles"]])
    assert np.max(np.abs(poles)) == pytest.approx(r.data["stability"]["max_pole_radius"], abs=1e-15)
    a_from_poles = np.real(np.poly(poles))
    assert np.max(np.abs(a_from_poles - np.array(r.data["a"]))) < 1e-12


def test_group_delay_of_a_first_order_section_by_hand():
    # H(z) = (1 + z^-1)/(2 (1 - a z^-1))-like first-order low pass: b = g[1, 1], a = [1, p] with p = -(1-wc)/(1+wc) = -0.41421356.
    # group delay (samples) = Re[S_b/B] - Re[S_a/A];  at w = 0:  numerator (1 + z^-1): 1/2;  denominator 1 + p z^-1: Re[p/(1+p)] = p/(1+p)
    # tau(0) = 1/2 - p/(1+p) = 0.5 + 0.41421356/0.58578644 = 0.5 + 0.7071068 = 1.2071068  (exactly 1/2 + 1/sqrt(2)).  Tolerance 1e-6 (w = 1e-7 rad).
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=1, cutoff="1 kHz", points=16)
    rows = {round(g["frequency_hz"], 6): g for g in r.data["group_delay"]}
    # evaluate by the tool at Nyquist/8 = 500 Hz and compare with a numerical derivative of the unwrapped phase
    h = lambda f: iir.sos_response(np.array(r.data["sos"]), np.array([2 * math.pi * f / 8000]))[0]
    f0, df = 500.0, 1e-3
    dphi = (np.angle(h(f0 + df)) - np.angle(h(f0 - df))) / (2 * math.pi * 2 * df / 8000)
    assert rows[500.0]["group_delay_samples"] == pytest.approx(-dphi, abs=1e-6)
    tau0 = 0.5 + 1 / math.sqrt(2)
    assert iir.group_delay_sos(np.array(r.data["sos"]), 1e-9)[0] == pytest.approx(tau0, abs=1e-6)


def test_magnitude_phase_table_is_a_csv_artifact_with_the_requested_rows():
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=3, cutoff="1 kHz", points=64)
    t = table(r, "filter_response.csv")
    assert list(t) == ["frequency_hz", "magnitude_db", "phase_deg"] and len(t["frequency_hz"]) == 64
    assert t["frequency_hz"][0] == 0.0 and t["frequency_hz"][-1] == pytest.approx(4000.0)
    assert t["magnitude_db"][0] == pytest.approx(0.0, abs=1e-9)
    assert np.all(np.diff(t["magnitude_db"]) <= 1e-12)  # a Butterworth low pass falls monotonically
    assert t["phase_deg"][0] == pytest.approx(0.0, abs=1e-6) and t["phase_deg"][-1] < -180  # unwrapped: three poles and three zeros at -1


# ---------------------------------------------------------------------------------------------------- warnings


def test_warnings_for_ill_conditioned_designs():
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="48 kHz", order=4, cutoff="10 Hz")
    assert any("below 0.1 %" in w for w in r.warnings)  # cutoff near zero
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="48 kHz", order=4, cutoff="23.99 kHz")
    assert any("Nyquist" in w for w in r.warnings)
    r = ok(filter_design, family="butterworth", response="bandpass", sample_rate="48 kHz", order=2, cutoff="1000 Hz, 1100 Hz")
    assert any("narrower than 1 %" in w for w in r.warnings)
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="48 kHz", order=12, cutoff="5 kHz")
    assert any("direct-form" in w for w in r.warnings)


# ---------------------------------------------------------------------------------------------------- units


@pytest.mark.parametrize("rate, cutoff", [("48 kHz", "6 kHz"), ("48000 Hz", "6000 Hz"), ("48000 hz", "6e3 Hz"), ("0.048 MHz", "6 kHz"), (f"{48000 * 2 * math.pi} rad/s", f"{6000 * 2 * math.pi} rad/s")])
def test_frequencies_accept_hz_khz_mhz_and_rad_per_second(rate, cutoff):
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=rate, order=2, cutoff=cutoff)
    assert r.data["sample_rate_hz"] == pytest.approx(48000.0, rel=1e-12)
    assert r.data["edges_hz"][0] == pytest.approx(6000.0, rel=1e-9)


# ---------------------------------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "kwargs, family, param",
    [
        (dict(order=4, cutoff="0 Hz"), "input.invalid", "cutoff"),
        (dict(order=4, cutoff="4 kHz"), "input.invalid", "cutoff"),  # exactly Nyquist
        (dict(order=4, cutoff="5 kHz"), "input.invalid", "cutoff"),
        (dict(order=4, cutoff="-1 kHz"), "input.invalid", "cutoff"),
        (dict(order=41, cutoff="1 kHz"), "input.out_of_range", "order"),
        (dict(order=-1, cutoff="1 kHz"), "input.out_of_range", "order"),
        (dict(order=2.5, cutoff="1 kHz"), "input.invalid", "order"),
        (dict(order=4, cutoff="1000"), "units.bare_number", "cutoff"),
        (dict(order=4, cutoff="1 m"), "units.dimension_mismatch", "cutoff"),
        (dict(order=4, cutoff="abc"), "units.parse_error", "cutoff"),
        (dict(order=4, cutoff="1 kHz, 2 kHz"), "input.invalid", "cutoff"),  # two values for a low pass
        (dict(order=4), "input.missing", "cutoff"),
        (dict(), "input.missing", None),
        (dict(passband_edge="2 kHz", stopband_edge="1 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0), "input.invalid", "stopband_edge"),  # stopband edge inside the passband
        (dict(passband_edge="1 kHz", stopband_edge="1 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0), "input.invalid", "stopband_edge"),
        (dict(passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=40.0, stopband_attenuation_db=40.0), "input.invalid", "passband_ripple_db"),
        (dict(passband_edge="1 kHz", stopband_edge="2 kHz", passband_ripple_db=1.0), "input.missing", "stopband_attenuation_db"),
        (dict(passband_edge="1 kHz", stopband_edge="1.001 kHz", passband_ripple_db=0.01, stopband_attenuation_db=120.0), "input.out_of_range", "stopband_edge"),  # order far above 40
        (dict(order=4, cutoff="1 kHz", passband_ripple_db=float("nan")), "input.invalid", "passband_ripple_db"),
        (dict(order=4, cutoff="1 kHz", points=5), "input.out_of_range", "points"),
    ],
)
def test_bad_requests_are_refused_with_the_right_family(kwargs, family, param):
    base = dict(family="butterworth", response="lowpass", sample_rate=FS)
    fails(filter_design, family, param, **{**base, **kwargs})


@pytest.mark.parametrize("rate, family", [("-8 kHz", "input.invalid"), ("0 Hz", "input.invalid"), ("8 m", "units.dimension_mismatch"), ("8 s", "units.dimension_mismatch"), ("8000", "units.bare_number"), ("1e15 Hz", "input.out_of_range"), ("", "input.missing"), ("nan Hz", "units.parse_error"), ("inf Hz", "units.parse_error")])
def test_bad_sample_rates(rate, family):
    fails(filter_design, family, "sample_rate", family="butterworth", response="lowpass", sample_rate=rate, order=2, cutoff="1 kHz")


def test_chebyshev_and_choice_errors():
    fails(filter_design, "input.missing", "passband_ripple_db", family="chebyshev1", response="lowpass", sample_rate=FS, order=3, cutoff="1 kHz")
    fails(filter_design, "input.missing", "stopband_attenuation_db", family="chebyshev2", response="lowpass", sample_rate=FS, order=3, cutoff="1 kHz")
    fails(filter_design, "input.invalid", "family", family="elliptic", response="lowpass", sample_rate=FS, order=3, cutoff="1 kHz")
    fails(filter_design, "input.invalid", "response", family="butterworth", response="notch", sample_rate=FS, order=3, cutoff="1 kHz")
    fails(filter_design, "input.invalid", "cutoff", family="butterworth", response="bandpass", sample_rate=FS, order=3, cutoff="2 kHz, 1 kHz")  # not increasing


def test_edges_given_with_an_order_are_only_reported_on():
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate=FS, order=4, cutoff="1 kHz", passband_edge="500 Hz", stopband_edge="2 kHz", passband_ripple_db=1.0, stopband_attenuation_db=40.0)
    assert r.data["order"] == 4 and r.data["mode"] == "order and cutoff"
    roles = [c["role"] for c in r.data["spec_checks"]]
    assert roles == ["minus_3db_edge", "passband_edge", "stopband_edge"]
    # n = 4, wc = tan(pi/8) = 0.414214: gain at 2 kHz (ws = 1): -10 log10(1 + (1/0.414214)^8) = -10 log10(1 + 1154.7) = -30.63 dB (tolerance 0.01 dB)
    assert r.data["spec_checks"][2]["gain_db"] == pytest.approx(-30.63, abs=0.01)
    assert r.data["spec_checks"][2]["met"] is False
