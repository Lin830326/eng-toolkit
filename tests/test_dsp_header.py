"""CSV files with a header line, wide files with a named column, and the unit of the signal (UAT F4-2, F4-6, M2-2)."""

import math
from pathlib import Path

import numpy as np
import pytest

from eng_toolkit.domains.dsp.correlate import correlate
from eng_toolkit.domains.dsp.filter_apply import filter_apply
from eng_toolkit.domains.dsp.sampling import sampling
from eng_toolkit.domains.dsp.spectrum import spectrum
from tests.dsp_testing import fails, ok, sine, v

N = 1024
FS = "1024 Hz"  # bin k is at k Hz


def write(tmp_path, text, name="x.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def column_text(header, x):
    return (header + "\n" if header else "") + "\n".join(repr(float(t)) for t in x) + "\n"


def test_a_header_line_is_skipped_and_reported(tmp_path):
    x = sine(2.0, 37, N)
    r = ok(spectrum, samples_file=write(tmp_path, column_text("accel_g", x)), sample_rate=FS)
    assert v(r, "peak_amplitude") == pytest.approx(2.0, abs=1e-12) and v(r, "peak_frequency") == pytest.approx(37.0, abs=1e-9)
    assert r.data["header_skipped"] == "accel_g" and r.data["samples"] == N
    plain = ok(spectrum, samples_file=write(tmp_path, column_text("", x), "plain.csv"), sample_rate=FS)
    assert plain.data["header_skipped"] is None  # no header: nothing skipped
    assert v(plain, "peak_amplitude") == v(r, "peak_amplitude")


def test_a_two_column_logger_export_with_a_header_gives_its_time_base(tmp_path):
    t = np.arange(N) / 1024.0
    x = sine(2.0, 37, N)
    text = "time_s,strain\n" + "\n".join(f"{float(a)!r},{float(b)!r}" for a, b in zip(t, x)) + "\n"
    r = ok(spectrum, samples_file=write(tmp_path, text), window="hann")
    assert r.data["sample_rate_hz"] == pytest.approx(1024.0, rel=1e-12) and r.data["header_skipped"] == "time_s,strain"
    assert v(r, "peak_frequency") == pytest.approx(37.0, abs=1e-9)
    c = ok(spectrum, samples_file=write(tmp_path, text, "c.csv"), window="hann", column="strain")
    assert c.data["column_used"] == "strain" and c.data["sample_rate_hz"] == pytest.approx(1024.0, rel=1e-12)


def test_garbage_is_still_refused_and_the_line_is_named(tmp_path):
    cases = (
        ("value\n1\n2\nabc\n", 4),  # a word among the numbers
        ("value\ntext\n1\n2\n", 2),  # two header-like lines
        ("1,2\nx,y\n3,4\n", 2),  # a header only counts on line 1
        ("1,abc\n2,3\n", 1),  # mixed numbers and words on line 1
        ("a b\n1\n2\n", 2),  # the header names two columns, the data has one
        ("__import__('os')\n1\n2\n3\n", 1),
        ("nan\n1\n2\n", 1),
        ("\n\nvalue\n1\n2\nnan\n", 6),
    )
    for text, line in cases:
        r = fails(spectrum, "input.invalid", "samples_file", samples_file=write(tmp_path, text), sample_rate=FS)
        assert r.error.details["line"] == line and f"line {line}" in r.error.message, (text, r.error)


def test_a_wide_file_needs_a_column_and_the_message_lists_the_names(tmp_path):
    text = "index,time_s,a,b\n" + "\n".join(f"{k},{k / 1024!r},{float(s)!r},{float(2 * s)!r}" for k, s in enumerate(sine(1.0, 37, N))) + "\n"
    path = write(tmp_path, text)
    r = fails(spectrum, "input.invalid", "samples_file", samples_file=path)
    assert "4 columns (index, time_s, a, b)" in r.error.message and "'column'" in r.error.message and r.error.details["line"] == 2
    a = ok(spectrum, samples_file=path, column="a", window="rectangular")
    b = ok(spectrum, samples_file=path, column="B", window="rectangular")  # names are case-insensitive
    n3 = ok(spectrum, samples_file=path, column="3", window="rectangular")  # or a 1-based number
    assert v(a, "peak_amplitude") == pytest.approx(1.0, abs=1e-9) and v(b, "peak_amplitude") == pytest.approx(2.0, abs=1e-9)
    assert n3.data["column_used"] == "a" and a.data["sample_rate_hz"] == pytest.approx(1024.0, rel=1e-9)  # the time_s column gave the rate
    r = fails(spectrum, "input.invalid", "column", samples_file=path, column="zzz")
    assert "index, time_s, a, b" in r.error.message
    r = fails(spectrum, "input.invalid", "column", samples_file=write(tmp_path, column_text("", sine(1.0, 5, 64)), "n.csv"), column="value", sample_rate=FS)
    assert "no header" in r.error.message


def test_the_output_of_dsp_filter_apply_can_be_analysed_again(tmp_path):
    # UAT F4-2: filtered.csv (index,time_s,input,output) was refused by dsp_spectrum.  A 5 Hz + 100 Hz sum through a 20 Hz low-pass keeps the 5 Hz tone.
    t = np.arange(2048) / 1024.0
    x = np.sin(2 * np.pi * 5 * t) + np.sin(2 * np.pi * 100 * t)
    filtered = ok(filter_apply, samples="[" + ",".join(repr(float(v_)) for v_ in x) + "]", sample_rate=FS, family="butterworth", response="lowpass", order=4, cutoff="20 Hz")
    path = str(next(Path(a.path) for a in filtered.artifacts if a.path.endswith("filtered.csv")))
    assert Path(path).read_text(encoding="utf-8").splitlines()[0] == "index,time_s,input,output"
    default = ok(spectrum, samples_file=path, window="hann")  # no column: the output column, with the rate from time_s
    assert default.data["column_used"] == "output" and default.data["sample_rate_hz"] == pytest.approx(1024.0, rel=1e-9) and default.data["header_skipped"] == "index,time_s,input,output"
    assert v(default, "peak_frequency") == pytest.approx(5.0, abs=0.1)
    inp = ok(spectrum, samples_file=path, window="hann", column="input", peaks=2)
    assert {round(p["frequency_hz"]) for p in inp.data["peaks"]} == {5, 100}  # the input still holds both tones
    assert v(default, "rms") < v(inp, "rms")  # the 100 Hz tone is gone from the output
    again = ok(filter_apply, samples_file=path, column="output", b="[0.5, 0.5]")
    assert again.data["column_used"] == "output"
    dec = ok(sampling, operation="decimate", factor=2, samples_file=path, column="output")
    assert dec.data["column_used"] == "output" and dec.data["header_skipped"] == "index,time_s,input,output"
    c = ok(correlate, samples_file=write(tmp_path, column_text("value", sine(1.0, 5, 64)), "c.csv"), sample_rate="64 Hz")
    assert c.data["header_skipped"] == "value"


def test_the_unit_of_the_signal_is_carried_to_the_values_and_the_table(tmp_path):
    # UAT F4-6: g data read 'dimensionless'.  A 2 g tone: peak amplitude 2 g, rms 2/sqrt(2) g, integrated power 2 g^2 (mean square of an on-bin sine A^2/2 = 2).
    x = sine(2.0, 37, N)
    r = ok(spectrum, samples="[" + ",".join(repr(float(t)) for t in x) + "]", sample_rate=FS, window="rectangular", unit="g")
    assert r.values["peak_amplitude"].unit == "standard_gravity" and v(r, "peak_amplitude") == pytest.approx(2.0, abs=1e-12)
    assert r.values["rms"].unit == "standard_gravity" and v(r, "rms") == pytest.approx(math.sqrt(2.0), abs=1e-12)
    assert "standard_gravity" in r.values["integrated_power"].unit and "2" in r.values["integrated_power"].unit
    assert v(r, "integrated_power") == pytest.approx(2.0, rel=1e-9)
    assert r.data["signal_unit"] == "g"
    header = next(Path(a.path) for a in r.artifacts if a.path.endswith("spectrum.csv")).read_text(encoding="utf-8").splitlines()[0]
    assert header == "frequency_hz,amplitude_g,psd_g2_per_hz" and r.data["table"]["columns"] == header.split(",")
    si = ok(spectrum, samples="[" + ",".join(repr(float(t)) for t in x) + "]", sample_rate=FS, unit="m/s^2")
    assert si.values["peak_amplitude"].unit == "meter / second ** 2" and si.data["signal_unit"] == "m/s^2"
    none = ok(spectrum, samples="[" + ",".join(repr(float(t)) for t in x) + "]", sample_rate=FS)
    assert none.values["peak_amplitude"].unit == "dimensionless" and none.data["signal_unit"] is None
    for bad in ("not_a_unit", "m;rm", "g" * 40):
        fails(spectrum, "units.parse_error", "unit", samples="[1,2,3,4,5,6,7,8]", sample_rate=FS, unit=bad)
