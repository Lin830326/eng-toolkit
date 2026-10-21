"""dsp_filter_apply: recursion by hand, SOS against (b, a), zero-phase symmetry and edges, FIR delay, unstable filters, limits."""

import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _apply as ap
from eng_toolkit.domains.dsp.filter_apply import filter_apply
from eng_toolkit.domains.dsp.filter_design import filter_design
from tests.dsp_testing import fails, js, ok, sine, table, v


def out(r):
    return table(r, "filtered.csv")["output"]


# ---------------------------------------------------------------------------------------------------- recursion by hand


def test_one_pole_recursion_by_hand():
    # y[n] = x[n] + 0.5 y[n-1], x = unit impulse: y = [1, 0.5, 0.25, 0.125, 0.0625, 0.03125]  (exact in binary floating point)
    for kwargs in (dict(b="[1]", a="[1, -0.5]"), dict(sos="[[1, 0, 0, 1, -0.5, 0]]")):
        r = ok(filter_apply, samples="[1, 0, 0, 0, 0, 0]", **kwargs)
        assert out(r).tolist() == [1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125]
        assert r.data["stability"]["max_pole_radius"] == 0.5 and r.data["stability"]["verdict"] == "stable"
        assert r.data["dc_gain"] == pytest.approx(2.0, abs=1e-6)  # 1/(1 - 0.5)


def test_biquad_recursion_by_hand():
    # y[n] = x[n] + 0.5 x[n-1] + 0.25 y[n-1] - 0.125 y[n-2]; impulse: y0 = 1; y1 = 0.5 + 0.25 = 0.75; y2 = 0.25 (0.75) - 0.125 (1) = 0.0625;
    # y3 = 0.25 (0.0625) - 0.125 (0.75) = -0.078125.   Direct form II transposed must give the same numbers (exact in binary).
    for kwargs in (dict(b="[1, 0.5]", a="[1, -0.25, 0.125]"), dict(sos="[[1, 0.5, 0, 1, -0.25, 0.125]]")):
        r = ok(filter_apply, samples="[1, 0, 0, 0]", **kwargs)
        assert out(r).tolist() == [1.0, 0.75, 0.0625, -0.078125]


def test_moving_average_fir_by_hand():
    # y[n] = (x[n] + x[n-1] + x[n-2])/3 from a zero state: x = [3, 6, 9, 12] -> [1, 3, 6, 9]   (tolerance 1e-15)
    r = ok(filter_apply, samples="[3, 6, 9, 12]", b="[0.3333333333333333, 0.3333333333333333, 0.3333333333333333]")
    assert out(r) == pytest.approx([1.0, 3.0, 6.0, 9.0], abs=1e-15)
    assert r.data["delay"]["group_delay_samples"] == 1.0 and r.data["delay"]["linear_phase"] is True
    assert any("delayed by 1 samples" in w for w in r.warnings)


def test_a_denominator_not_normalised_is_normalised():
    r = ok(filter_apply, samples="[1, 0, 0, 0]", b="[2]", a="[2, -1]")  # same as b = [1], a = [1, -0.5]
    assert out(r).tolist() == [1.0, 0.5, 0.25, 0.125]


# ---------------------------------------------------------------------------------------------------- SOS against (b, a)


def reference_lfilter(b, a, x):
    """Straight difference equation, an independent implementation (not the transposed form)."""
    y = np.zeros(len(x))
    for n in range(len(x)):
        acc = sum(b[k] * x[n - k] for k in range(len(b)) if n - k >= 0) - sum(a[k] * y[n - k] for k in range(1, len(a)) if n - k >= 0)
        y[n] = acc / a[0]
    return y


@pytest.mark.parametrize("family, response, cut", [("butterworth", "lowpass", "1 kHz"), ("chebyshev1", "highpass", "1 kHz"), ("butterworth", "bandpass", "900 Hz, 1700 Hz")])
def test_sos_and_polynomial_forms_give_the_same_output_as_the_difference_equation(family, response, cut):
    d = ok(filter_design, family=family, response=response, sample_rate="8 kHz", order=3, cutoff=cut, passband_ripple_db=1.0)
    rng = np.random.default_rng(11)
    x = rng.normal(size=400)
    expected = reference_lfilter(d.data["b"], d.data["a"], x)
    by_sos = out(ok(filter_apply, samples=js(x), sos=json.dumps(d.data["sos"])))
    by_ba = out(ok(filter_apply, samples=js(x), b=json.dumps(d.data["b"]), a=json.dumps(d.data["a"])))
    by_design = out(ok(filter_apply, samples=js(x), sample_rate="8 kHz", family=family, response=response, order=3, cutoff=cut, passband_ripple_db=1.0))
    assert np.max(np.abs(by_ba - expected)) < 1e-10  # same polynomials, different order of operations (DF2T vs the plain difference equation): 1e-10 stated, about 5e-12 observed
    assert np.max(np.abs(by_sos - expected)) < 1e-9   # sections vs polynomial: rounding of the polynomial, 1e-9 stated
    assert np.max(np.abs(by_design - by_sos)) == 0.0


