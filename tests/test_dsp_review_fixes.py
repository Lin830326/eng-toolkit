"""Regression tests for the 17 issues an independent review found in the first version of the dsp domain (one test per item)."""

import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _iir as iir
from eng_toolkit.domains.dsp.correlate import correlate
from eng_toolkit.domains.dsp.filter_apply import filter_apply
from eng_toolkit.domains.dsp.filter_design import filter_design
from eng_toolkit.domains.dsp.sampling import sampling
from eng_toolkit.domains.dsp.spectrum import spectrum
from eng_toolkit.domains.dsp.window import window
from tests.dsp_testing import fails, js, ok, sine, table, v


def test_01_fir_specification_is_sized_for_the_passband_ripple_too():
    # Rp = 0.0001 dB -> delta_p = 1 - 10^(-Rp/20) = 1.1512e-5, delta_s = 10^(-40/20) = 0.01: delta = min = 1.1512e-5, A = -20 log10(delta) = 98.777 dB
    # beta = 0.1102 (98.777 - 8.7) = 9.9264;  d_omega = 0.2 pi (fs = 2 Hz, 0.4 -> 0.6 Hz);  M = (98.777 - 7.95)/(2.285 x 0.628319) = 63.26 -> 64, 65 taps
    # (before the fix: 24 taps, 0.118 dB of ripple).  Tolerance on beta 1e-3.
    delta_p = 1 - 10 ** (-0.0001 / 20)
    a_db = -20 * math.log10(delta_p)
    assert a_db == pytest.approx(98.777, abs=1e-3)
    r = ok(filter_design, family="fir_kaiser", response="lowpass", sample_rate="2 Hz", passband_edge=".4 Hz", stopband_edge=".6 Hz", passband_ripple_db=0.0001, stopband_attenuation_db=40.0, points=16)
    assert r.data["num_taps"] == 65 and r.data["beta"] == pytest.approx(0.1102 * (a_db - 8.7), abs=1e-9)
    assert r.data["measurement"]["passband_max_deviation_db"] < 0.00012   # 0.0001 asked; Kaiser's formula is a fit (measured 0.000109), not 0.017 as before
    assert r.data["measurement"]["passband_ripple_peak_to_peak_db"] < 0.00025
    assert any("tighter than the stopband" in w for w in r.warnings)
    # the formula is a fit: when the measurement still misses, the tool says so instead of claiming success
    assert r.data["meets_specification"] is False and any("exceeds the 0.0001 dB asked for" in w for w in r.warnings)
    # a looser ripple leaves the stopband in charge: textbook 60 dB example unchanged
    r = ok(filter_design, family="fir_kaiser", response="lowpass", sample_rate="2 Hz", passband_edge=".4 Hz", stopband_edge=".6 Hz", passband_ripple_db=1.0, stopband_attenuation_db=60.0, points=16)
    assert r.data["num_taps"] == 38 and r.data["meets_specification"] is True


def test_02_group_delay_does_not_confuse_a_small_gain_with_a_zero():
    # second-order Butterworth, cutoff 0.1 Hz at 1 MHz: tau(wc) = sqrt(2)/sin(wc), wc = 2 pi 0.1/1e6 = 6.2832e-7 rad  ->  2 250 790.79 samples
    # (before the fix 1 880 319 and a false "on a zero" note).  Tolerance 1e-6 relative.
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="1 MHz", order=2, cutoff="0.1 Hz", points=16)
    wc = 2 * math.pi * 0.1 / 1e6
    row = next(g for g in r.data["group_delay"] if g["frequency_hz"] == 0.1)
    assert row["group_delay_samples"] == pytest.approx(math.sqrt(2) / math.sin(wc), rel=1e-6) and "note" not in row
    # multiplying a numerator by a constant cannot change a group delay
    sos = np.array(ok(filter_design, family="butterworth", response="lowpass", sample_rate="8 kHz", order=4, cutoff="1 kHz", points=16).data["sos"])
    scaled = sos.copy()
    scaled[:, :3] *= 1e-12
    assert iir.group_delay_sos(scaled, 0.7)[0] == pytest.approx(iir.group_delay_sos(sos, 0.7)[0], rel=1e-12)
    assert iir.group_delay_sos(scaled, 0.7)[1] is False


