"""dsp_spectrum: scaling, Parseval, converter metrics, Welch, files; every expected number derived by hand."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp.spectrum import spectrum
from tests.dsp_testing import fails, js, ok, sine, table, v

N = 1024
FS = "1024 Hz"  # fs = N: bin k is at k Hz


def spec(x, **kw):
    kw.setdefault("sample_rate", FS)
    return ok(spectrum, samples=js(x), **kw)


# ---------------------------------------------------------------------------------------------------- scaling


@pytest.mark.parametrize("window, gain", [("rectangular", 1.0), ("hann", 0.5), ("hamming", 0.54), ("blackman", 0.42), ("flattop", 0.21557895)])
def test_an_on_bin_sine_reads_its_amplitude_with_the_coherent_gain_correction(window, gain):
    # x = 1.5 sin(2 pi 37 k/N + 0.3).  Bin 37 of the windowed DFT is (A/2) sum(w) e^{j phi}; the amplitude is 2|X|/sum(w).
    # sum(w) = N a0 for a periodic cosine-sum window (a0 = 1, 0.5, 0.54, 0.42, 0.21557895), so the reading is exactly A = 1.5.
    # The windows of a periodic cosine sum have a spectrum of finite support, so the image at -37 does not touch bin 37: exact up to rounding.
    # Tolerance 1e-12 (observed about 3e-15).
    r = spec(sine(1.5, 37, N, 0.3), window=window)
    t = table(r, "spectrum.csv")
    assert t["amplitude"][37] == pytest.approx(1.5, abs=1e-12)
    assert v(r, "peak_amplitude") == pytest.approx(1.5, abs=1e-12)
    assert v(r, "peak_frequency") == pytest.approx(37.0, abs=1e-12)
    assert r.data["coherent_gain"] == pytest.approx(gain, abs=1e-12)
    assert r.data["amplitude_correction_factor"] == pytest.approx(1.0 / gain, rel=1e-12)
    others = np.delete(t["amplitude"], [36, 37, 38])
    assert others.max() < 0.8 or window in ("hann", "hamming", "blackman", "flattop")  # leakage stays in the lobe
    mask = np.abs(np.arange(len(t["amplitude"])) - 37) > win.LOBE_HALF_WIDTH[window]
    assert t["amplitude"][mask].max() < 1e-12  # nothing outside the main lobe for an on-bin tone


def test_enbw_and_bin_width_by_hand():
    # Hann: sum(w^2) = 3N/8, sum(w) = N/2 -> ENBW = N (3N/8)/(N/2)^2 = 1.5 bins; bin width = fs/N = 1 Hz (tolerance 1e-12)
    r = spec(sine(1.0, 37, N), window="hann")
    assert v(r, "bin_width") == pytest.approx(1.0, abs=1e-12) and v(r, "enbw") == pytest.approx(1.5, abs=1e-12)
    # Hamming: (0.54^2 + 0.46^2/2)/0.54^2 = (0.2916 + 0.1058)/0.2916 = 1.362826;  Blackman: (0.42^2 + (0.5^2 + 0.08^2)/2)/0.42^2 = 0.3046/0.1764 = 1.726757
    assert v(spec(sine(1.0, 37, N), window="hamming"), "enbw") == pytest.approx(1.362826, abs=1e-6)
    assert v(spec(sine(1.0, 37, N), window="blackman"), "enbw") == pytest.approx(1.726757, abs=1e-6)
    assert v(spec(sine(1.0, 37, N), window="rectangular"), "enbw") == pytest.approx(1.0, abs=1e-12)


def test_dc_and_nyquist_are_not_doubled():
    # x = 2 + 0.5 cos(pi k): DC bin reads 2, the Nyquist bin 0.5 (c_k = 1 there); an interior bin would be doubled.
    x = 2.0 + 0.5 * np.cos(np.pi * np.arange(N))
    t = table(spec(x, window="rectangular", peaks=3), "spectrum.csv")
    assert t["amplitude"][0] == pytest.approx(2.0, abs=1e-12) and t["amplitude"][-1] == pytest.approx(0.5, abs=1e-12)


@pytest.mark.parametrize("window", ["rectangular", "hann", "hamming", "blackman", "flattop"])
def test_parseval_the_integral_of_the_psd_is_the_mean_square(window):
    # sum over bins of PSD x bin width = sum((w x)^2)/sum(w^2); for the rectangular window that is the mean square of x, sum(x^2)/N.
    # Equivalent: PSD integral x N = sum of squares (energy/dt).  Tolerance 1e-12 relative on the tool's own integral, 1e-12 through the CSV too (the CSV holds shortest round-trip reprs).
    rng = np.random.default_rng(7)
    x = rng.normal(size=N) + 0.3
    w = win.window_values(window, N, periodic=True)
    expected = float(np.sum((w * x) ** 2) / np.sum(w * w))
    r = spec(x, window=window)
    assert v(r, "integrated_power") == pytest.approx(expected, rel=1e-12)
    t = table(r, "spectrum.csv")
    assert float(np.sum(t["psd_unit2_per_hz"]) * (1024.0 / N)) == pytest.approx(expected, rel=1e-12)
    if window == "rectangular":
        assert v(r, "integrated_power") * N == pytest.approx(float(np.sum(x * x)), rel=1e-12)


def test_psd_of_white_noise_is_flat_at_variance_over_half_fs():
    # white noise of variance s2 sampled at fs: one-sided PSD = 2 s2/fs.  The average over the bins converges to it: with 2^16 samples and
    # Welch segments of 1024 (127 averages at 50 % overlap) the mean over the interior bins is within 2 % (statistical, not exact).
    rng = np.random.default_rng(3)
    x = rng.normal(scale=2.0, size=1 << 16)
    r = ok(spectrum, samples=js(x), sample_rate="1000 Hz", window="hann", segment_length=1024, overlap_percent=50)
    psd = table(r, "spectrum.csv")["psd_unit2_per_hz"][5:-5]
    assert psd.mean() == pytest.approx(2 * 4.0 / 1000.0, rel=0.02)
    assert r.data["segments"] == (65536 - 512) // 512


# ---------------------------------------------------------------------------------------------------- converter metrics


def test_thd_is_sqrt_of_the_harmonic_powers_over_the_fundamental():
    # a1 = 1 at bin 50, a2 = 0.01 at bin 100, a3 = 0.003 at bin 150: THD = sqrt(0.01^2 + 0.003^2)/1 = 0.0104403065 (-39.6252 dB).
    # Tolerance 1e-12 on the ratio (coherent record, lobes do not overlap).
    x = sine(1.0, 50, N) + sine(0.01, 100, N, 1.0) + sine(0.003, 150, N, 2.0)
    for window in ("rectangular", "hann", "blackman", "flattop"):
        r = spec(x, window=window, adc_metrics=True, harmonics=5)
        assert v(r, "thd_ratio") == pytest.approx(math.sqrt(0.01 ** 2 + 0.003 ** 2), abs=1e-12)
        assert v(r, "thd_db") == pytest.approx(20 * math.log10(math.sqrt(0.01 ** 2 + 0.003 ** 2)), abs=1e-9)
        assert r.data["adc_metrics"]["harmonics"][0]["amplitude"] == pytest.approx(0.01, abs=1e-12)


def test_snr_sinad_sfdr_and_enob_of_a_sine_plus_known_spurs():
    # A = 1 at bin 50 (power 1/2); harmonics a2 = 0.01 (bin 100) and a3 = 0.003 (bin 150); other spurs 0.002 at bin 123, 0.001 at 175, 0.0005 at 333
    # (every spur is more than 2 x 5 + 1 bins from a harmonic bin, so even the flat-top lobes of +-5 bins do not touch: a spur closer than that to a harmonic
    # position is indistinguishable from the harmonic, see docs/dsp/design.md).
    # noise power = (0.002^2 + 0.001^2 + 0.0005^2)/2 = 2.625e-6;  harmonic power = (0.01^2 + 0.003^2)/2 = 5.45e-5
    # SNR = 10 log10(0.5/2.625e-6) = 52.7984 dB;  SINAD = 10 log10(0.5/(2.625e-6 + 5.45e-5)) = 39.4... computed below;  SFDR = 20 log10(1/0.01) = 40 dB
    # ENOB = (SINAD - 1.76)/6.02.  Tolerance 1e-9 dB (observed 1e-12).
    x = sine(1.0, 50, N) + sine(0.01, 100, N, 1.0) + sine(0.003, 150, N, 2.0) + sine(0.002, 123, N, 0.5) + sine(0.001, 175, N, 1.5) + sine(0.0005, 333, N, 2.5)
    noise, harm = (0.002 ** 2 + 0.001 ** 2 + 0.0005 ** 2) / 2, (0.01 ** 2 + 0.003 ** 2) / 2
    for window in ("rectangular", "hann", "hamming", "blackman", "flattop"):
        r = spec(x, window=window, adc_metrics=True, harmonics=5, full_scale_amplitude=1.0)
        assert v(r, "snr_db") == pytest.approx(10 * math.log10(0.5 / noise), abs=1e-9)
        assert v(r, "sinad_db") == pytest.approx(10 * math.log10(0.5 / (noise + harm)), abs=1e-9)
        assert v(r, "sfdr_dbc") == pytest.approx(40.0, abs=1e-9)
        assert v(r, "enob_bits") == pytest.approx((10 * math.log10(0.5 / (noise + harm)) - 1.76) / 6.02, abs=1e-9)
        assert v(r, "fundamental_amplitude") == pytest.approx(1.0, abs=1e-12)
        assert v(r, "fundamental_frequency") == pytest.approx(50.0, abs=1e-9)
    assert 10 * math.log10(0.5 / noise) == pytest.approx(52.7984, abs=1e-4)


def test_enob_accounts_for_a_tone_below_full_scale():
    # A = 0.5 against full scale 1: level = 20 log10(0.5) = -6.0206 dBFS; ENOB = (SINAD - 1.76 + 6.0206)/6.02.
    x = sine(0.5, 50, N) + sine(0.0005, 123, N)
    r = spec(x, window="hann", adc_metrics=True, full_scale_amplitude=1.0)
    sinad = 10 * math.log10((0.25 / 2) / (0.0005 ** 2 / 2))
    assert v(r, "sinad_db") == pytest.approx(sinad, abs=1e-9)
    assert v(r, "enob_bits") == pytest.approx((sinad - 1.76 + 6.0206) / 6.02, abs=1e-6)
    assert r.data["adc_metrics"]["signal_level_dbfs"] == pytest.approx(-6.0206, abs=1e-4)


def test_harmonics_above_nyquist_fold_back_into_the_spectrum():
    # fs = 1024, fundamental at bin 300: the 2nd harmonic 600 folds to 1024 - 600 = 424, the 3rd 900 to 124.
    x = sine(1.0, 300, N) + sine(0.01, 424, N, 1.0) + sine(0.005, 124, N, 2.0)
    r = spec(x, window="hann", adc_metrics=True, harmonics=3)
    assert v(r, "thd_ratio") == pytest.approx(math.hypot(0.01, 0.005), abs=1e-12)
    assert [h["bin"] for h in r.data["adc_metrics"]["harmonics"]] == [424, 124]


def test_a_harmonic_that_lands_on_the_fundamental_is_skipped_and_said_so():
    # fundamental at bin 256: the 4th harmonic folds to 1024 - 1024 = 0 (DC), the 5th to 256 (on the fundamental).
    x = sine(1.0, 256, N) + 0.01 * np.cos(2 * np.pi * 2 * 256 * np.arange(N) / N)
    r = spec(x, window="rectangular", adc_metrics=True, harmonics=5)
    skipped = {s["harmonic"] for s in r.data["adc_metrics"]["harmonics_skipped"]}
    assert 4 in skipped and 5 in skipped


def test_a_simulated_quantiser_lands_within_0_3_db_of_6_02_n_plus_1_76():
    # A full-scale sine (amplitude 1 of range +-1) quantised to N bits with step 2/2^N: error variance step^2/12, signal power 1/2:
    # SNR = 10 log10((1/2)(12)/(2/2^N)^2) = 6.0206 N + 1.7609 (the textbook 6.02 N + 1.76: 49.92, 61.96, 74.00 dB for N = 8, 10, 12).
    # 2^16 samples, 4099 cycles (coherent, prime, so the error is spread over many harmonics that SINAD counts): tolerance 0.3 dB (stated).
    # A non-coherent tone with a Blackman window reads 0.4 dB low at 8 bits: its leakage floor (about -60 dB) is comparable to the noise.
    for bits in (8, 10, 12):
        n = 1 << 16
        x = np.sin(2 * np.pi * 4099 * np.arange(n) / n)
        step = 2.0 / 2 ** bits
        q = np.round(x / step) * step
        r = ok(spectrum, samples=js(q), sample_rate="1 kHz", window="rectangular", adc_metrics=True, full_scale_amplitude=1.0)
        expected = 6.02 * bits + 1.76
        assert v(r, "sinad_db") == pytest.approx(expected, abs=0.3), (bits, v(r, "sinad_db"))
        assert v(r, "enob_bits") == pytest.approx(bits, abs=0.05)


def test_a_non_coherent_tone_with_a_window_is_leakage_limited():
    # documented limit: Blackman side lobes put a floor of roughly -60 dB on SINAD for a tone between bins
    n = 1 << 14
    r = ok(spectrum, samples=js(np.sin(2 * np.pi * 0.1234567 * np.arange(n))), sample_rate="1 kHz", window="blackman", adc_metrics=True)
    assert 55.0 < v(r, "sinad_db") < 150.0


def test_converter_metrics_refuse_a_record_too_short_for_the_window():
    fails(spectrum, "input.invalid", "adc_metrics", samples=js(sine(1.0, 2, 16)), sample_rate="16 Hz", window="flattop", adc_metrics=True)


# ---------------------------------------------------------------------------------------------------- peaks and warnings


def test_peak_list_with_parabolic_interpolation():
    # Hann, tone at 100.3 bins amplitude 1.5: the parabola through ln(amplitude) of the three bins recovers frequency to 0.02 bin and
    # amplitude to 4 % (measured worst case over all offsets: 0.016 bin and 3.8 %; the bin reading alone is low by up to 1.42 dB = 15 %).
    x = sine(1.5, 100.3, N, 0.4)
    r = spec(x, window="hann", peaks=3)
    p = r.data["peaks"][0]
    assert p["frequency_hz"] == pytest.approx(100.3, abs=0.02) and p["amplitude"] == pytest.approx(1.5, rel=0.04)
    assert abs(p["bin_amplitude"] - 1.5) > abs(p["amplitude"] - 1.5)  # the interpolated amplitude is better than the bin reading
    assert any("off a bin centre" in w for w in r.warnings)  # non-coherent record
    # Blackman: 0.007 bin and 1.1 % at worst
    p = spec(x, window="blackman", peaks=1).data["peaks"][0]
    assert p["frequency_hz"] == pytest.approx(100.3, abs=0.01) and p["amplitude"] == pytest.approx(1.5, rel=0.012)


def test_two_tones_are_listed_in_amplitude_order_and_close_ones_warn():
    x = sine(1.0, 100, N) + sine(0.5, 300, N, 1.0)
    r = spec(x, window="hann", peaks=5)
    assert [round(p["frequency_hz"]) for p in r.data["peaks"]] == [100, 300]
    r = spec(sine(1.0, 100, N) + sine(0.9, 102, N, 1.0), window="hann", peaks=5)
    assert any("cannot be resolved" in w for w in r.warnings)
    assert spec(x, window="hann", peaks=0).data["peaks"] == []


def test_a_dc_peak_is_listed():
    r = spec(np.full(N, 3.0) + sine(0.5, 100, N), window="rectangular", peaks=3)
    assert r.data["peaks"][0]["frequency_hz"] == 0.0 and r.data["peaks"][0]["amplitude"] == pytest.approx(3.0, abs=1e-12)


def test_non_coherent_rectangular_record_warns_about_leakage():
    r = spec(sine(1.0, 100.5, N), window="rectangular")
    assert any("rectangular window leaks" in w for w in r.warnings)
    assert v(r, "peak_amplitude") < 1.0  # scalloping: 3.9 dB down at a half-bin offset


def test_the_table_is_bounded_and_max_hold_decimated():
    rng = np.random.default_rng(1)
    x = rng.normal(size=1 << 14) + sine(1.0, 3001.0, 1 << 14)
    r = ok(spectrum, samples=js(x), sample_rate="16384 Hz", window="hann", table_rows=100)
    t = table(r, "spectrum.csv")
    assert len(t["frequency_hz"]) <= 100 and r.data["table"]["decimated"] is True and r.data["table"]["bins"] == 8193
    assert t["amplitude"].max() > 0.9  # the tone survives the decimation (max-hold)
    assert any("max-hold" in w for w in r.warnings)


def test_welch_averaging_of_a_coherent_tone_reads_its_amplitude():
    # 4096 samples, segments of 1024 at 50 % overlap -> (4096 - 512)//512 = 7 segments; a tone at 37 cycles per 1024 samples is on bin 37 of each.
    x = sine(1.5, 37.0 * 4, 4096, 0.2)
    r = ok(spectrum, samples=js(x), sample_rate=FS, window="hann", segment_length=1024, overlap_percent=50)
    assert r.data["segments"] == 7 and r.data["bin_width_hz"] == 1.0
    assert v(r, "peak_amplitude") == pytest.approx(1.5, abs=1e-12) and v(r, "peak_frequency") == pytest.approx(37.0, abs=1e-12)


def test_welch_reports_unused_samples():
    r = ok(spectrum, samples=js(np.zeros(1000) + sine(1.0, 10, 1000)), sample_rate="1 kHz", window="hann", segment_length=300, overlap_percent=0)
    assert r.data["segments"] == 3 and any("last 100 sample" in w for w in r.warnings)


# ---------------------------------------------------------------------------------------------------- files


def write(tmp_path, text, name="rec.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_a_one_column_file_with_a_sample_rate(tmp_path):
    x = sine(2.0, 37, N)
    path = write(tmp_path, "\n".join(repr(float(t)) for t in x) + "\n")
    r = ok(spectrum, samples_file=path, sample_rate=FS, window="hann")
    assert v(r, "peak_amplitude") == pytest.approx(2.0, abs=1e-12)
    assert r.provenance.input_files and r.provenance.input_files[0].bytes > 1000 and len(r.provenance.input_files[0].sha256) == 64
    assert r.data["source"] == "file"


def test_a_two_column_file_gives_the_sample_rate_from_the_time_column(tmp_path):
    t = np.arange(N) / 1024.0
    x = sine(2.0, 37, N)
    path = write(tmp_path, "\n".join(f"{float(a)!r},{float(b)!r}" for a, b in zip(t, x)) + "\n")
    r = ok(spectrum, samples_file=path, window="hann")
    assert r.data["sample_rate_hz"] == pytest.approx(1024.0, rel=1e-12) and v(r, "peak_frequency") == pytest.approx(37.0, abs=1e-9)
    fails(spectrum, "input.invalid", "sample_rate", samples_file=path, sample_rate="2000 Hz", window="hann")  # disagrees with the time column
    ok(spectrum, samples_file=path, sample_rate="1.024 kHz", window="hann")  # agrees


def test_files_are_data_only_and_errors_name_the_line(tmp_path):
    bad = write(tmp_path, "1\n2\n3\nabc\n5\n6\n7\n8\n9\n", "bad.csv")
    r = fails(spectrum, "input.invalid", "samples_file", samples_file=bad, sample_rate=FS)
    assert r.error.details["line"] == 4 and "line 4" in r.error.message
    for text, line in (("time,value\n0,1\n1,2\n", 1), ("1\n2\nnan\n", 3), ("1\n2\ninf\n", 3), ("1\n2\n0x10\n", 3), ("1\n2\n1_0\n", 3), ("1\n2\n3,4,5\n", 3), ("1,2\n3\n", 2), ("1\n2\n1e999\n", 3), ("1\n2\n__import__('os')\n", 3)):
        r = fails(spectrum, "input.invalid", "samples_file", samples_file=write(tmp_path, text, "x.csv"), sample_rate=FS)
        assert r.error.details["line"] == line, (text, r.error)
    fails(spectrum, "input.invalid", "samples_file", samples_file=write(tmp_path, "", "empty.csv"), sample_rate=FS)
    fails(spectrum, "input.invalid", "samples_file", samples_file=write(tmp_path, "1\n2\n3\x00\n", "nul.csv"), sample_rate=FS)


def test_file_problems(tmp_path, monkeypatch):
    fails(spectrum, "input.file_not_found", "samples_file", samples_file=str(tmp_path / "missing.csv"), sample_rate=FS)
    fails(spectrum, "input.unsupported_format", "samples_file", samples_file=write(tmp_path, "1\n", "data.bin"), sample_rate=FS)
    fails(spectrum, "input.file_not_found", "samples_file", samples_file=str(tmp_path), sample_rate=FS)  # a directory
    # a non-uniform time column
    path = write(tmp_path, "\n".join(f"{t!r},{t}" for t in (0.0, 1.0, 2.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0)), "t.csv")
    fails(spectrum, "input.invalid", "samples_file", samples_file=path)
    path = write(tmp_path, "\n".join(f"{t!r},{t}" for t in (0.0, 1.0, 1.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)), "t2.csv")
    fails(spectrum, "input.invalid", "samples_file", samples_file=path)
    # the read-directory allow list
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "ok.csv").write_text("\n".join(str(i % 3) for i in range(64)), encoding="utf-8")
    outside = write(tmp_path, "\n".join(str(i % 3) for i in range(64)), "outside.csv")
    monkeypatch.setenv("ENG_TOOLKIT_READ_DIRS", str(allowed))
    ok(spectrum, samples_file=str(allowed / "ok.csv"), sample_rate="64 Hz")
    fails(spectrum, "input.path_not_allowed", "samples_file", samples_file=outside, sample_rate="64 Hz")
    fails(spectrum, "input.path_not_allowed", "samples_file", samples_file=str(allowed / ".." / "outside.csv"), sample_rate="64 Hz")


def test_a_file_over_the_byte_cap_is_refused(tmp_path, monkeypatch):
    import eng_toolkit.domains.dsp._common as common

    monkeypatch.setattr(common, "MAX_FILE_BYTES", 1000)
    path = write(tmp_path, "\n".join(str(i % 7) for i in range(2000)), "big.csv")
    fails(spectrum, "input.too_large", "samples_file", samples_file=path, sample_rate=FS)


# ---------------------------------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "kwargs, family, param",
    [
        (dict(samples="[1, 2, NaN, 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, Infinity, 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, 1e999, 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, 1e200, 4, 5, 6, 7, 8, 9]"), "input.out_of_range", "samples"),
        (dict(samples="[1, 2, 'a', 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, \"3\", 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, true, 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[1, 2, null, 4, 5, 6, 7, 8, 9]"), "input.invalid", "samples"),
        (dict(samples="[[1, 2], [3, 4]]"), "input.invalid", "samples"),
        (dict(samples="{'a': 1}"), "input.invalid", "samples"),
        (dict(samples="__import__('os').system('echo pwned')"), "input.invalid", "samples"),
        (dict(samples="[1, 2, 3"), "input.invalid", "samples"),
        (dict(samples="[1, 2, 3]"), "input.invalid", "samples"),  # fewer than 8
        (dict(samples="[]"), "input.invalid", "samples"),
        (dict(samples="[" + "0," * 5_000_000 + "0]"), "input.out_of_range", "samples"),  # 5 million values
        (dict(samples="[" * 3000 + "]" * 3000), "input.invalid", "samples"),
        (dict(samples="x" * 100_000_001), "input.out_of_range", "samples"),
        (dict(samples=""), "input.missing", "samples"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", samples_file="x.csv"), "input.invalid", "samples"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="-1 kHz"), "input.invalid", "sample_rate"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="1 m"), "units.dimension_mismatch", "sample_rate"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="1 s"), "units.dimension_mismatch", "sample_rate"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate="1000"), "units.bare_number", "sample_rate"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", sample_rate=""), "input.missing", "sample_rate"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", window="kaiser"), "input.invalid", "window"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", window="blackman-harris"), "input.invalid", "window"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", window="__import__('os')"), "input.invalid", "window"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", overlap_percent=100.0), "input.invalid", "overlap_percent"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", overlap_percent=150.0), "input.invalid", "overlap_percent"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", overlap_percent=-1.0), "input.invalid", "overlap_percent"),
        (dict(samples="[0,1,0,-1,0,1,0,-1,0,1,0,-1,0,1,0,-1]", segment_length=32), "input.invalid", "segment_length"),
        (dict(samples="[0,1,0,-1,0,1,0,-1,0,1,0,-1,0,1,0,-1]", segment_length=4), "input.invalid", "segment_length"),
        (dict(samples="[0,1,0,-1,0,1,0,-1,0,1,0,-1,0,1,0,-1]", segment_length=-8), "input.invalid", "segment_length"),
        (dict(samples="[0,1,0,-1,0,1,0,-1,0,1,0,-1,0,1,0,-1]", segment_length=8.5), "input.invalid", "segment_length"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", peaks=101), "input.out_of_range", "peaks"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", harmonics=1), "input.out_of_range", "harmonics"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", table_rows=5), "input.out_of_range", "table_rows"),
        (dict(samples="[0,1,0,-1,0,1,0,-1]", full_scale_amplitude=-1.0), "input.invalid", "full_scale_amplitude"),
    ],
)
def test_bad_requests_are_refused(kwargs, family, param):
    base = {"sample_rate": "8 Hz"}
    fails(spectrum, family, param, **{**base, **kwargs})
