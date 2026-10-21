"""dsp_correlate: statistics, autocorrelation and period, cross-correlation and delay, Hilbert envelope; expected values by hand."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp.correlate import correlate
from tests.dsp_testing import fails, js, ok, sine, table, v


def gauss(n, centre, sigma):
    return np.exp(-(((np.arange(n) - centre) / sigma) ** 2) / 2.0)


# ---------------------------------------------------------------------------------------------------- statistics


def test_statistics_by_hand():
    # x = [1, -2, 3, -4]: mean = -2/4 = -0.5;  mean square = (1 + 4 + 9 + 16)/4 = 7.5, rms = 2.738613;  peak 4;  peak-to-peak 3 - (-4) = 7;
    # crest = 4/2.738613 = 1.460593;  deviations 1.5, -1.5, 3.5, -3.5: variance = (2.25 + 2.25 + 12.25 + 12.25)/4 = 7.25 (population), 29/3 = 9.6667 (sample)
    r = ok(correlate, samples="[1, -2, 3, -4]")
    assert v(r, "mean") == -0.5 and v(r, "peak") == 4.0 and v(r, "peak_to_peak") == 7.0
    assert v(r, "rms") == pytest.approx(math.sqrt(7.5), abs=1e-15) and v(r, "crest_factor") == pytest.approx(4 / math.sqrt(7.5), abs=1e-15)
    assert v(r, "variance") == pytest.approx(7.25, abs=1e-15) and v(r, "standard_deviation") == pytest.approx(math.sqrt(7.25), abs=1e-15)
    assert r.data["statistics"]["variance_sample"] == pytest.approx(29 / 3, abs=1e-14)


def test_crest_factor_of_a_sine_is_sqrt_2_and_of_zeros_is_undefined():
    r = ok(correlate, samples=js(sine(2.0, 10, 1000)))
    assert v(r, "crest_factor") == pytest.approx(math.sqrt(2), abs=1e-12) and v(r, "rms") == pytest.approx(math.sqrt(2), abs=1e-12)
    r = ok(correlate, samples="[0, 0, 0, 0]")
    assert "crest_factor" not in r.values and "undefined" in r.data["statistics"]["crest_factor"]


# ---------------------------------------------------------------------------------------------------- autocorrelation


def test_autocorrelation_by_hand():
    # x = [1, 0, -1, 0]: mean 0, c0 = 2;  c1 = x0x1 + x1x2 + x2x3 = 0;  c2 = x0x2 + x1x3 = -1;  c3 = x0x3 = 0   ->  r = [1, 0, -0.5, 0]
    r = ok(correlate, samples="[1, 0, -1, 0]", max_lag=3)
    t = table(r, "autocorrelation.csv")
    assert t["r"].tolist() == [1.0, 0.0, -0.5, 0.0] and t["lag_samples"].tolist() == [0, 1, 2, 3]


def test_autocorrelation_of_a_sinusoid_peaks_at_its_period():
    # period 50 samples, 4000 samples (80 cycles), 1 kHz: first zero crossing at a quarter period, then the first full-height peak at lag 50 exactly.
    # The integer lag is exact; the parabolic refinement is approximate (the 1/N end effects of a finite record move it: 0.18 sample at 8 cycles, about 0.02 at 80).
    for n, tol in ((400, 0.3), (4000, 0.05)):
        r = ok(correlate, samples=js(np.sin(2 * np.pi * np.arange(n) / 50.0)), sample_rate="1 kHz")
        p = r.data["dominant_period"]
        assert p["found"] and p["lag_samples"] == 50
        assert v(r, "period_samples") == pytest.approx(50.0, abs=tol)
        assert v(r, "period") == pytest.approx(0.05, abs=tol / 1000) and v(r, "period_frequency") == pytest.approx(20.0, rel=tol / 50)
        assert p["strength"] == pytest.approx(1.0, abs=1e-9)
    # the table peaks there too (biased autocorrelation: (1 - k/N) cos(2 pi k/50), a little under 1 at lag 50 -- maximum near it)
    t = table(ok(correlate, samples=js(np.sin(2 * np.pi * np.arange(4000) / 50.0)), max_lag=120), "autocorrelation.csv")
    assert int(np.argmax(t["r"][20:80])) + 20 in (49, 50)
    assert t["r"][50] == pytest.approx((1 - 50 / 4000), abs=2e-3)


def test_period_of_a_noisy_sinusoid_and_of_noise():
    rng = np.random.default_rng(2)
    n = 3000
    x = np.sin(2 * np.pi * np.arange(n) / 37.0) + rng.normal(scale=0.7, size=n)
    p = ok(correlate, samples=js(x)).data["dominant_period"]
    assert p["found"] and p["lag_samples"] == 37
    q = ok(correlate, samples=js(rng.normal(size=n))).data["dominant_period"]
    assert q["found"] is False and "reason" in q


def test_a_constant_record_has_no_autocorrelation_and_a_trend_has_no_period():
    r = ok(correlate, samples="[2, 2, 2, 2, 2, 2]")
    assert "not applicable" in r.data["autocorrelation"] and not any(a.path.endswith("autocorrelation.csv") for a in r.artifacts)
    r = ok(correlate, samples=js(np.arange(200.0)))
    assert r.data["dominant_period"]["found"] is False


def test_the_mean_is_removed_by_default():
    r = ok(correlate, samples=js(5.0 + sine(1.0, 10, 1000)), max_lag=150)
    assert ok(correlate, samples=js(sine(1.0, 10, 1000)), max_lag=150).data["dominant_period"]["lag_samples"] == r.data["dominant_period"]["lag_samples"] == 100
    t = table(ok(correlate, samples=js(5.0 + sine(1.0, 10, 1000)), max_lag=5, remove_mean=False), "autocorrelation.csv")
    assert t["r"][5] > 0.99  # with the offset kept, the autocorrelation is dominated by it


# ---------------------------------------------------------------------------------------------------- cross-correlation


def test_cross_correlation_by_hand_and_the_sign_convention():
    # x = [1, 2, 3], y = [0, 1, 2, 3] (x delayed by one sample), means kept: c[k] = sum_n x[n] y[n+k]:
    # k = -3..3: 0, 0, 3, 8, 14, 8, 3 ... (k=-1: x1y0 + x2y1 = 3; k=0: 0 + 2 + 6 = 8; k=1: 1 + 4 + 9 = 14; k=2: 2 + 6 = 8; k=3: 3), norm sqrt(14 x 14) = 14
    r = ok(correlate, samples="[1, 2, 3]", samples_b="[0, 1, 2, 3]", remove_mean=False)
    t = table(r, "cross_correlation.csv")
    assert t["lag_samples"].tolist() == [-3, -2, -1, 0, 1, 2, 3]
    assert t["rho"] == pytest.approx([0, 0, 3 / 14, 8 / 14, 1.0, 8 / 14, 3 / 14], abs=1e-15)
    assert v(r, "lag_samples") == 1.0 and v(r, "correlation_coefficient") == pytest.approx(1.0, abs=1e-15)
    assert r.data["cross_correlation"]["sign"].startswith("positive lag")


def test_an_integer_sample_delay_is_recovered_exactly():
    # the second record is the first delayed by exactly 7 samples: lag 7, refined 7 (the correlation of a symmetric pulse is symmetric: tolerance 1e-9),
    # correlation coefficient 1 (tolerance 1e-12), delay 7 ms at 1 kHz
    n = 400
    a, b = gauss(n, 150.0, 6.0), gauss(n, 157.0, 6.0)
    r = ok(correlate, samples=js(a), samples_b=js(b), sample_rate="1 kHz", remove_mean=False)
    assert v(r, "lag_samples") == 7.0 and v(r, "lag_refined_samples") == pytest.approx(7.0, abs=1e-9)
    assert v(r, "correlation_coefficient") == pytest.approx(1.0, abs=1e-12) and v(r, "delay") == pytest.approx(0.007, abs=1e-12)
    # earlier second record: negative lag
    r = ok(correlate, samples=js(b), samples_b=js(a), sample_rate="1 kHz", remove_mean=False)
    assert v(r, "lag_samples") == -7.0 and v(r, "delay") == pytest.approx(-0.007, abs=1e-12)


def test_the_integer_lag_of_delayed_noise_is_exact():
    rng = np.random.default_rng(9)
    x = rng.normal(size=1000)
    y = np.concatenate([np.zeros(13), x[:-13]])  # delayed by 13 samples
    r = ok(correlate, samples=js(x), samples_b=js(y))
    assert v(r, "lag_samples") == 13.0 and v(r, "correlation_coefficient") == pytest.approx(987 / 1000, abs=0.03)  # 987 of 1000 samples overlap


@pytest.mark.parametrize("d", [0.0, 0.1, 0.3, 0.5, 0.77, 0.9])
def test_a_fractional_delay_is_found_within_0_01_sample(d):
    # Gaussian pulses 3 samples wide: second = first delayed by 7 + d samples.  The parabola through the three values around the maximum is exact for
    # a Gaussian up to O(1/sigma^2): measured worst case 0.003 sample (sigma 3), 0.0007 (sigma 6).  Stated tolerance 0.01.
    n = 400
    r = ok(correlate, samples=js(gauss(n, 150.0, 3.0)), samples_b=js(gauss(n, 157.0 + d, 3.0)), remove_mean=False)
    assert v(r, "lag_refined_samples") == pytest.approx(7.0 + d, abs=0.01)
    # a windowed tone burst (0.05 cycles/sample, i.e. 20 samples per cycle): measured worst case 0.0016
    burst = lambda c: gauss(n, c, 40.0) * np.sin(2 * np.pi * 0.05 * (np.arange(n) - c))
    r = ok(correlate, samples=js(burst(150.0)), samples_b=js(burst(157.0 + d)), sample_rate="2 kHz")
    assert v(r, "lag_refined_samples") == pytest.approx(7.0 + d, abs=0.01)
    assert v(r, "delay") == pytest.approx((7.0 + d) / 2000.0, abs=0.01 / 2000.0)


def test_unequal_lengths_and_a_lag_limit():
    a, b = gauss(300, 100.0, 5.0), gauss(500, 130.0, 5.0)
    r = ok(correlate, samples=js(a), samples_b=js(b), remove_mean=False)
    assert v(r, "lag_samples") == 30.0
    r = ok(correlate, samples=js(a), samples_b=js(b), remove_mean=False, max_lag=10)
    assert any("end of the searched lag range" in w for w in r.warnings)


def test_files_and_the_time_column(tmp_path):
    t = np.arange(300) / 500.0
    a, b = gauss(300, 100.0, 5.0), gauss(300, 112.0, 5.0)
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    pa.write_text("\n".join(f"{float(x)!r},{float(y)!r}" for x, y in zip(t, a)), encoding="utf-8")
    pb.write_text("\n".join(f"{float(x)!r},{float(y)!r}" for x, y in zip(t, b)), encoding="utf-8")
    r = ok(correlate, samples_file=str(pa), samples_b_file=str(pb), remove_mean=False)
    assert v(r, "lag_samples") == 12.0 and v(r, "delay") == pytest.approx(12 / 500.0, abs=1e-12)
    pc = tmp_path / "c.csv"
    pc.write_text("\n".join(f"{float(x) * 2!r},{float(y)!r}" for x, y in zip(t, b)), encoding="utf-8")
    fails(correlate, "input.invalid", "samples_b_file", samples_file=str(pa), samples_b_file=str(pc))  # different time bases


# ---------------------------------------------------------------------------------------------------- Hilbert envelope


def test_the_hilbert_envelope_of_an_on_bin_am_signal_is_the_modulating_envelope():
    # x = (1 + 0.5 cos(2 pi 3 k/N)) cos(2 pi 40 k/N), N = 512: the lines are at bins 37, 40, 43 (and their mirror images), all inside 0 < bin < N/2,
    # so the analytic signal is (1 + 0.5 cos(2 pi 3 k/N)) exp(j 2 pi 40 k/N) and its modulus is the envelope.  Tolerance 1e-12 (observed 3e-16).
    n = 512
    k = np.arange(n)
    env = 1 + 0.5 * np.cos(2 * np.pi * 3 * k / n)
    x = env * np.cos(2 * np.pi * 40 * k / n)
    r = ok(correlate, samples=js(x), envelope=True)
    e = table(r, "envelope.csv")
    assert np.max(np.abs(e["envelope"] - env)) < 1e-12
    assert v(r, "envelope_max") == pytest.approx(1.5, abs=1e-12) and v(r, "envelope_mean") == pytest.approx(1.0, abs=1e-12)
    assert not any("not periodic" in w for w in r.warnings)
    # odd length works too: N = 513, same construction
    n = 513
    k = np.arange(n)
    env = 1 + 0.3 * np.cos(2 * np.pi * 2 * k / n)
    r = ok(correlate, samples=js(env * np.sin(2 * np.pi * 50 * k / n + 0.4)), envelope=True)
    assert np.max(np.abs(table(r, "envelope.csv")["envelope"] - env)) < 1e-12


def test_the_envelope_of_a_plain_sine_is_its_amplitude_and_a_non_periodic_record_is_flagged():
    r = ok(correlate, samples=js(sine(1.7, 30, 600)), envelope=True)
    assert np.max(np.abs(table(r, "envelope.csv")["envelope"] - 1.7)) < 1e-12
    r = ok(correlate, samples=js(sine(1.0, 30.37, 600)), envelope=True)
    assert any("not periodic" in w for w in r.warnings)  # the ends of a 30.37-cycle sine (0 and 0.906) differ by 45 % of its range
    assert np.max(np.abs(table(r, "envelope.csv")["envelope"] - 1.0)) > 0.01  # and the envelope is visibly wrong near them
    r = ok(correlate, samples=js(np.arange(100.0)), envelope=True)
    assert any("not periodic" in w for w in r.warnings)


# ---------------------------------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "kwargs, family, param",
    [
        (dict(samples="[1, 2, NaN]"), "input.invalid", "samples"),
        (dict(samples="[1, 2]"), "input.invalid", "samples"),  # fewer than 3
        (dict(samples=""), "input.missing", "samples"),
        (dict(samples="[" + "0," * 5_000_000 + "0]"), "input.out_of_range", "samples"),
        (dict(samples="[1, 2, 3, 4]", samples_b="[1, 2, Infinity]"), "input.invalid", "samples_b"),
        (dict(samples="[1, 2, 3, 4]", samples_b="[1, 1, 1, 1]"), "input.invalid", "samples_b"),  # constant: no variance after removing the mean
        (dict(samples="[1, 2, 3, 4]", max_lag=4), "input.invalid", "max_lag"),
        (dict(samples="[1, 2, 3, 4]", max_lag=-1), "input.out_of_range", "max_lag"),
        (dict(samples="[1, 2, 3, 4]", max_lag=100001), "input.out_of_range", "max_lag"),
        (dict(samples="[1, 2, 3, 4]", sample_rate="1 m"), "units.dimension_mismatch", "sample_rate"),
        (dict(samples="[1, 2, 3, 4]", sample_rate="-5 Hz"), "input.invalid", "sample_rate"),
        (dict(samples="[1, 2, 3, 4]", envelope="yes"), "input.invalid", "envelope"),
        (dict(samples="[1, 2, 3, 4]", samples_file="x.csv"), "input.invalid", "samples"),
    ],
)
def test_bad_requests_are_refused(kwargs, family, param):
    fails(correlate, family, param, **kwargs)
