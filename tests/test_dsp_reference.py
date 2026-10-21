"""The dsp tools against scipy.signal (imported only here, behind importorskip; the package never imports scipy).

Each comparison states its tolerance.  The designs are deterministic closed forms, so they should agree with scipy to rounding.
"""

import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.dsp import _apply as ap
from eng_toolkit.domains.dsp import _iir as iir
from eng_toolkit.domains.dsp import _windows as win
from eng_toolkit.domains.dsp.correlate import correlate, cross_covariance
from eng_toolkit.domains.dsp.filter_apply import filter_apply
from eng_toolkit.domains.dsp.filter_design import filter_design
from eng_toolkit.domains.dsp.spectrum import spectrum
from tests.dsp_testing import js, ok, table, v

sig = pytest.importorskip("scipy.signal")

BTYPE = {"lowpass": "lowpass", "highpass": "highpass", "bandpass": "bandpass", "bandstop": "bandstop"}


def edges_for(response, fs, rng):
    nyq = fs / 2
    if response in ("lowpass", "highpass"):
        return [float(rng.uniform(0.05, 0.9)) * nyq]
    lo = float(rng.uniform(0.05, 0.6))
    return [lo * nyq, (lo + float(rng.uniform(0.05, 0.3))) * nyq]


def cutoff_text(edges):
    return ", ".join(f"{e!r} Hz" for e in edges)


def scipy_design(family, n, edges, fs, response, rp=1.0, rs=40.0, output="sos"):
    wn = [e / (fs / 2) for e in edges] if len(edges) > 1 else edges[0] / (fs / 2)
    if family == "butterworth":
        return sig.butter(n, wn, btype=BTYPE[response], output=output)
    if family == "chebyshev1":
        return sig.cheby1(n, rp, wn, btype=BTYPE[response], output=output)
    return sig.cheby2(n, rs, wn, btype=BTYPE[response], output=output)


# ---------------------------------------------------------------------------------------------------- IIR designs


@pytest.mark.parametrize("family", ["butterworth", "chebyshev1", "chebyshev2"])
@pytest.mark.parametrize("response", ["lowpass", "highpass", "bandpass", "bandstop"])
def test_designs_by_order_match_scipy_responses_and_coefficients(family, response):
    rng = np.random.default_rng(sum(map(ord, family + response)))
    worst_resp, worst_coef, worst_sos = 0.0, 0.0, 0.0
    for _ in range(25):
        fs = float(rng.choice([1000.0, 8000.0, 44100.0]))
        n = int(rng.integers(1, 9))
        edges = edges_for(response, fs, rng)
        r = ok(filter_design, family=family, response=response, sample_rate=f"{fs!r} Hz", order=n, cutoff=cutoff_text(edges), passband_ripple_db=1.0, stopband_attenuation_db=40.0, points=16)
        ref_sos = scipy_design(family, n, edges, fs, response)
        w = np.linspace(1e-3, math.pi - 1e-3, 700)
        _, h_ref = sig.sosfreqz(ref_sos, worN=w)
        h = iir.sos_response(np.array(r.data["sos"]), w)
        worst_resp = max(worst_resp, float(np.max(np.abs(h - h_ref))))
        if r.data["digital_order"] <= 6:  # direct-form coefficients are only comparable at low order
            b_ref, a_ref = scipy_design(family, n, edges, fs, response, output="ba")
            worst_coef = max(worst_coef, float(np.max(np.abs(np.array(r.data["b"]) - b_ref))), float(np.max(np.abs(np.array(r.data["a"]) - a_ref))))
    assert worst_resp < 1e-8, worst_resp   # sections against scipy's sections, on a 700-point grid: tolerance 1e-8 absolute on |H| <= 1 (observed ~1e-12)
    assert worst_coef < 1e-9, worst_coef   # b and a against scipy's, order <= 6: tolerance 1e-9 (observed ~1e-15)


