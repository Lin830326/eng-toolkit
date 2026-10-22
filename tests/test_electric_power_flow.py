"""Independent three-phase phasor, sequence and fault-network checks."""
import math

import numpy as np
import pytest

from eng_toolkit.domains.electric.power_flow import power_flow
from tests.electric_testing import call, close, ok, v


def z(item):
    return complex(item["real"], item["imag"])


def test_balanced_load_correction_and_phase_values():
    r = ok(power_flow, line_voltage="400 V", real_power="12 kW", power_factor=.8, target_power_factor=1)
    # S=12/.8=15 kVA, Q=sqrt(15²-12²)=9 kvar, I=15000/(sqrt(3)*400).
    close(v(r, "apparent_power"), 15000)
    close(v(r, "reactive_power"), 9000)
    close(v(r, "line_current"), 15000/(math.sqrt(3)*400))
    close(v(r, "correction_reactive_power"), 9000)
    close(v(r, "capacitance_per_branch"), 9000/(3*2*math.pi*50*400**2))
    close(v(r, "resistive_loss_reduction_fraction"), 1-.8**2)
    d = ok(power_flow, line_voltage=".4 kV", real_power="12000 W", power_factor=.8, connection="delta")
    close(v(d, "phase_voltage"), 400)
    close(v(d, "phase_current"), v(r, "line_current")/math.sqrt(3))


def test_phasors_and_power_pq_agree():
    # 10 A at -acos(.8) relative to the phase voltage yields 8-j6 A.
    r = ok(power_flow, line_voltage="400 V", source="phasors", line_current="10 A", current_angle=f"{-math.acos(.8)} rad")
    p, reactive = math.sqrt(3)*400*8, math.sqrt(3)*400*6
    close(v(r, "real_power"), p)
    other = ok(power_flow, line_voltage="400 V", source="power_pq", real_power=f"{p} W", reactive_power=f"{reactive} var")
    close(v(other, "line_current"), 10)
    close(v(other, "power_factor"), .8)


def test_feeder_phasors_and_power_balance():
    r = ok(power_flow, mode="feeder", line_voltage="400 V", real_power="12 kW", power_factor=.8,
           feeder_resistance="2 ohm/km", feeder_reactance="1 ohm/km", length="100 m", target_power_factor=1)
    # Per conductor Z=.2+j.1; I=(12000-j9000)/(sqrt(3)*400).
    i = complex(12000, -9000)/(math.sqrt(3)*400)
    vs = 400/math.sqrt(3)+i*complex(.2, .1)
    close(v(r, "voltage_drop_exact"), math.sqrt(3)*abs(vs)-400)
    close(v(r, "voltage_drop_approximate"), math.sqrt(3)*(i*complex(.2, .1)).real)
    close(v(r, "sending_real_power"), 12000+3*abs(i)**2*.2)
    close(v(r, "sending_real_power"), 3*(vs*i.conjugate()).real)


def test_per_unit_rebase_invariance():
    r = ok(power_flow, mode="per_unit", impedance="1 ohm,2 ohm", base_voltage="10 kV", base_power="10 MVA",
           new_base_voltage="20 kV", new_base_power="50 MVA")
    # Zbase_old=10 ohm, Zbase_new=8 ohm; physical impedance remains 1+j2 ohm.
    close(v(r, "old_base_impedance"), 10)
    close(v(r, "new_base_impedance"), 8)
    assert z(r.data["new_per_unit"]) == pytest.approx(complex(.125, .25))
    r2 = ok(power_flow, mode="per_unit", impedance=".1 dimensionless,.2 dimensionless", impedance_basis="per_unit",
            base_voltage="10 kV", base_power="10 MVA", new_base_voltage="20 kV", new_base_power="50 MVA")
    assert z(r2.data["impedance_ohm"]) == pytest.approx(complex(1, 2))


def test_known_sequence_and_round_trip_randomized():
    # [Va,Vb,Vc]=[3,0,0] has V0=V1=V2=1 by each Fortescue matrix row.
    r = ok(power_flow, mode="components", phasors="3 V,0 V;0 V,0 V;0 V,0 V")
    for item in r.data["sequence"].values():
        assert z(item) == pytest.approx(1)
    rng = np.random.default_rng(113)
    for _ in range(10):
        phase = rng.normal(size=3)+1j*rng.normal(size=3)
        raw = ";".join(f"{x.real} V,{x.imag} V" for x in phase)
        one = ok(power_flow, mode="components", phasors=raw)
        seq = [z(one.data["sequence"][str(i)]) for i in range(3)]
        # A direct DFT is independent of the tool's hand-written transform.
        expected = np.fft.ifft(phase)
        assert seq == pytest.approx(expected)
        raw_seq = ";".join(f"{x.real} V,{x.imag} V" for x in seq)
        back = ok(power_flow, mode="components", direction="sequence_to_phase", phasors=raw_seq)
        assert [z(back.data["phase"][key]) for key in "abc"] == pytest.approx(phase)


