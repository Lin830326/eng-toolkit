"""The control tools against independent implementations (scipy.signal, scipy.linalg, python-control), when those are installed.

scipy is in the dev group and python-control is optional; both are imported only here, behind ``pytest.importorskip``.  The tools
themselves never import either.
"""

import numpy as np
import pytest

from eng_toolkit.domains.control import _lti as lti
from eng_toolkit.domains.control._poly import parse_polynomial
from eng_toolkit.domains.control.discretize import discretize
from eng_toolkit.domains.control.frequency import frequency
from eng_toolkit.domains.control.response import response
from tests.control_testing import ok, table, v

SYSTEMS = [
    ([16.0], [1.0, 4.0, 16.0]),
    ([1.0], [1.0, 3.0, 2.0]),
    ([2.0, 1.0], [1.0, 2.0, 5.0, 3.0]),
    ([1.0, -1.0], [1.0, 5.0, 8.0, 6.0]),  # non-minimum phase
    ([5.0], [1.0, 6.0, 15.0, 20.0, 15.0, 6.0]),  # order 5
    ([0.5, 3.0, 1.0], [1.0, 0.4, 4.0, 0.3]),  # lightly damped
]


def text(p):
    return "[" + ", ".join(repr(float(c)) for c in p) + "]"


def test_expm_against_scipy_linalg():
    linalg = pytest.importorskip("scipy.linalg")
    rng = np.random.default_rng(1)
    for scale in (0.01, 0.5, 3.0, 20.0, 80.0):
        for n in (1, 2, 5, 12):
            a = rng.normal(size=(n, n)) * scale
            ref = linalg.expm(a)
            got = lti.expm(a)
            assert np.max(np.abs(got - ref)) <= 1e-11 * max(1.0, np.max(np.abs(ref))), (n, scale)


@pytest.mark.parametrize("num, den", SYSTEMS)
@pytest.mark.parametrize("kind", ["step", "impulse"])
def test_time_response_against_scipy_signal(num, den, kind):
    signal = pytest.importorskip("scipy.signal")
    r = ok(response, numerator=text(num), denominator=text(den), input_type=kind)
    data = table(r, "response.csv")
    system = signal.lti(num, den)
    t, y = (system.step(T=data["time_s"]) if kind == "step" else system.impulse(T=data["time_s"]))
    scale = max(1.0, float(np.max(np.abs(y))))
    assert np.max(np.abs(data["output"] - y)) < 1e-7 * scale


def test_ramp_against_scipy_lsim():
    signal = pytest.importorskip("scipy.signal")
    r = ok(response, numerator="[2.0]", denominator="[1.0, 3.0, 2.0]", input_type="ramp", samples=801)
    data = table(r, "response.csv")
    _, y, _ = signal.lsim(signal.lti([2.0], [1.0, 3.0, 2.0]), U=data["time_s"], T=data["time_s"])
    assert np.max(np.abs(data["output"] - y)) < 1e-7 * max(1.0, float(np.max(np.abs(y))))


@pytest.mark.parametrize("num, den", SYSTEMS)
def test_bode_data_against_scipy_freqs(num, den):
    signal = pytest.importorskip("scipy.signal")
    r = ok(frequency, numerator=text(num), denominator=text(den), points=120)
    data = table(r, "bode.csv")
    _, h = signal.freqs(num, den, worN=data["frequency_rad_s"])
    assert np.max(np.abs(data["magnitude_db"] - 20 * np.log10(np.abs(h)))) < 1e-9
    # compare phases modulo 360 (scipy's is the principal value)
    diff = np.angle(np.exp(1j * np.radians(data["phase_deg"])) / (h / np.abs(h)))
    assert np.max(np.abs(diff)) < 1e-9


DISCRETE_CASES = [
    ("zoh", "zoh", {}),
    ("foh", "foh", {}),
    ("tustin", "bilinear", {}),
    ("forward_euler", "gbt", {"alpha": 0.0}),
    ("backward_euler", "gbt", {"alpha": 1.0}),
]