@pytest.mark.parametrize("family, scipy_ord", [("butterworth", "buttord"), ("chebyshev1", "cheb1ord"), ("chebyshev2", "cheb2ord")])
@pytest.mark.parametrize("response", ["lowpass", "highpass", "bandpass", "bandstop"])
def test_minimum_order_matches_scipy_and_the_edges_agree(family, scipy_ord, response):
    # Order: exactly equal (integer).  Edges: scipy's band-stop search uses scipy.optimize.fminbound with xtol 1e-5, so the band-stop edges agree to
    # about 1e-5 of the Nyquist frequency (ours are the golden-section optimum to 1e-12); all other edges agree to 1e-9.
    rng = np.random.default_rng(sum(map(ord, family + response)) + 1)
    compared = 0
    for _ in range(40):
        fs = float(rng.choice([1000.0, 8000.0, 48000.0]))
        nyq = fs / 2
        rp = float(rng.uniform(0.1, 3.0))
        rs = float(rng.uniform(rp + 10, 90.0))
        if response == "lowpass":
            p = [float(rng.uniform(0.05, 0.4)) * fs]
            s = [p[0] + float(rng.uniform(0.02, 0.1)) * fs]
        elif response == "highpass":
            s = [float(rng.uniform(0.05, 0.3)) * fs]
            p = [s[0] + float(rng.uniform(0.02, 0.1)) * fs]
        else:
            c, bw, tw = float(rng.uniform(0.15, 0.3)) * fs, float(rng.uniform(0.03, 0.08)) * fs, float(rng.uniform(0.02, 0.06)) * fs
            inner, outer = [c - bw / 2, c + bw / 2], [c - bw / 2 - tw, c + bw / 2 + tw]
            p, s = (inner, outer) if response == "bandpass" else (outer, inner)
        if max(p + s) >= nyq * 0.98 or min(p + s) <= 0:
            continue
        wp = [e / nyq for e in p] if len(p) > 1 else p[0] / nyq
        ws = [e / nyq for e in s] if len(s) > 1 else s[0] / nyq
        n_ref, wn_ref = getattr(sig, scipy_ord)(wp, ws, rp, rs)
        r = filter_design(family=family, response=response, sample_rate=f"{fs!r} Hz", passband_edge=cutoff_text(p), stopband_edge=cutoff_text(s), passband_ripple_db=rp, stopband_attenuation_db=rs, points=16)
        if n_ref > 40:
            assert r.ok is False and r.error.family == "input.out_of_range"
            continue
        assert r.ok, r.error
        assert r.data["order"] == n_ref, (family, response, r.data["order_equation"], n_ref)
        ours = np.array(r.data["edges_hz"]) / nyq
        tol = 2e-5 if response == "bandstop" else 1e-9
        assert ours == pytest.approx(np.atleast_1d(wn_ref), abs=tol, rel=1e-9 if response != "bandstop" else 1e-4), (family, response, ours, wn_ref)
        assert all(c["met"] for c in r.data["spec_checks"] if "met" in c)
        compared += 1
    assert compared >= 15


def test_the_closed_form_order_equations_against_scipy_for_the_worked_example():
    # lowpass 1 kHz / 2 kHz at 8 kHz, 1 dB / 40 dB: scipy buttord gives 6, cheb1ord 4 (the hand derivation in test_dsp_filter_design)
    assert sig.buttord(1000 / 4000, 2000 / 4000, 1, 40)[0] == 6 and sig.cheb1ord(1000 / 4000, 2000 / 4000, 1, 40)[0] == 4


# ---------------------------------------------------------------------------------------------------- FIR


@pytest.mark.parametrize("family, window", [("fir_rectangular", "boxcar"), ("fir_hann", "hann"), ("fir_hamming", "hamming"), ("fir_blackman", "blackman"), ("fir_kaiser", ("kaiser", 6.5))])
@pytest.mark.parametrize("response", ["lowpass", "highpass", "bandpass", "bandstop"])
def test_window_method_taps_match_firwin(family, window, response):
    rng = np.random.default_rng(sum(map(ord, family + response)))
    for _ in range(10):
        fs = float(rng.choice([2.0, 1000.0, 48000.0]))
        n = int(rng.choice([31, 63, 101, 255]))
        edges = edges_for(response, fs, rng)
        r = ok(filter_design, family=family, response=response, sample_rate=f"{fs!r} Hz", order=n, cutoff=cutoff_text(edges), kaiser_beta=6.5, points=16)
        pass_zero = {"lowpass": True, "highpass": False, "bandpass": False, "bandstop": True}[response]
        ref = sig.firwin(n, edges if len(edges) > 1 else edges[0], window=window, pass_zero=pass_zero, fs=fs)
        assert np.max(np.abs(np.array(r.data["coefficients"]) - ref)) < 1e-13  # same formula and scaling: tolerance 1e-13 (observed ~1e-16)


def test_kaiser_order_and_beta_match_kaiserord():
    for atten, width in ((60.0, 0.2), (40.0, 0.1), (80.0, 0.05), (30.0, 0.3), (110.0, 0.02)):
        n_ref, beta_ref = sig.kaiserord(atten, width)
        fs = 2.0  # width is a fraction of Nyquist (= 1 Hz): the transition runs from 0.4 to 0.4 + width Hz
        r = ok(filter_design, family="fir_kaiser", response="lowpass", sample_rate=f"{fs!r} Hz", passband_edge="0.4 Hz", stopband_edge=f"{0.4 + width!r} Hz", stopband_attenuation_db=atten, points=16)
        assert r.data["num_taps"] == n_ref and r.data["beta"] == pytest.approx(beta_ref, abs=1e-12)
    assert sig.kaiser_beta(60.0) == pytest.approx(win.kaiser_beta(60.0), abs=1e-12)


