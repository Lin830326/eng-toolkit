"""dsp_sampling: aliasing, minimum sample rate, quantisation, decimation and interpolation; expected values by hand."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp.sampling import sampling
from tests.dsp_testing import fails, js, ok, table, v

# ---------------------------------------------------------------------------------------------------- alias


@pytest.mark.parametrize(
    "f, fs, alias, zone, inverted",
    [
        # fs = 1 kHz, Nyquist 500 Hz.  alias = |f - fs round(f/fs)|; zone = floor(f/500); odd zones are mirrored.
        (1300, 1000, 300, 2, False),  # round(1.3) = 1 -> |1300 - 1000| = 300
        (700, 1000, 300, 1, True),    # round(0.7) = 1 -> |700 - 1000| = 300
        (2200, 1000, 200, 4, False),  # round(2.2) = 2 -> 200
        (2800, 1000, 200, 5, True),   # round(2.8) = 3 -> |2800 - 3000| = 200
        (3600, 1000, 400, 7, True),   # round(3.6) = 4 -> |3600 - 4000| = 400
        (250, 1000, 250, 0, False),   # below Nyquist: unchanged
        (500, 1000, 500, 1, True),    # exactly Nyquist
        (1000, 1000, 0, 2, False),    # a tone at fs is DC
        (2000, 1000, 0, 4, False),
        (0, 1000, 0, 0, False),
        (48000 + 1000, 48000, 1000, 2, False),  # 49 kHz at 48 kS/s -> 1 kHz (zone floor(49/24) = 2)
    ],
)
def test_alias_frequency_and_folding_zone_by_hand(f, fs, alias, zone, inverted):
    r = ok(sampling, operation="alias", frequency=f"{f} Hz", sample_rate=f"{fs} Hz")
    assert v(r, "alias_frequency") == pytest.approx(alias, abs=1e-9)
    assert v(r, "folding_zone") == zone and r.data["spectrum_inverted"] is inverted
    assert v(r, "nyquist_frequency") == fs / 2


def test_the_textbook_case_1_3_khz_at_1_khz_is_300_hz_and_the_units_are_free():
    r = ok(sampling, operation="alias", frequency="1.3 kHz", sample_rate="1 kHz")
    assert v(r, "alias_frequency") == pytest.approx(300.0, abs=1e-9)
    assert r.data["aliased"] is True and any("appears at 300 Hz" in w for w in r.warnings)
    r = ok(sampling, operation="alias", frequency=f"{2 * math.pi * 1300} rad/s", sample_rate=f"{2 * math.pi * 1000} rad/s")
    assert v(r, "alias_frequency") == pytest.approx(300.0, abs=1e-9)
    # the alias is the same for every tone k fs +- alias (identical samples)
    for f in (300, 700, 1300, 1700, 2300):
        assert v(ok(sampling, operation="alias", frequency=f"{f} Hz", sample_rate="1 kHz"), "alias_frequency") == pytest.approx(300.0, abs=1e-9)
    ok_r = ok(sampling, operation="alias", frequency="300 Hz", sample_rate="1 kHz")
    assert ok_r.data["aliased"] is False and 1300.0 in ok_r.data["same_samples_as_tones_hz"]


def test_identical_samples_for_a_tone_and_its_alias():
    # the claim behind the alias: sin(2 pi 1300 k/1000) = sin(2 pi 300 k/1000) for integer k
    k = np.arange(50)
    assert np.allclose(np.sin(2 * np.pi * 1300 * k / 1000), np.sin(2 * np.pi * 300 * k / 1000), atol=1e-12)
    assert np.allclose(np.sin(2 * np.pi * 700 * k / 1000), -np.sin(2 * np.pi * 300 * k / 1000), atol=1e-12)  # inverted zone: sign flip


# ---------------------------------------------------------------------------------------------------- minimum sample rate


def test_minimum_sample_rate_with_a_guard_band():
    # fs >= 2 (f_max + guard) = 2 (20 + 2) kHz = 44 kHz
    r = ok(sampling, operation="min_sample_rate", max_frequency="20 kHz", guard_band="2 kHz")
    assert v(r, "min_sample_rate") == pytest.approx(44000.0, abs=1e-9)
    assert v(ok(sampling, operation="min_sample_rate", max_frequency="20 kHz"), "min_sample_rate") == 40000.0


def test_bandpass_sampling_ranges_by_hand():
    # band 17 .. 22 kHz: n_max = floor(22/(22 - 17)) = 4;  range n: 2 f_h/n <= fs <= 2 f_l/(n-1):
    # n = 1: fs >= 44;  n = 2: 22 .. 34;  n = 3: 14.667 .. 17;  n = 4: 11 .. 11.333 kHz  -> lowest rate 11 kHz
    r = ok(sampling, operation="min_sample_rate", max_frequency="22 kHz", lowest_frequency="17 kHz")
    ranges = {x["n"]: x for x in r.data["bandpass_sampling"]["ranges"]}
    assert set(ranges) == {1, 2, 3, 4} and r.data["bandpass_sampling"]["n_max"] == 4
    assert (ranges[2]["fs_min_hz"], ranges[2]["fs_max_hz"]) == pytest.approx((22000.0, 34000.0), abs=1e-6)
    assert (ranges[3]["fs_min_hz"], ranges[3]["fs_max_hz"]) == pytest.approx((14666.666666666666, 17000.0), abs=1e-6)
    assert (ranges[4]["fs_min_hz"], ranges[4]["fs_max_hz"]) == pytest.approx((11000.0, 11333.333333333334), abs=1e-6)
    assert v(r, "min_bandpass_sample_rate") == pytest.approx(11000.0, abs=1e-6) and v(r, "min_sample_rate") == 44000.0
    assert any("band-pass filter" in w for w in r.warnings)


def test_min_sample_rate_refusals():
    fails(sampling, "input.missing", "max_frequency", operation="min_sample_rate")
    fails(sampling, "input.invalid", "max_frequency", operation="min_sample_rate", max_frequency="-1 kHz")
    fails(sampling, "input.invalid", "lowest_frequency", operation="min_sample_rate", max_frequency="1 kHz", lowest_frequency="2 kHz")
    fails(sampling, "input.out_of_range", "max_frequency", operation="min_sample_rate", max_frequency="1e15 Hz")
    fails(sampling, "units.dimension_mismatch", "max_frequency", operation="min_sample_rate", max_frequency="1 m")


# ---------------------------------------------------------------------------------------------------- quantisation


@pytest.mark.parametrize("bits, snr", [(8, 49.92), (10, 61.96), (12, 74.00), (14, 86.04), (16, 98.08), (24, 146.24)])
def test_quantisation_snr_is_6_02_n_plus_1_76(bits, snr):
    # 6.02 x 16 + 1.76 = 96.32 + 1.76 = 98.08 dB  (tolerance 1e-9: the rounded textbook constants);  exact 6.0206 N + 1.7609 differs by < 0.011 dB at 16 bits
    r = ok(sampling, operation="quantization", bits=bits)
    assert v(r, "quantization_snr_db") == pytest.approx(snr, abs=1e-9)
    assert v(r, "quantization_snr_exact_db") == pytest.approx(20 * math.log10(2) * bits + 10 * math.log10(1.5), abs=1e-12)
    assert abs(v(r, "quantization_snr_db") - v(r, "quantization_snr_exact_db")) < 0.0006 * bits + 0.001


def test_quantisation_below_full_scale_and_the_enob_inverse():
    assert v(ok(sampling, operation="quantization", bits=12, input_level_dbfs=-6.0), "quantization_snr_db") == pytest.approx(74.0 - 6.0, abs=1e-9)
    # ENOB = (SINAD - 1.76)/6.02: 98.08 -> 16, 74.0 -> 12, 70 -> 11.3355 (= 68.24/6.02)
    assert v(ok(sampling, operation="quantization", sinad_db=98.08), "enob_bits") == pytest.approx(16.0, abs=1e-12)
    assert v(ok(sampling, operation="quantization", sinad_db=74.0), "enob_bits") == pytest.approx(12.0, abs=1e-12)
    assert v(ok(sampling, operation="quantization", sinad_db=70.0), "enob_bits") == pytest.approx(68.24 / 6.02, abs=1e-12)
    r = ok(sampling, operation="quantization", bits=12, sinad_db=68.0, input_level_dbfs=-6.0)  # a -6 dBFS tone reading 68 dB is a full 12 bits
    assert v(r, "enob_bits") == pytest.approx(12.0, abs=1e-12)
    assert v(r, "quantization_noise_rms_lsb") == pytest.approx(1 / math.sqrt(12), abs=1e-15)


def test_a_simulated_quantiser_agrees_with_the_formula_within_0_3_db():
    n = 1 << 18
    x = np.sin(2 * np.pi * 0.1234567 * np.arange(n))
    for bits in (6, 9, 12):
        step = 2.0 / 2 ** bits
        e = np.round(x / step) * step - x
        measured = 10 * math.log10(0.5 / np.mean(e * e))
        assert measured == pytest.approx(v(ok(sampling, operation="quantization", bits=bits), "quantization_snr_db"), abs=0.3)


def test_quantisation_refusals():
    fails(sampling, "input.missing", "bits", operation="quantization")
    fails(sampling, "input.out_of_range", "bits", operation="quantization", bits=33)
    fails(sampling, "input.out_of_range", "bits", operation="quantization", bits=-1)
    fails(sampling, "input.invalid", "input_level_dbfs", operation="quantization", bits=8, input_level_dbfs=3.0)
    fails(sampling, "input.invalid", "input_level_dbfs", operation="quantization", bits=8, input_level_dbfs=float("nan"))
    fails(sampling, "input.invalid", "operation", operation="resample")


# ---------------------------------------------------------------------------------------------------- decimation


def test_decimation_removes_the_tone_that_would_alias_onto_the_wanted_one():
    # fs = 1 kHz, factor 4 -> 250 Hz, new Nyquist 125 Hz, kept band 0..100 Hz (passband_fraction 0.8), alias bands 150..250 Hz and up.
    # The 300 Hz tone would alias to |300 - 250| = 50 Hz, on top of the wanted 50 Hz tone.
    # Kaiser design, A = 60 dB: transition (1 - 0.8) x 250 = 50 Hz, d_omega = 2 pi 50/1000 = 0.314159;  M = (60 - 7.95)/(2.285 x 0.314159) = 72.51 -> 73
    # taps = 74, made odd = 75 (integer delay 37).   Tolerances: 3e-3 on the interior samples (passband ripple +-0.0009 and alias residual 1e-3).
    n = 4000
    k = np.arange(n)
    wanted = np.sin(2 * np.pi * 50 * k / 1000)
    x = wanted + np.sin(2 * np.pi * 300 * k / 1000 + 0.7)
    r = ok(sampling, operation="decimate", samples=js(x), sample_rate="1 kHz", factor=4)
    assert r.data["filter"]["taps"] == 75 and v(r, "filter_taps") == 75 and r.data["filter"]["delay_samples_input_rate"] == 37
    assert v(r, "output_samples") == 1000 and v(r, "output_rate") == 250.0
    t = table(r, "decimated.csv")
    expected = wanted[::4]
    assert np.max(np.abs(t["value"][40:-40] - expected[40:-40])) < 3e-3
    assert t["time_s"][1] == pytest.approx(0.004) and list(t) == ["index", "time_s", "value"]
    assert v(r, "alias_band_attenuation_db") >= 59.0 and v(r, "alias_band_attenuation_db") < 70.0   # design 60 dB: measured 60.4
    assert v(r, "passband_ripple_db") < 0.02
    # alias bands: k fs_out +- 100 Hz for k = 1, 2, ... clipped at fs/2 = 500 Hz:  [150, 350] and [400, 500]
    assert r.data["alias_bands_hz"] == [[150.0, 350.0], [400.0, 500.0]]
    # without the filter the sum would be 2 sin(2 pi 50 .) -- the aliased tone is a full-size copy
    naive = x[::4]
    assert np.max(np.abs(naive[40:-40] - expected[40:-40])) > 0.9


def test_decimation_of_a_dc_signal_and_filter_gain():
    r = ok(sampling, operation="decimate", samples=js(np.full(1000, 3.0)), sample_rate="1 kHz", factor=5, attenuation_db=50.0)
    y = table(r, "decimated.csv")["value"]
    assert np.max(np.abs(y[20:-20] - 3.0)) < 1e-12 + 3.0 * 0.0010  # DC gain 1 exactly (to rounding), interior samples
    assert r.data["filter"]["dc_gain"] == pytest.approx(1.0, abs=1e-12)


def test_interpolation_reproduces_a_slow_sine_between_the_samples():
    # factor 3: kept band 0..0.4 fs (passband_fraction 0.8 of the old Nyquist 0.5 fs), images start at (1 - 0.4) fs = 0.6 fs; transition 0.2 fs;
    # d_omega (new rate) = 2 pi 0.2/3 = 0.418879;  M = 52.05/(2.285 x 0.418879) = 54.38 -> 55, taps 56 -> 57 (odd).   Gain L = 3 (DC gain 3 of the FIR).
    # A 0.05 fs sine is inside the kept band: the interpolated record is sin(2 pi 0.05 j/3) at the new instants, interior error < 3e-3 (ripple 0.0009 + images 1e-3).
    k = np.arange(300)
    x = np.sin(2 * np.pi * 0.05 * k)
    r = ok(sampling, operation="interpolate", samples=js(x), sample_rate="1 kHz", factor=3)
    assert r.data["filter"]["taps"] == 57 and v(r, "output_samples") == 900 and v(r, "output_rate") == 3000.0
    y = table(r, "interpolated.csv")["value"]
    j = np.arange(900)
    assert np.max(np.abs(y[60:-60] - np.sin(2 * np.pi * 0.05 * j[60:-60] / 3))) < 3e-3
    assert r.data["filter"]["dc_gain"] == pytest.approx(3.0, abs=1e-12)
    assert v(r, "image_band_attenuation_db") >= 59.0
    assert np.max(np.abs(y[::3][30:-30] - x[30:-30])) < 3e-3  # the original samples are kept (the filter is a half-band-like interpolator)


def test_decimation_and_interpolation_refusals():
    x = js(np.ones(100))
    fails(sampling, "input.missing", "factor", operation="decimate", samples=x, sample_rate="1 kHz")
    fails(sampling, "input.invalid", "factor", operation="decimate", samples=x, sample_rate="1 kHz", factor=1)
    fails(sampling, "input.out_of_range", "factor", operation="decimate", samples=x, sample_rate="1 kHz", factor=1001)
    fails(sampling, "input.out_of_range", "factor", operation="decimate", samples=x, sample_rate="1 kHz", factor=-3)
    fails(sampling, "input.invalid", "attenuation_db", operation="decimate", samples=x, sample_rate="1 kHz", factor=2, attenuation_db=200.0)
    fails(sampling, "input.invalid", "passband_fraction", operation="decimate", samples=x, sample_rate="1 kHz", factor=2, passband_fraction=0.99)
    fails(sampling, "input.missing", "sample_rate", operation="decimate", samples=x, factor=2)
    fails(sampling, "input.missing", "samples", operation="decimate", sample_rate="1 kHz", factor=2)
    fails(sampling, "input.invalid", "samples", operation="decimate", samples="[1, NaN, 3]", sample_rate="1 kHz", factor=2)
    fails(sampling, "input.out_of_range", "attenuation_db", operation="decimate", samples=x, sample_rate="1 kHz", factor=1000, attenuation_db=140.0, passband_fraction=0.95)  # > 4095 taps
    fails(sampling, "input.out_of_range", "factor", operation="interpolate", samples=js(np.ones(5000)), sample_rate="1 kHz", factor=1000)  # 5 million output samples
    fails(sampling, "input.invalid", "sample_rate", operation="decimate", samples=x, sample_rate="-1 kHz", factor=2)
    fails(sampling, "units.dimension_mismatch", "sample_rate", operation="alias", frequency="1 kHz", sample_rate="1 m")
    fails(sampling, "units.bare_number", "frequency", operation="alias", frequency="1300", sample_rate="1 kHz")
    fails(sampling, "input.invalid", "frequency", operation="alias", frequency="-1 kHz", sample_rate="1 kHz")
    fails(sampling, "input.invalid", "sample_rate", operation="alias", frequency="1 kHz", sample_rate="0 Hz")