@pytest.mark.parametrize("kwargs", [dict(order=2, cutoff="0.499999999999 Hz"), dict(order=20, cutoff="1e-8 Hz"), dict(order=2, cutoff="1e-9 Hz")])
def test_03_unrepresentable_designs_are_refused_not_returned(kwargs):
    # a 2nd-order section near Nyquist gives -300 dB at the cutoff (the definition is -3.0103 dB); order 20 at 1e-8 fs gives -3.155 dB; a pole rounds onto z = 1
    r = fails(filter_design, "input.out_of_range", "cutoff", family="butterworth", response="lowpass", sample_rate="1 Hz", points=16, **kwargs)
    assert "cannot be represented" in r.error.message


def test_04_dc_gain_is_h_of_one_not_a_value_at_a_nonzero_frequency():
    # first-order Butterworth, cutoff 1e-9 fs: the DC gain is exactly 1 (0 dB); evaluating at 1e-9 rad gave 0.9753 (-0.1086 dB)
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="1 Hz", order=1, cutoff="1e-9 Hz", points=16)
    assert r.data["dc_gain"] == pytest.approx(1.0, abs=1e-12) and r.data["dc_gain_db"] == pytest.approx(0.0, abs=1e-10)
    a = ok(filter_apply, samples="[1, 0, 0]", sos=json.dumps(r.data["sos"]))
    assert a.data["dc_gain"] == pytest.approx(1.0, abs=1e-6)  # sum(b)/sum(a), cancellation limits it to ~2e-8 here
    # a high pass has a zero at DC; a pole at z = 1 has an infinite DC gain (null)
    h = ok(filter_design, family="butterworth", response="highpass", sample_rate="8 kHz", order=3, cutoff="1 kHz", points=16)
    assert h.data["dc_gain"] == pytest.approx(0.0, abs=1e-12) and h.data["dc_gain_db"] <= -200
    assert ok(filter_apply, samples="[1, 0, 0]", b="[1]", a="[1, -1]", allow_unstable=True).data["dc_gain"] is None


def test_05_analogue_gain_overflow_is_a_refusal_naming_the_cutoff():
    r = fails(filter_design, "input.out_of_range", "cutoff", family="butterworth", response="lowpass", sample_rate="1 Hz", order=40, cutoff="0.499999999999 Hz", points=16)
    assert r.error.family != "engine.failure"


def test_06_the_nyquist_bin_can_be_the_strongest_peak():
    n = 1024
    x = (-1.0) ** np.arange(n) + sine(0.1, 100, n)
    r = ok(spectrum, samples=js(x), sample_rate="1024 Hz", window="rectangular", peaks=3)
    first = r.data["peaks"][0]
    assert first["frequency_hz"] == 512.0 and first["amplitude"] == pytest.approx(1.0, abs=1e-12) and first["bin"] == 512
    assert [round(p["frequency_hz"]) for p in r.data["peaks"]] == [512, 100]
    assert v(r, "peak_frequency") == 512.0 and v(r, "peak_amplitude") == pytest.approx(1.0, abs=1e-12)
    # odd segment length: the last bin is not Nyquist but is still a candidate
    y = sine(1.0, 500.0, 1001)
    assert ok(spectrum, samples=js(y), sample_rate="1001 Hz", window="rectangular", peaks=2).data["peaks"][0]["bin"] == 500


def test_07_a_tone_inside_the_dc_lobe_is_refused_not_measured_as_roundoff():
    # 1 cycle in 1024 samples under a Hann window: the tone's lobe (bins 0..3) overlaps the DC lobe; before the fix: "fundamental" 463.86 Hz at 1e-16
    r = fails(spectrum, "input.invalid", "adc_metrics", samples=js(sine(1.0, 1, 1024)), sample_rate="1024 Hz", window="hann", adc_metrics=True)
    assert "DC lobe" in r.error.message
    # 5 cycles (bin 5 = 2 x 2 + 1, the nearest allowed for a 2-bin half width) is accepted, 4 is not
    ok(spectrum, samples=js(sine(1.0, 5, 1024)), sample_rate="1024 Hz", window="hann", adc_metrics=True)
    fails(spectrum, "input.invalid", "adc_metrics", samples=js(sine(1.0, 4, 1024)), sample_rate="1024 Hz", window="hann", adc_metrics=True)


