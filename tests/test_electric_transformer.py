"""Independent transformer arithmetic; default tolerances in electric_testing.close."""
import math

import numpy as np
import pytest

from eng_toolkit.domains.electric.transformer import transformer
from tests.electric_testing import call, close, ok, table, v

TESTS = dict(rating="20 kVA", primary_voltage="8000 V", secondary_voltage="240 V",
             open_circuit="8000 V,0.214 A,400 W", short_circuit="489 V,2.5 A,240 W")
CIRCUIT = dict(rating="20 kVA", primary_voltage="8000 V", secondary_voltage="240 V", mode="circuit",
               circuit="144 ohm,35 ohm,0.02 ohm,0.08 ohm,0.02 ohm,0.08 ohm")


def test_chapman_style_20kva_test_reduction():
    r = ok(transformer, **TESTS)
    # OC Rc=8000²/400=160000 ohm, Ic=.05 A; SC Req=240/2.5²=38.4 ohm.
    # Refer to 240 V by a²=(8000/240)². Approx loss=400+240=640 W.
    a = 8000/240
    req = 38.4/a**2
    xeq = math.sqrt((489/2.5)**2-38.4**2)/a**2
    close(v(r, "core_resistance"), 160000/a**2)
    close(v(r, "magnetizing_reactance"), 8000/math.sqrt(.214**2-.05**2)/a**2)
    close(v(r, "equivalent_resistance"), req)
    close(v(r, "equivalent_reactance"), xeq)
    close(v(r, "regulation_approximate"), (20000/240)*(req*.8+xeq*.6)/240)
    close(v(r, "efficiency_approximate"), 16000/16640)
    close(v(r, "maximum_efficiency_load_fraction"), math.sqrt(400/240))
    close(v(r, "maximum_efficiency"), math.sqrt(400/240)*16000/(math.sqrt(400/240)*16000+800))
    close(v(r, "input_power_exact"), v(r, "output_power")+v(r, "core_loss_exact")+v(r, "copper_loss_exact"))
    assert "assumed" in r.data["exact_circuit_identification"]


def test_exact_t_circuit_against_two_port_randomized():
    rng = np.random.default_rng(871)
    for _ in range(12):
        rc, xm = rng.uniform(100, 200), rng.uniform(20, 50)
        r1, r2, x1, x2 = rng.uniform(.01, .05, 4)
        pf = rng.uniform(.2, 1)
        load = rng.uniform(.1, 1.5)
        r = ok(transformer, **{**CIRCUIT, "circuit": f"{rc} ohm,{xm} ohm,{r1} ohm,{x1} ohm,{r2} ohm,{x2} ohm"}, load_fraction=float(load), power_factor=float(pf))
        # Cascaded series/shunt/series two-port: A=1+Z1Y; B=Z1+Z2+Z1YZ2.
        # Source Vs=A*240+B*I, no-load secondary Vs/A. This is independent matrix composition.
        t1 = np.array([[1, complex(r1, x1)], [0, 1]])
        tm = np.array([[1, 0], [complex(1/rc, -1/xm), 1]])
        t2 = np.array([[1, complex(r2, x2)], [0, 1]])
        matrix = t1@tm@t2
        amps = load*20000/240*complex(pf, -math.sqrt(1-pf**2))
        source, ip = matrix@np.array([240, amps])
        close(v(r, "regulation_exact"), abs(source/matrix[0, 0])/240-1)
        close(v(r, "input_power_exact"), (source*ip.conjugate()).real)


def test_reference_invariance_and_unit_conversion():
    low = ok(transformer, **TESTS)
    high = ok(transformer, **TESTS, referred_side="primary")
    close(v(high, "equivalent_resistance"), v(low, "equivalent_resistance")*(8000/240)**2)
    assert low.data["per_unit"] == high.data["per_unit"]
    converted = ok(transformer, **{**TESTS, "rating": "20000 VA", "primary_voltage": "8 kV", "secondary_voltage": ".24 kV"})
    close(v(converted, "efficiency_exact"), v(low, "efficiency_exact"))


def test_reactive_extreme_keeps_exact_real_power_balance():
    r = ok(transformer, mode="circuit", rating="100 kVA", primary_voltage="400 V", secondary_voltage="400 V",
           connection="Y-Y", circuit="1e-6 ohm,1 ohm,1e-12 ohm,1e12 ohm,.01 ohm,.01 ohm",
           power_factor_direction="leading", power_factor=.001)
    # Passive T network: real input is 100 W output plus positive branch losses,
    # even when a complex source-power product subtracts enormous reactive terms.
    close(v(r, "output_power"), 100)
    close(v(r, "input_power_exact"), 100+v(r, "core_loss_exact")+v(r, "copper_loss_exact"))
    assert 0 <= v(r, "efficiency_exact") <= 1


@pytest.mark.parametrize("connection,expected_a,shift", [
    ("Y-Y", 8000/240, 0), ("Y-Delta", 8000/(math.sqrt(3)*240), -30),
    ("Delta-Y", 8000*math.sqrt(3)/240, 30), ("Delta-Delta", 8000/240, 0),
])
def test_three_phase_line_winding_relations(connection, expected_a, shift):
    r = ok(transformer, **CIRCUIT, connection=connection)
    # Balanced three-phase S=sqrt(3)*Vline*Iline regardless of winding connection.
    close(v(r, "turns_ratio"), expected_a)
    close(v(r, "secondary_line_current_rated"), 20000/(math.sqrt(3)*240))
    close(v(r, "secondary_line_phase_shift"), shift)
    close(v(r, "input_power_exact"), v(r, "output_power")+v(r, "core_loss_exact")+v(r, "copper_loss_exact"))


def test_leading_no_load_all_day_and_autotransformer():
    lag = ok(transformer, **TESTS)
    lead = ok(transformer, **TESTS, power_factor_direction="leading")
    assert v(lead, "regulation_approximate") < 0 < v(lag, "regulation_approximate")
    r = ok(transformer, **TESTS, load_fraction=0, load_profile="8 h,1,.8;16 h,0,1", autotransformer=True)
    # 8*16=128 kWh output; 24*.4+8*.24=11.52 kWh loss.
    close(v(r, "all_day_efficiency"), 128/(128+11.52))
    close(v(r, "efficiency_exact"), 0)
    close(v(r, "autotransformer_rating"), 20000*8240/240)
    close(v(r, "autotransformer_conducted_power")+v(r, "autotransformer_transformed_power"), v(r, "autotransformer_rating"))
    assert len(table(r)) == 2


@pytest.mark.parametrize("changes", [
    {"rating": "20 kW"}, {"open_circuit": "8000 V,.01 A,400 W"},
    {"open_circuit": "8000 V,.214 A,.4 kVA"}, {"short_circuit": "489 V,2.5 A,2000 W"},
    {"primary_voltage": "0 V"}, {"rating": "1e308 VA"}, {"secondary_voltage": "1e-300 yV"},
    {"primary_voltage": "NaN V"}, {"primary_voltage": "2 A"}, {"primary_voltage": "("*6000},
    {"circuit": CIRCUIT["circuit"]}, {"load_profile": "8 h,1,.8"}, {"load_profile": ";".join(["1 h,1,1"]*49)},
])
def test_refusals(changes):
    assert not call(transformer, **{**TESTS, **changes}).ok