def test_filter_design_job_is_referenced_and_warnings_are_carried_over():
    r = ok(filter_apply, samples=js(sine(1, 5, 256)), sample_rate="1 kHz", family="butterworth", response="lowpass", order=12, cutoff="100 Hz")
    assert r.data["design_job_id"] and r.data["sections"] == 6
    assert any(w.startswith("design: ") for w in r.warnings)


def test_output_artifact_and_statistics():
    x = sine(1.0, 8, 256)
    r = ok(filter_apply, samples=js(x), sample_rate="256 Hz", sos="[[0.5, 0.5, 0, 1, 0, 0]]")  # y = (x[n] + x[n-1])/2
    t = table(r, "filtered.csv")
    assert list(t) == ["index", "time_s", "input", "output"] and len(t["index"]) == 256 and t["time_s"][1] == pytest.approx(1 / 256)
    assert t["input"] == pytest.approx(x, abs=1e-11)
    assert r.data["input"]["rms"] == pytest.approx(math.sqrt(0.5), rel=1e-3)
    # gain of (1 + z^-1)/2 at 8 cycles per 256 samples: cos(pi 8/256) = 0.99518; the rms of the output (after the first sample) follows
    assert v(r, "rms_gain") == pytest.approx(math.cos(math.pi * 8 / 256), abs=2e-3)
    assert r.data["output"]["rms"] == pytest.approx(v(r, "output_rms"), abs=1e-15)


# ---------------------------------------------------------------------------------------------------- zero phase


def test_zero_phase_filtering_of_a_symmetric_pulse_stays_symmetric():
    # A pulse symmetric about the centre sample passes through a forward-backward filter without losing its symmetry: y[n] = y[N-1-n]
    # (the odd extension of a symmetric record is symmetric, and the steady-state start values at both ends are equal) provided the filter's
    # response has died out inside the record: here the poles have radius < 0.5, so after 100 samples 0.5^100 = 8e-31.  Tolerance 1e-12.
    n = 201
    t = np.arange(n) - 100.0
    x = np.exp(-(t / 12.0) ** 2) + 0.3 * (np.abs(t) < 5)
    sos = json.dumps(ok(filter_design, family="butterworth", response="lowpass", sample_rate="1 kHz", order=4, cutoff="150 Hz").data["sos"])
    for kwargs in (dict(sos=sos), dict(b="[0.2, 0.2, 0.2, 0.2, 0.2]")):
        r = ok(filter_apply, samples=js(x), zero_phase=True, **kwargs)
        y = out(r)
        assert np.max(np.abs(y - y[::-1])) < 1e-12
        assert int(np.argmax(y)) == 100  # the peak did not move: no delay
        assert r.data["delay"]["group_delay_samples"] == 0.0
    # whereas the causal filter delays the pulse
    yc = out(ok(filter_apply, samples=js(x), sos=sos))
    assert int(np.argmax(yc)) > 100


def test_zero_phase_squares_the_magnitude_and_a_constant_passes_unchanged():
    # sine exactly at the -3.0103 dB cutoff: causal amplitude 1/sqrt(2), forward-backward amplitude |H|^2 = 1/2 (tolerance 1e-6 away from the ends).
    d = ok(filter_design, family="butterworth", response="lowpass", sample_rate="1 kHz", order=3, cutoff="100 Hz")
    n = 2000
    x = np.sin(2 * np.pi * 100.0 * np.arange(n) / 1000.0)
    y = out(ok(filter_apply, samples=js(x), sos=json.dumps(d.data["sos"]), zero_phase=True))
    amp = math.sqrt(2.0) * float(np.sqrt(np.mean(y[500:1500] ** 2)))  # 100 whole cycles: rms x sqrt(2) is the amplitude
    assert amp == pytest.approx(0.5, abs=1e-6)
    # edge handling: the steady-state start makes a constant (DC gain 1) come out as that constant at every sample, ends included
    r = ok(filter_apply, samples=js(np.full(300, 5.0)), sos=json.dumps(d.data["sos"]), zero_phase=True)
    assert np.max(np.abs(out(r) - 5.0)) < 1e-12
    assert "odd reflection" in r.data["edge_handling"] and r.data["steady_state_initial_conditions"] is True
    # the causal filter shows the start-up transient instead
    assert abs(out(ok(filter_apply, samples=js(np.full(300, 5.0)), sos=json.dumps(d.data["sos"])))[0] - 5.0) > 1.0