def test_08_an_all_zero_record_has_no_fundamental():
    fails(spectrum, "input.invalid", "adc_metrics", samples=js(np.zeros(1024)), sample_rate="1024 Hz", adc_metrics=True)
    fails(spectrum, "input.invalid", "adc_metrics", samples=js(np.full(1024, 3.0)), sample_rate="1024 Hz", adc_metrics=True, window="rectangular")  # DC only
    ok(spectrum, samples=js(np.zeros(1024)), sample_rate="1024 Hz")  # without converter metrics a zero spectrum is fine


def test_09_welch_work_is_bounded():
    # 2^22 samples, segments of 2^21 with 99.99999 % overlap would be 2 097 153 FFTs of 2^21 points
    r = fails(spectrum, "input.out_of_range", "overlap_percent", samples="[" + "0," * ((1 << 22) - 1) + "0]", sample_rate="1 Hz", segment_length=2097152, overlap_percent=99.99999)
    assert r.error.details["segments"] > 2_000_000
    ok(spectrum, samples=js(np.zeros(4096)), sample_rate="1 Hz", segment_length=64, overlap_percent=90.0)  # many small segments are fine


def test_10_small_signals_are_not_squared_to_zero():
    # amplitude 1e-90 on bin 13: the spectrum reads 1e-90 (squares are 1e-180); a record below 1e-100 (squares underflow) is refused instead of silently zeroed
    x = sine(1e-90, 13, 1024)
    r = ok(spectrum, samples=js(x), sample_rate="1024 Hz", window="rectangular")
    assert v(r, "peak_amplitude") == pytest.approx(1e-90, rel=1e-9) and v(r, "peak_frequency") == pytest.approx(13.0, abs=1e-9)
    assert v(r, "rms") == pytest.approx(1e-90 / math.sqrt(2), rel=1e-3)
    fails(spectrum, "input.out_of_range", "samples", samples=js(sine(1e-200, 13, 1024)), sample_rate="1024 Hz", window="rectangular")
    fails(correlate, "input.out_of_range", "samples", samples=js(sine(1e-200, 13, 256)))


def test_11_normalisation_cannot_create_infinity():
    fails(filter_apply, "input.out_of_range", "sos", samples="[1, 0, 0]", sos="[[1, 0, 0, 1e-320, -1, 0]]")
    fails(filter_apply, "input.out_of_range", "a", samples="[1, 0, 0]", b="[1]", a="[1e-320, -1]")
    fails(filter_apply, "input.out_of_range", "a", samples="[1, 0, 0]", b="[1e12]", a="[1e-300, 1]")


def test_12_enob_uses_the_level_without_needing_bits():
    # (68 - 1.76 - (-6))/6.02 = 72.24/6.02 = 12 exactly (before: 11.0033)
    assert v(ok(sampling, operation="quantization", sinad_db=68, input_level_dbfs=-6), "enob_bits") == pytest.approx(12.0, abs=1e-12)
    assert v(ok(sampling, operation="quantization", sinad_db=68, input_level_dbfs=-6, bits=12), "enob_bits") == pytest.approx(12.0, abs=1e-12)
    assert v(ok(sampling, operation="quantization", sinad_db=74), "enob_bits") == pytest.approx(12.0, abs=1e-12)