@pytest.mark.parametrize("name, ref", [("hann", "hann"), ("hamming", "hamming"), ("blackman", "blackman"), ("flattop", "flattop"), ("rectangular", "boxcar")])
@pytest.mark.parametrize("n, periodic", [(64, True), (65, True), (64, False), (65, False)])
def test_windows_match_get_window(name, ref, n, periodic):
    assert np.max(np.abs(win.window_values(name, n, periodic=periodic) - sig.get_window(ref, n, fftbins=periodic))) < 1e-14
    assert np.max(np.abs(win.window_values("kaiser", n, periodic=periodic, beta=7.0) - sig.get_window(("kaiser", 7.0), n, fftbins=periodic))) < 1e-14


# ---------------------------------------------------------------------------------------------------- responses


def test_table_and_group_delay_match_freqz_and_group_delay():
    r = ok(filter_design, family="chebyshev1", response="bandpass", sample_rate="8 kHz", order=3, cutoff="900 Hz, 1700 Hz", passband_ripple_db=0.5, points=300)
    t = table(r, "filter_response.csv")
    sos = np.array(r.data["sos"])
    w = t["frequency_hz"] * 2 * math.pi / 8000
    _, h_ref = sig.sosfreqz(sos, worN=w)
    mag_ref = 20 * np.log10(np.maximum(np.abs(h_ref), 1e-15))
    assert np.max(np.abs(t["magnitude_db"][1:-1] - mag_ref[1:-1])) < 1e-6  # dB; the floor -300 dB differs only at exact zeros
    ph_ref = np.degrees(np.unwrap(np.angle(h_ref)))
    keep = np.abs(h_ref) > 1e-6
    assert np.max(np.abs((t["phase_deg"] - ph_ref)[keep] - ((t["phase_deg"] - ph_ref)[keep][0]))) < 1e-6  # equal up to a whole multiple of 360 degrees
    b, a = np.array(r.data["b"]), np.array(r.data["a"])
    for row in r.data["group_delay"]:
        wq = 2 * math.pi * row["frequency_hz"] / 8000
        if wq <= 0.0 or abs(wq - math.pi) < 1e-9:
            continue
        _, gd = sig.group_delay((b, a), w=[wq])
        assert row["group_delay_samples"] == pytest.approx(float(gd[0]), abs=1e-5)  # direct-form polynomials amplify rounding: 1e-5 samples


# ---------------------------------------------------------------------------------------------------- spectra


@pytest.mark.parametrize("window", ["hann", "hamming", "blackman", "flattop", "rectangular"])
@pytest.mark.parametrize("seg, overlap", [(256, 50.0), (200, 0.0), (333, 75.0), (1000, 0.0)])
def test_welch_psd_and_amplitude_match_scipy(window, seg, overlap):
    rng = np.random.default_rng(4)
    x = rng.normal(size=2000) + 0.5
    fs = 1000.0
    nover = int(round(seg * overlap / 100.0))
    ref_name = "boxcar" if window == "rectangular" else window
    f_ref, p_ref = sig.welch(x, fs=fs, window=ref_name, nperseg=seg, noverlap=nover, detrend=False, scaling="density")
    _, s_ref = sig.welch(x, fs=fs, window=ref_name, nperseg=seg, noverlap=nover, detrend=False, scaling="spectrum")
    r = ok(spectrum, samples=js(x), sample_rate="1000 Hz", window=window, segment_length=seg, overlap_percent=overlap, table_rows=1000)
    t = table(r, "spectrum.csv")
    assert t["frequency_hz"] == pytest.approx(f_ref, rel=1e-12, abs=1e-12)
    assert np.max(np.abs(t["psd_unit2_per_hz"] - p_ref) / np.maximum(p_ref, 1e-300)) < 1e-11  # relative 1e-11 (observed ~1e-14)
    c = np.full(len(f_ref), 2.0)
    c[0] = 1.0
    if seg % 2 == 0:
        c[-1] = 1.0
    # scipy's 'spectrum' is c |X|^2/(sum w)^2 averaged; our amplitude is c sqrt(mean |X|^2)/sum(w), so amplitude^2 = c x scipy
    assert np.max(np.abs(t["amplitude"] ** 2 - c * s_ref)) < 1e-12 * max(1.0, float(np.max(c * s_ref)))


def test_a_single_fft_matches_scipy_welch_with_one_segment():
    x = np.random.default_rng(8).normal(size=1024)
    _, p_ref = sig.welch(x, fs=1024.0, window="hann", nperseg=1024, noverlap=0, detrend=False)
    t = table(ok(spectrum, samples=js(x), sample_rate="1024 Hz", window="hann"), "spectrum.csv")
    assert np.max(np.abs(t["psd_unit2_per_hz"] - p_ref) / np.maximum(p_ref, 1e-300)) < 1e-11