def test_zero_phase_needs_a_record_longer_than_the_extension():
    # one section with b2 != 0: padlen = 3 (2 x 1 + 1) = 9, so 9 samples are too few and 10 are enough; b = [0.5, 0.5]: padlen 3 x 2 = 6
    fails(filter_apply, "input.invalid", "samples", samples=js(np.ones(9)), sos="[[0.1, 0.2, 0.1, 1, -0.5, 0.2]]", zero_phase=True)
    ok(filter_apply, samples=js(np.ones(10)), sos="[[0.1, 0.2, 0.1, 1, -0.5, 0.2]]", zero_phase=True)
    fails(filter_apply, "input.invalid", "samples", samples=js(np.ones(6)), b="[0.5, 0.5]", zero_phase=True)
    ok(filter_apply, samples=js(np.ones(7)), b="[0.5, 0.5]", zero_phase=True)


# ---------------------------------------------------------------------------------------------------- FIR delay


def test_fir_group_delay_is_reported_and_removed_on_request():
    h = ok(filter_design, family="fir_hamming", response="lowpass", sample_rate="8 kHz", order=63, cutoff="1 kHz").data["coefficients"]
    x = np.zeros(300)
    x[100] = 1.0
    r = ok(filter_apply, samples=js(x), b=json.dumps(h))
    assert r.data["delay"]["group_delay_samples"] == 31.0 and v(r, "group_delay_samples") == 31.0
    y = out(r)
    assert int(np.argmax(y)) == 131 and y[131] == pytest.approx(h[31], abs=1e-15)  # impulse response, delayed by (63-1)/2
    rc = ok(filter_apply, samples=js(x), b=json.dumps(h), compensate_delay=True)
    yc = out(rc)
    assert int(np.argmax(yc)) == 100 and yc[100] == pytest.approx(h[31], abs=1e-15) and yc[69] == pytest.approx(h[0], abs=1e-15)
    assert rc.data["delay"]["compensated_samples"] == 31


def test_even_length_fir_delay_cannot_be_removed():
    r = ok(filter_apply, samples=js(np.arange(20.0)), b="[0.5, 0.5]", compensate_delay=True)
    assert any("not a whole number" in w for w in r.warnings) and r.data["delay"]["group_delay_samples"] == 0.5


def test_long_fir_by_fft_matches_direct_convolution():
    rng = np.random.default_rng(5)
    x = rng.normal(size=5000)
    h = rng.normal(size=1500) / 100.0
    r = ok(filter_apply, samples=js(x), b=json.dumps(h.tolist()))
    assert np.max(np.abs(out(r) - np.convolve(x, h)[:5000])) < 1e-12  # FFT convolution: tolerance 1e-12 absolute for unit-variance data


# ---------------------------------------------------------------------------------------------------- stability


@pytest.mark.parametrize("a, radius, verdict", [("[1, -1.5]", 1.5, "unstable"), ("[1, -1]", 1.0, "marginal"), ("[1, 0, 1]", 1.0, "marginal"), ("[1, -2.5, 1]", 2.0, "unstable")])
def test_unstable_and_marginal_filters_are_refused(a, radius, verdict):
    # roots: 1.5; 1; +-j (radius 1); z^2 - 2.5 z + 1 = (z - 2)(z - 0.5) -> 2 and 0.5
    r = fails(filter_apply, "input.invalid", "allow_unstable", samples="[1, 0, 0, 0, 0]", b="[1]", a=a)
    assert r.error.details["verdict"] == verdict and r.error.details["max_pole_radius"] == pytest.approx(radius, abs=1e-9)
    assert "allow_unstable" in r.error.message


def test_an_unstable_filter_can_be_run_on_request_and_overflow_is_refused():
    r = ok(filter_apply, samples="[1, 0, 0, 0, 0]", b="[1]", a="[1, -1.5]", allow_unstable=True)
    assert out(r).tolist() == [1.0, 1.5, 2.25, 3.375, 5.0625]
    assert any("unstable filter" in w for w in r.warnings)
    fails(filter_apply, "input.out_of_range", "allow_unstable", samples=js(np.ones(2000)), b="[1]", a="[1, -1.5]", allow_unstable=True)  # 1.5^2000 overflows
    fails(filter_apply, "input.invalid", "zero_phase", samples=js(np.ones(50)), b="[1]", a="[1, -1.5]", allow_unstable=True, zero_phase=True)