def test_13_alias_of_a_huge_ratio_is_exact_or_refused():
    # exact remainder arithmetic: 1 000 000 037 Hz at 1000 Hz is 37 Hz (the products f - fs round(f/fs) are not needed)
    r = ok(sampling, operation="alias", frequency="1000000037 Hz", sample_rate="1000 Hz")
    assert v(r, "alias_frequency") == pytest.approx(37.0, abs=1e-9) and v(r, "folding_zone") == 2000000
    # alias never exceeds Nyquist, whatever the ratio
    rng = np.random.default_rng(3)
    for f, fs in zip(rng.uniform(1, 1e9, 40), rng.uniform(0.5, 5e3, 40)):
        got = v(ok(sampling, operation="alias", frequency=f"{float(f)!r} Hz", sample_rate=f"{float(fs)!r} Hz"), "alias_frequency")
        assert 0.0 <= got <= fs / 2 * (1 + 1e-15)
    # the review's case: 999999999999999.875 Hz at 0.003 Hz is 3.3e17 sample rates up, where one ulp of f exceeds the sample rate: refused
    fails(sampling, "input.out_of_range", "frequency", operation="alias", frequency="999999999999999.875 Hz", sample_rate=".003 Hz")


def test_14_cross_correlation_normalises_each_record_separately():
    # [1e80, 0, -1e80, 0] against itself: coefficient 1 at lag 0 (before: 0 at lag -3 because the product of the energies overflowed);
    # the same at 1e-100, where the product of the energies underflowed
    for scale in (1e80, 1e-100, 1.0):
        x = js(np.array([1.0, 0.0, -1.0, 0.0]) * scale)
        r = ok(correlate, samples=x, samples_b=x, remove_mean=False)
        assert v(r, "lag_samples") == 0.0 and v(r, "correlation_coefficient") == pytest.approx(1.0, abs=1e-12)
    a, b = js(np.array([1.0, 2.0, 3.0]) * 1e90), js(np.array([0.0, 1.0, 2.0, 3.0]) * 1e-90)
    assert v(ok(correlate, samples=a, samples_b=b, remove_mean=False), "correlation_coefficient") == pytest.approx(1.0, abs=1e-12)  # unequal scales, delayed copy
    r = ok(correlate, samples=js(sine(1e80, 10, 400)), max_lag=60)  # the autocorrelation is scale free too
    assert r.data["dominant_period"]["lag_samples"] == 40


def test_15_the_documented_one_per_second_unit_parses():
    assert v(ok(sampling, operation="alias", frequency="10 1/s", sample_rate="100 Hz"), "alias_frequency") == pytest.approx(10.0, abs=1e-12)
    assert v(ok(sampling, operation="alias", frequency="130 Hz", sample_rate="100 1/s"), "alias_frequency") == pytest.approx(30.0, abs=1e-12)
    r = ok(filter_design, family="butterworth", response="lowpass", sample_rate="8000 1/s", order=2, cutoff="1000 1/s", points=16)
    assert r.data["sample_rate_hz"] == 8000.0 and r.data["edges_hz"][0] == pytest.approx(1000.0, abs=1e-9)
    ok(spectrum, samples=js(sine(1.0, 5, 64)), sample_rate="64 1/s")
    ok(correlate, samples=js(sine(1.0, 5, 64)), sample_rate="64 1/s")
    ok(filter_apply, samples=js(sine(1.0, 5, 64)), sample_rate="64 1/s", b="[0.5, 0.5]")
    fails(sampling, "units.parse_error", "frequency", operation="alias", frequency="10 11/s", sample_rate="100 Hz")


@pytest.mark.parametrize(
    "tool, kwargs, param",
    [
        (filter_design, dict(family="butterworth", response="lowpass", sample_rate="8 kHz", order=41, cutoff="1 kHz"), "/order"),
        (spectrum, dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="8 Hz", overlap_percent=100.0), "/overlap_percent"),
        (filter_apply, dict(samples="[1,2,3]", b="[1]", a="[0]"), "/a"),
        (sampling, dict(operation="quantization", bits=33), "/bits"),
        (correlate, dict(samples="[1,2,3,4]", max_lag=100001), "/max_lag"),
        (window, dict(window="hann", length=7), "/length"),
        (filter_design, dict(family="butterworth", response="lowpass", sample_rate="1 Hz", order=2, cutoff="1e-9 Hz"), "/cutoff"),
        (spectrum, dict(samples="[1,2,3,4,5,6,7,8]", sample_rate="8 m"), "/sample_rate"),
        (spectrum, dict(samples="[1,2,3,4,5,6,7,8]", sample_rate="8 Hz", window="nope"), "/window"),
        (spectrum, dict(samples="[1,2,NaN,4,5,6,7,8]", sample_rate="8 Hz"), "/samples"),
        (spectrum, dict(samples="", samples_file="", sample_rate="8 Hz"), "/samples"),
        (filter_apply, dict(samples="[1,2,3]", sos="[[1,0,0]]"), "/sos"),
        (sampling, dict(operation="resample"), "/operation"),
    ],
)
def test_16_every_refusal_names_its_parameter_as_a_json_pointer(tool, kwargs, param):
    r = fails(tool, r_family(kwargs, tool), param, **kwargs)
    assert r.error.details["param"] == param