# ---------------------------------------------------------------------------------------------------- correlation and envelope


@pytest.mark.parametrize("nx, ny", [(50, 50), (64, 40), (30, 77)])
def test_cross_covariance_matches_scipy_correlate(nx, ny):
    rng = np.random.default_rng(nx * 100 + ny)
    x, y = rng.normal(size=nx), rng.normal(size=ny)
    lag = max(nx, ny) - 1
    lags, c = cross_covariance(x, y, lag)
    ref = sig.correlate(y, x, mode="full")  # sum_n y[n + k] x[n]
    ref_lags = sig.correlation_lags(ny, nx, mode="full")
    lookup = dict(zip(ref_lags.tolist(), ref.tolist()))
    for k, val in zip(lags.tolist(), c.tolist()):
        assert val == pytest.approx(lookup.get(k, 0.0), abs=1e-12)  # outside scipy's range the sum is empty (0)


def test_tool_delay_agrees_with_scipy_peak_of_correlate():
    rng = np.random.default_rng(12)
    x = rng.normal(size=500)
    y = np.concatenate([np.zeros(23), x[:-23]]) + 0.1 * rng.normal(size=500)
    r = ok(correlate, samples=js(x), samples_b=js(y), remove_mean=False)
    lags = sig.correlation_lags(len(y), len(x))
    assert v(r, "lag_samples") == float(lags[int(np.argmax(sig.correlate(y, x)))]) == 23.0


def test_hilbert_envelope_matches_scipy_hilbert():
    rng = np.random.default_rng(6)
    for n in (64, 65, 1000):
        x = rng.normal(size=n)
        e = table(ok(correlate, samples=js(x), envelope=True), "envelope.csv")["envelope"]
        assert np.max(np.abs(e - np.abs(sig.hilbert(x)))) < 1e-12


# ---------------------------------------------------------------------------------------------------- filtering


def test_causal_and_zero_phase_filtering_match_scipy():
    rng = np.random.default_rng(10)
    for n in (200, 201):
        x = rng.normal(size=n)
        d = ok(filter_design, family="butterworth", response="lowpass", sample_rate="1 kHz", order=4, cutoff="120 Hz", points=16)
        sos = np.array(d.data["sos"])
        b, a = np.array(d.data["b"]), np.array(d.data["a"])
        y = table(ok(filter_apply, samples=js(x), sos=json.dumps(d.data["sos"])), "filtered.csv")["output"]
        assert np.max(np.abs(y - sig.sosfilt(sos, x))) < 1e-12
        y = table(ok(filter_apply, samples=js(x), b=json.dumps(d.data["b"]), a=json.dumps(d.data["a"])), "filtered.csv")["output"]
        assert np.max(np.abs(y - sig.lfilter(b, a, x))) < 1e-12
        yz = table(ok(filter_apply, samples=js(x), sos=json.dumps(d.data["sos"]), zero_phase=True), "filtered.csv")["output"]
        assert np.max(np.abs(yz - sig.sosfiltfilt(sos, x))) < 1e-9  # same odd extension and steady-state start: tolerance 1e-9 (observed ~1e-13)
        yz = table(ok(filter_apply, samples=js(x), b=json.dumps(d.data["b"]), a=json.dumps(d.data["a"]), zero_phase=True), "filtered.csv")["output"]
        assert np.max(np.abs(yz - sig.filtfilt(b, a, x))) < 1e-9
    h = np.random.default_rng(1).normal(size=31) / 10
    y = table(ok(filter_apply, samples=js(x), b=json.dumps(h.tolist()), zero_phase=True), "filtered.csv")["output"]
    assert np.max(np.abs(y - sig.filtfilt(h, [1.0], x))) < 1e-9


def test_lfilter_zi_matches_scipy():
    d = ok(filter_design, family="chebyshev1", response="lowpass", sample_rate="1 kHz", order=5, cutoff="100 Hz", passband_ripple_db=1.0, points=16)
    b, a = np.array(d.data["b"]), np.array(d.data["a"])
    assert np.max(np.abs(ap.lfilter_zi(b, a) - sig.lfilter_zi(b, a))) < 1e-9
    sos = np.array(d.data["sos"])
    assert np.max(np.abs(ap.sos_zi(sos) - sig.sosfilt_zi(sos))) < 1e-12
    assert ap.padlen_for(sos, None, None) == 3 * (2 * len(sos) + 1 - min(int((sos[:, 2] == 0).sum()), int((sos[:, 5] == 0).sum())))