def test_an_unstable_section_inside_a_cascade_is_found():
    fails(filter_apply, "input.invalid", "allow_unstable", samples="[1, 0, 0]", sos="[[1, 0, 0, 1, -0.5, 0], [1, 0, 0, 1, -2.2, 1.21]]")  # second pair: radius 1.1


def test_an_fir_is_never_unstable():
    assert ok(filter_apply, samples="[1, 0, 0]", b="[1e6, -1e6]").data["stability"]["verdict"] == "stable"


# ---------------------------------------------------------------------------------------------------- limits and refusals


def test_the_recursion_work_limit(monkeypatch):
    monkeypatch.setattr(ap, "WORK_LIMIT", 1000)
    fails(filter_apply, "input.out_of_range", "samples", samples=js(np.ones(2000)), sos="[[1, 0, 0, 1, -0.5, 0]]")
    ok(filter_apply, samples=js(np.ones(200)), sos="[[1, 0, 0, 1, -0.5, 0]]")


@pytest.mark.parametrize(
    "kwargs, family, param",
    [
        (dict(), "input.missing", "sos"),
        (dict(sos="[[1, 0, 0, 1, 0]]"), "input.invalid", "sos"),
        (dict(sos="[[1, 0, 0, 1, 0, 0]]", b="[1]"), "input.invalid", "b"),
        (dict(sos="[[1, 0, 0, 0, 0, 0]]"), "input.invalid", "sos"),
        (dict(sos="[[1, 0, 0, 1, NaN, 0]]"), "input.invalid", "sos"),
        (dict(sos="[[1, 0, 0, 1, 1e999, 0]]"), "input.invalid", "sos"),
        (dict(sos="[[1, 0, 0, 1, 1e13, 0]]"), "input.out_of_range", "sos"),
        (dict(sos="[[1, 0, 0, 1, 'a', 0]]"), "input.invalid", "sos"),
        (dict(sos="[[1, 0, 0, 1, true, 0]]"), "input.invalid", "sos"),
        (dict(sos="[" + ",".join(["[1,0,0,1,0,0]"] * 41) + "]"), "input.out_of_range", "sos"),
        (dict(sos="__import__('os')"), "input.invalid", "sos"),
        (dict(sos="[" * 5000 + "]" * 5000), "input.invalid", "sos"),
        (dict(b="[1, 2]", a="[0, 1]"), "input.invalid", "a"),
        (dict(a="[1, 0.5]", sos="[[1, 0, 0, 1, 0, 0]]"), "input.invalid", "b"),
        (dict(b="[1]", a="[1" + ", 0.001" * 41 + "]"), "input.out_of_range", "a"),  # order 41
        (dict(b="[" + ",".join(["0.001"] * 4096) + "]"), "input.out_of_range", "b"),
        (dict(b="[" + ",".join(["0.001"] * 42) + "]", a="[1, -0.5]"), "input.out_of_range", "b"),
        (dict(b="[NaN]"), "input.invalid", "b"),
        (dict(b="[1e13]"), "input.out_of_range", "b"),
        (dict(b="[]"), "input.invalid", "b"),
        (dict(b="[1]", allow_unstable="yes"), "input.invalid", "allow_unstable"),
        (dict(family="butterworth"), "input.missing", "response"),
        (dict(family="butterworth", response="lowpass", order=4, cutoff="1 kHz"), "input.missing", "sample_rate"),
        (dict(family="butterworth", response="lowpass", order=41, cutoff="1 kHz", sample_rate="8 kHz"), "input.out_of_range", "order"),
        (dict(family="butterworth", response="lowpass", order=4, cutoff="4 kHz", sample_rate="8 kHz"), "input.invalid", "cutoff"),
        (dict(family="butterworth", response="lowpass", order=4, cutoff="1 kHz", sample_rate="-8 kHz"), "input.invalid", "sample_rate"),
        (dict(family="butterworth", response="lowpass", order=4, cutoff="1 kHz", sample_rate="8 m"), "units.dimension_mismatch", "sample_rate"),
    ],
)
def test_bad_requests_are_refused(kwargs, family, param):
    fails(filter_apply, family, param, **{"samples": "[1, 2, 3, 4, 5, 6, 7, 8]", **kwargs})


def test_samples_problems_are_refused_here_too():
    fails(filter_apply, "input.invalid", "samples", samples="[1, NaN]", b="[1]")
    fails(filter_apply, "input.invalid", "samples", samples="[1]", b="[1]")
    fails(filter_apply, "input.out_of_range", "samples", samples="[" + "0," * 5_000_000 + "0]", b="[1]")