def r_family(kwargs, tool):
    """The family the call is expected to fail with (looked up by running it once; this test is about the pointer, not the family)."""
    from tests.dsp_testing import call

    result = call(tool, **kwargs)
    assert result.ok is False
    return result.error.family


def test_16_refusals_found_by_a_scan_of_bad_calls_all_carry_a_pointer():
    cases = [
        (filter_design, dict(family="butterworth", response="lowpass", sample_rate="8 kHz", order=4, cutoff=c)) for c in ("0 Hz", "4 kHz", "-1 kHz", "1", "1 m", "x", "1 kHz, 2 kHz")
    ] + [
        (spectrum, dict(samples=s, sample_rate="8 Hz")) for s in ("[1,2]", "[", "{}", "[true,1,2,3,4,5,6,7]", "[[1],[2]]", "[1e400,1,2,3,4,5,6,7]")
    ] + [
        (correlate, dict(samples="[1,2,3,4]", samples_b=s)) for s in ("[1,1,1,1]", "[1,NaN]", "[]")
    ] + [
        (sampling, dict(operation="decimate", samples="[1,2,3,4]", sample_rate="1 Hz", factor=f)) for f in (0, 1, 5000)
    ]
    for tool, kwargs in cases:
        result = getattr(tool, "fn", tool)(**kwargs)
        assert result.ok is False, (tool.__name__, kwargs)
        assert result.error.family != "engine.failure", result.error
        assert str(result.error.details.get("param", "")).startswith("/"), (tool.__name__, kwargs, result.error)


@pytest.mark.parametrize(
    "tool, kwargs, param",
    [
        (window, dict(window="kaiser", kaiser_beta=1e300), "/kaiser_beta"),
        (spectrum, dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="8 Hz", adc_metrics=True, full_scale_amplitude=1e300), "/full_scale_amplitude"),
        (sampling, dict(operation="quantization", bits=8, input_level_dbfs=-1e300), "/input_level_dbfs"),
        (filter_design, dict(family="chebyshev1", response="lowpass", sample_rate="8 kHz", order=3, cutoff="1 kHz", passband_ripple_db=1e300), "/passband_ripple_db"),
        (filter_apply, dict(samples="[1,2,3,4]", sample_rate="8 kHz", family="chebyshev1", response="lowpass", order=3, cutoff="1 kHz", passband_ripple_db=1e300), "/passband_ripple_db"),
        (filter_design, dict(family="butterworth", response="lowpass", sample_rate="8 kHz", order=3, cutoff="1 kHz", stopband_attenuation_db=1e300), "/stopband_attenuation_db"),
        (sampling, dict(operation="decimate", samples="[1,2,3,4]", sample_rate="1 Hz", factor=2, attenuation_db=1e300), "/attenuation_db"),
        (correlate, dict(samples="[1,2,3,4]", max_lag=10 ** 400), "/max_lag"),
        (spectrum, dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="1e400 Hz"), "/sample_rate"),
    ],
)
def test_17_absurd_magnitudes_are_out_of_range_not_invalid(tool, kwargs, param):
    fails(tool, "input.out_of_range", param, **kwargs)


def test_17_ordinary_wrong_values_and_non_finite_numbers_stay_invalid():
    fails(window, "input.invalid", "kaiser_beta", window="kaiser", kaiser_beta=51.0)
    fails(window, "input.invalid", "kaiser_beta", window="kaiser", kaiser_beta=float("nan"))
    fails(sampling, "input.invalid", "input_level_dbfs", operation="quantization", bits=8, input_level_dbfs=3.0)