@pytest.mark.parametrize("fault", ["three_phase", "line_ground", "line_line", "double_line_ground"])
def test_fault_against_independent_phase_domain_nodal_solve(fault):
    # Choose prefault phase voltage 100 V and Z012=j*[3,1,1].
    # SLG Ia=3*100/(j5)=-j60 A; three-phase |Ia|=100 A.
    kwargs = dict(mode="fault", fault_type=fault, line_voltage=f"{100*math.sqrt(3)} V", positive_impedance="0 ohm,1 ohm")
    if fault != "three_phase":
        kwargs["negative_impedance"] = "0 ohm,1 ohm"
    if fault in ("line_ground", "double_line_ground"):
        kwargs["zero_impedance"] = "0 ohm,3 ohm"
    r = ok(power_flow, **kwargs)
    a = np.exp(2j*np.pi/3)
    transform = np.array([[1, 1, 1], [1, a*a, a], [1, a, a*a]])
    zabc = transform@np.diag([3j, 1j, 1j])@np.linalg.inv(transform)
    prefault = np.array([100, 100*a*a, 100*a])
    # Solve phase-network KVL with physical boundary conditions directly.
    if fault == "three_phase":
        currents = np.linalg.solve(zabc, prefault)
    elif fault == "line_ground":
        currents = np.array([prefault[0]/zabc[0, 0], 0, 0])
        close(v(r, "fault_current"), 60)
    elif fault == "line_line":
        ib = (prefault[1]-prefault[2])/(zabc[1, 1]-zabc[1, 2]-zabc[2, 1]+zabc[2, 2])
        currents = np.array([0, ib, -ib])
    else:
        currents = np.r_[0, np.linalg.solve(zabc[1:, 1:], prefault[1:])]
    actual = [z(r.data["fault_phase_currents"][key]) for key in "abc"]
    assert actual == pytest.approx(currents, abs=1e-10)
    close(v(r, "asymmetrical_peak_factor"), 2*math.sqrt(2))


@pytest.mark.parametrize("fault", ["line_ground", "line_line", "double_line_ground"])
def test_resistive_fault_boundary_conditions(fault):
    args = dict(mode="fault", fault_type=fault, line_voltage="400 V", positive_impedance=".1 ohm,1 ohm", negative_impedance=".2 ohm,.8 ohm", fault_impedance=".3 ohm,.1 ohm")
    if fault != "line_line":
        args["zero_impedance"] = ".5 ohm,2 ohm"
    r = ok(power_flow, **args)
    i = [z(r.data["fault_phase_currents"][key]) for key in "abc"]
    volts = [z(r.data["fault_phase_voltages"][key]) for key in "abc"]
    if fault == "line_ground":
        assert volts[0] == pytest.approx(complex(.3, .1)*i[0])
        assert i[1:] == pytest.approx([0, 0], abs=1e-10)
    elif fault == "line_line":
        assert volts[1]-volts[2] == pytest.approx(complex(.3, .1)*i[1])
        assert i[1]+i[2] == pytest.approx(0, abs=1e-10)
    else:
        assert volts[1] == pytest.approx(volts[2])
        assert volts[1] == pytest.approx(complex(.3, .1)*(i[1]+i[2]))


@pytest.mark.parametrize("args", [
    {"line_voltage": "400 V", "real_power": "12 kVA"},
    {"line_voltage": "400 V", "source": "power_pq", "real_power": "12 kW", "reactive_power": "9 kW"},
    {"line_voltage": "1e308 V", "real_power": "1 W"}, {"line_voltage": "1e-300 yV", "real_power": "1 W"},
    {"line_voltage": "-400 V", "real_power": "1 W"}, {"line_voltage": "NaN V", "real_power": "1 W"},
    {"line_voltage": "1 kg", "real_power": "1 W"}, {"line_voltage": "400 V", "real_power": "1 W", "line_current": "1 A"},
    {"mode": "components", "phasors": "("*6000}, {"mode": "components", "phasors": "1 V,0 V;2 V,0 V"},
    {"mode": "fault", "line_voltage": "400 V", "positive_impedance": "0 ohm,0 ohm"},
    {"line_voltage": "400 V", "real_power": "1 W", "power_factor_direction": "leading", "target_power_factor": 1},
])
def test_refusals(args):
    assert not call(power_flow, **args).ok