@pytest.mark.parametrize("num, den", SYSTEMS[:5])
@pytest.mark.parametrize("ours, theirs, extra", DISCRETE_CASES)
def test_discretisation_against_scipy_cont2discrete(num, den, ours, theirs, extra):
    signal = pytest.importorskip("scipy.signal")
    step = 0.05
    r = ok(discretize, numerator=text(num), denominator=text(den), sample_time=f"{step} s", method=ours)
    n_d = np.array(r.data["discrete"]["numerator"]["coefficients_descending"])
    d_d = np.array(r.data["discrete"]["denominator"]["coefficients_descending"])
    n_ref, d_ref, _ = signal.cont2discrete((num, den), step, method=theirs, **extra)
    n_ref = np.atleast_1d(np.squeeze(n_ref))
    d_ref = np.atleast_1d(d_ref)
    # compare as functions: evaluate both on the unit circle (robust to padding and to cancelled common factors)
    z = np.exp(1j * np.linspace(0.01, np.pi, 40))
    ours_h = np.polyval(n_d, z) / np.polyval(d_d, z)
    ref_h = np.polyval(n_ref, z) / np.polyval(d_ref, z)
    assert np.max(np.abs(ours_h - ref_h)) < 1e-8 * max(1.0, np.max(np.abs(ref_h))), (ours, num, den)


def test_python_control_margins_and_step_info():
    control = pytest.importorskip("control")
    sys = control.tf([1.0], [1.0, 3.0, 2.0, 0.0])
    gm, pm, wpc, wgc = control.margin(sys)
    r = ok(frequency, numerator="[1]", denominator="s (s+1)(s+2)")
    assert v(r, "gain_margin") == pytest.approx(gm, rel=1e-6)
    assert v(r, "phase_margin") == pytest.approx(pm, abs=1e-4)
    assert v(r, "phase_crossover_frequency") == pytest.approx(wpc, rel=1e-6)
    assert v(r, "gain_crossover_frequency") == pytest.approx(wgc, rel=1e-6)
    second = control.tf([16.0], [1.0, 4.0, 16.0])
    info = control.step_info(second, T=np.linspace(0, 5, 20001))
    r = ok(response, numerator="[16]", denominator="[1, 4, 16]")
    m = r.data["metrics"]
    assert m["overshoot_percent"] == pytest.approx(info["Overshoot"], abs=5e-3)
    assert m["peak_time"] == pytest.approx(info["PeakTime"], abs=2e-3)
    assert m["rise_time"] == pytest.approx(info["RiseTime"], abs=2e-3)
    assert m["settling_time"] == pytest.approx(info["SettlingTime"], abs=2e-3)


def test_python_control_discretisation_and_poles():
    control = pytest.importorskip("control")
    sys = control.tf([3.0], [1.0, 2.0])
    for ours, theirs in (("zoh", "zoh"), ("tustin", "tustin"), ("foh", "foh")):
        r = ok(discretize, numerator="3", denominator="[1, 2]", sample_time="0.1 s", method=ours)
        d = control.sample_system(sys, 0.1, method=theirs)
        num, den = np.atleast_1d(np.squeeze(d.num[0][0])), np.atleast_1d(np.squeeze(d.den[0][0]))
        z = np.exp(1j * np.linspace(0.01, 3.0, 20))
        ours_h = np.polyval(r.data["discrete"]["numerator"]["coefficients_descending"], z) / np.polyval(r.data["discrete"]["denominator"]["coefficients_descending"], z)
        assert np.max(np.abs(ours_h - np.polyval(num, z) / np.polyval(den, z))) < 1e-9
    assert sorted(control.poles(control.tf([1.0], [1.0, 3.0, 2.0]))) == pytest.approx([-2.0, -1.0])


def test_the_tools_do_not_import_scipy_or_python_control():
    import importlib
    import pathlib
    import re

    package = pathlib.Path(importlib.import_module("eng_toolkit.domains.control").__file__).parent
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(scipy|control|sympy)\b", source, re.MULTILINE), path.name
        assert not re.search(r"(?<![\w.])(eval|exec|compile)\s*\(", source), path.name  # re.compile( is fine: the dot excludes it
    assert parse_polynomial("[1]", param="p") is not None
