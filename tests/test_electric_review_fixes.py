"""Regression tests for the independent-review findings (kappa basis, transformer
maximum-efficiency range, PMSM light-load field weakening, Hz in EMF constants)."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.electric.pm_dc_motor import _envelope, pm_dc_motor
from eng_toolkit.domains.electric.power_flow import power_flow
from eng_toolkit.domains.electric.transformer import transformer
from tests.electric_testing import call, close, ok, v

# --- 1. IEC kappa uses the bolted positive-sequence R1/X1 -------------------

LG = dict(mode="fault", fault_type="line_ground", line_voltage="11 kV", positive_impedance="0.1 ohm,1 ohm",
          negative_impedance="0.1 ohm,1 ohm", zero_impedance="1 ohm,3 ohm")


def test_iec_kappa_line_ground_uses_positive_sequence_ratio():
    r = ok(power_flow, **LG, peak_method="iec_kappa")
    # E=11000/sqrt(3); loop Z1+Z2+Z0=1.2+j5 ohm; Ia=3E/|Z|=3705.293 A.
    ia = 3*11000/math.sqrt(3)/math.hypot(1.2, 5)
    close(v(r, "fault_current"), ia)
    # IEC 60909-0: R1/X1=0.1, kappa=1.02+0.98 exp(-0.3)=1.7460019 (old loop basis 1.2/5 gave 1.497).
    kappa = 1.02+.98*math.exp(-.3)
    close(kappa, 1.7460018563, rel=1e-10)
    close(v(r, "kappa"), kappa)
    close(v(r, "kappa_r_over_x"), .1)
    close(v(r, "asymmetrical_peak_factor"), math.sqrt(2)*kappa)
    # ip=sqrt(2)*1.7460*3705.29=9149.18 A (old value 7844 A).
    close(v(r, "asymmetrical_peak_current"), math.sqrt(2)*kappa*ia)
    close(v(r, "asymmetrical_peak_current"), 9149.18, rel=1e-6)
    # The reported X/R stays the fault-loop ratio 5/1.2 and is labelled as such.
    close(v(r, "x_over_r"), 5/1.2)
    assert "fault-loop" in r.data["x_over_r_basis"]


@pytest.mark.parametrize("fault", ["three_phase", "line_line", "double_line_ground"])
def test_iec_kappa_ignores_fault_resistance_and_other_sequences(fault):
    args = dict(mode="fault", fault_type=fault, line_voltage="400 V", positive_impedance="0.2 ohm,1 ohm",
                fault_impedance="0.5 ohm,0 ohm", peak_method="iec_kappa")
    if fault != "three_phase":
        args["negative_impedance"] = "0.3 ohm,0.9 ohm"
    if fault == "double_line_ground":
        args["zero_impedance"] = "2 ohm,4 ohm"
    r = ok(power_flow, **args)
    # Only R1/X1=0.2 enters: kappa=1.02+0.98 exp(-0.6).
    close(v(r, "kappa"), 1.02+.98*math.exp(-.6))
    close(v(r, "asymmetrical_peak_current"), math.sqrt(2)*(1.02+.98*math.exp(-.6))*v(r, "fault_current"))


def test_half_cycle_keeps_approximate_loop_time_constant():
    r = ok(power_flow, **LG)
    # Loop R/X=1.2/5: sqrt(2)(1+exp(-pi*0.24)), labelled approximate.
    close(v(r, "asymmetrical_peak_factor"), math.sqrt(2)*(1+math.exp(-math.pi*.24)))
    assert "kappa" not in r.values
    assert r.data["peak_factor_basis"].startswith("approximate")


def test_iec_kappa_resistive_positive_sequence_uses_limit():
    r = ok(power_flow, mode="fault", line_voltage="400 V", positive_impedance="1 ohm,0 ohm", peak_method="iec_kappa")
    # R1/X1 unbounded: kappa tends to 1.02.
    close(v(r, "kappa"), 1.02)
    assert "kappa_r_over_x" not in r.values


# --- 2. Transformer maximum efficiency outside the 0..2 current range --------

TX = dict(rating="20 kVA", primary_voltage="8000 V", secondary_voltage="240 V",
          open_circuit="8000 V,0.214 A,400 W", short_circuit="100 V,2.5 A,10 W")


def test_transformer_maximum_efficiency_beyond_range_is_omitted():
    r = ok(transformer, **TX)
    # Pcore=8000²/400 ohm referred: 240²/144=400 W. Req=10/2.5²=1.6 ohm primary,
    # 1.6/(8000/240)²=1.44e-3 ohm secondary; Pcu_fl=(20000/240)²*1.44e-3=10 W.
    close(v(r, "core_loss_approximate"), 400)
    close(v(r, "full_load_copper_loss"), 10)
    # xmax=sqrt(400/10)=6.32 > 2: the old tool returned it and eta 0.99216 as values.
    assert "maximum_efficiency_load_fraction" not in r.values
    assert "maximum_efficiency" not in r.values
    assert r.data["maximum_efficiency_outside_validity_range"] is True
    xmax = math.sqrt(40)
    close(r.data["unconstrained_maximum_efficiency_load_fraction"], xmax)
    close(r.data["unconstrained_maximum_efficiency"], xmax*16000/(xmax*16000+800))
    close(r.data["load_fraction_validity_limit"], 2)
    # Range-limited maximum at x=2, pf .8: 32000/(32000+400+4*10)=32000/32440.
    close(v(r, "efficiency_at_load_fraction_limit"), 32000/32440)
    assert any("exceeds the 0..2 validity range" in w for w in r.warnings)


def test_transformer_maximum_efficiency_inside_range_is_reported():
    r = ok(transformer, **{**TX, "short_circuit": "489 V,2.5 A,240 W"})
    # xmax=sqrt(400/240)=1.29 <= 2 keeps the stationary maximum in values.
    close(v(r, "maximum_efficiency_load_fraction"), math.sqrt(400/240))
    assert r.data["maximum_efficiency_outside_validity_range"] is False
    assert "efficiency_at_load_fraction_limit" not in r.values


# --- 3. PMSM light-load field weakening near the first voltage contact -------

def _slsqp_minimum_current(c):
    """Independent two-variable SLSQP: min id²+iq² with exact torque and voltage limit."""
    optimize = pytest.importorskip("scipy.optimize")
    k = 1.5*c["p"]
    torque, imax = c["torque"], c["imax"]
    q_scale = torque/(k*c["psi"])
    delta = c["ld"]-c["lq"]

    def unpack(x):
        return x[0]*imax, x[1]*q_scale

    def torque_residual(x):
        d, qq = unpack(x)
        return k*(c["psi"]+delta*d)*qq/torque-1

    def voltage_margin(x):
        d, qq = unpack(x)
        vd = c["r"]*d-c["we"]*c["lq"]*qq
        vq = c["r"]*qq+c["we"]*(c["ld"]*d+c["psi"])
        return 1-(vd*vd+vq*vq)/c["vmax"]**2

    best = None
    for start in (-1.0, -0.1, 0.0):
        s = c["psi"]+delta*start*imax
        if s <= 0:
            continue
        answer = optimize.minimize(lambda x: x[0]**2+(x[1]*q_scale/imax)**2, [start, c["psi"]/s],
                                   method="SLSQP", options={"ftol": 1e-16, "maxiter": 500},
                                   constraints=[{"type": "eq", "fun": torque_residual},
                                                {"type": "ineq", "fun": voltage_margin}])
        if abs(torque_residual(answer.x)) < 1e-9 and voltage_margin(answer.x) > -1e-9:
            d, qq = unpack(answer.x)
            if best is None or math.hypot(d, qq) < best[0]:
                best = (math.hypot(d, qq), d, qq)
    return best


def _pmsm_call(c):
    return call(pm_dc_motor, mode="pmsm", resistance=f"{c['r']!r} ohm", d_inductance=f"{c['ld']!r} H",
                q_inductance=f"{c['lq']!r} H", flux_linkage=f"{c['psi']!r} Wb", pole_pairs=c["p"],
                current_limit=f"{c['imax']!r} A", voltage=f"{c['vdc']!r} V", speed=f"{c['speed']!r} rad/s",
                torque=f"{c['torque']!r} N*m", curve_points=2, speed_points=f"{c['speed']!r} rad/s")


def _check_against_reference(c):
    r = _pmsm_call(c)
    reference = _slsqp_minimum_current(c)
    # Every request is below the envelope torque, so it is feasible.
    assert reference is not None
    assert r.ok, r.error
    close(v(r, "electromagnetic_torque"), c["torque"], rel=1e-9, abs=0)
    close(v(r, "phase_peak_current"), reference[0], rel=1e-6, abs=0)
    assert v(r, "phase_peak_voltage") <= c["vmax"]*(1+1e-8)
    assert v(r, "phase_peak_current") <= c["imax"]*(1+1e-8)
    return r


def test_pmsm_light_load_field_weakening_failing_input():
    vdc = 253.53533886510996
    c = dict(r=0.01131513371911644, ld=4.492071844039667e-3, lq=1.6852448785358238e-3, psi=0.004360015056169262,
             p=3, imax=88.05876191886624, vdc=vdc, vmax=vdc/math.sqrt(3), speed=18828.730849999163,
             torque=1.4651940628517658e-06)
    c["we"] = c["p"]*c["speed"]
    # Old 48-step current bisection refused with electric.nonconvergence.
    r = _check_against_reference(c)
    # SLSQP minimum-current point: id=-0.3937178 A, iq=1.00033e-4 A, |i|=0.3937178 A.
    close(v(r, "d_current"), -0.3937178, rel=1e-6)
    close(v(r, "q_current"), 1.00033e-4, rel=1e-5)
    # iq follows exactly from the torque: T/(1.5 p (psi+(Ld-Lq) id)).
    close(v(r, "q_current"), c["torque"]/(4.5*(c["psi"]+(c["ld"]-c["lq"])*v(r, "d_current"))))
    # The point lies on the voltage boundary (MTPA would violate it).
    close(v(r, "phase_peak_voltage"), c["vmax"], rel=1e-9)


def test_pmsm_randomized_light_load_field_weakening_matches_slsqp():
    pytest.importorskip("scipy.optimize")
    rng = np.random.default_rng(20261022)
    for _ in range(200):
        while True:
            p = int(rng.integers(1, 9))
            psi = 10**rng.uniform(-3, -.5)
            ld = 10**rng.uniform(-4, -2)
            lq = ld*10**rng.uniform(-.6, .6)  # both saliency signs
            r = 10**rng.uniform(-3, 0)
            imax = 10**rng.uniform(.5, 2.5)
            vdc = 10**rng.uniform(1.5, 3)
            vmax = vdc/math.sqrt(3)
            # Open-circuit EMF p*omega*psi exceeds Vmax: light load needs negative id.
            speed = vmax*rng.uniform(1.05, 3)/(p*psi)
            envelope = _envelope(speed, r, ld, lq, psi, p, imax, vmax)
            if envelope is not None and envelope[0] > 0:
                break
        torque = envelope[0]*10**rng.uniform(-8, -4)
        _check_against_reference(dict(r=r, ld=ld, lq=lq, psi=psi, p=p, imax=imax, vdc=vdc, vmax=vmax,
                                      speed=speed, we=p*speed, torque=torque))


# --- 4. Hz in DC/BLDC constants; ke=kt tolerance -----------------------------

DC = dict(voltage="24 V", resistance="2 ohm", torque_constant="0.1 N*m/A")


@pytest.mark.parametrize(("param", "raw"), [
    ("back_emf_constant", "0.1 V/Hz"), ("back_emf_constant", "0.1 V/kHz"), ("back_emf_constant", "100 mV/Hz"),
    ("back_emf_constant", "0.1 V/hertz"), ("torque_constant", "0.1 V/Hz"),
])
def test_cyclic_rate_units_refused_for_speed_constants(param, raw):
    # 0.1 V/Hz is 0.1 V per rev/s = 0.0159 V*s/rad, but pint reads Hz as 1/s (2*pi off).
    r = call(pm_dc_motor, **{**DC, param: raw})
    assert not r.ok
    assert r.error.family == "units.dimension_mismatch"
    assert r.error.details["param"] == "/"+param
    assert "V*s/rad" in r.error.message


@pytest.mark.parametrize("raw", ["0.1 V*s/rad", f"{0.2*math.pi!r} V*s/revolution", f"{0.1*2*math.pi/60!r} V/rpm"])
def test_angular_spellings_still_accepted(raw):
    r = ok(pm_dc_motor, **DC, back_emf_constant=raw, torque="0.6 N*m")
    close(v(r, "speed"), 120)


def test_rounded_datasheet_back_emf_within_tolerance_warns():
    # 5.236 V/krpm = 5.236/(1000*2pi/60) = 0.0500001 V*s/rad: 2.3e-6 from kt=0.05.
    ke = 5.236/(1000*2*math.pi/60)
    assert 1e-9 < abs(ke-.05)/.05 < 1e-3
    r = ok(pm_dc_motor, voltage="24 V", resistance="2 ohm", torque_constant="0.05 N*m/A", back_emf_constant="5.236 V/krpm")
    # kt is used for both: I=12/2=6 A, omega=(24-12)/0.05=240 rad/s.
    close(v(r, "current"), 6)
    close(v(r, "speed"), 240)
    assert any("back_emf_constant differs" in w for w in r.warnings)


def test_back_emf_beyond_tolerance_refused():
    # 0.1002 vs 0.1 is 2e-3 relative, beyond the stated 1e-3.
    r = call(pm_dc_motor, **DC, back_emf_constant="0.1002 V*s/rad")
    assert not r.ok
    assert r.error.details["param"] == "/back_emf_constant"
