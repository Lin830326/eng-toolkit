"""Round 3 of the user-acceptance run (judges M2-3, M3-1): rainflow reports the history extremes, flags a dominant cycle and isolated single-sample
spikes; the stress-life safety factor says that it assumes proportional loading and is not valid for a preloaded bolt."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.rainflow import rainflow
from eng_toolkit.domains.fatigue.sn import sn
from tests.fatigue_testing import call, close, ok, table, v

DAMAGE = dict(sample_scale="1 MPa", basquin_a="1000 MPa", basquin_b=-0.1)  # N = (S/1000)^-10: 100 MPa gives 1e10 cycles, 750 MPa 18 cycles


def noisy(n=300, seed=1, amplitude=100.0, noise=5.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return amplitude * np.sin(2 * np.pi * t / 25.0) + rng.normal(0.0, noise, n)


def as_json(values):
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


# --------------------------------------------------------------------------------------------------- extremes
def test_history_min_max_range_are_reported_in_data_and_values():
    r = ok(rainflow, history="[0,100,-60,0]", sample_scale="1 MPa")
    assert (r.data["history_min"], r.data["history_max"], r.data["history_range"]) == (-60.0, 100.0, 160.0)
    assert r.data["history_unit"] == "MPa"
    assert r.data["history_extremes"]["max"] == {"value": 100.0, "index": 1} and r.data["history_extremes"]["min"]["index"] == 2
    close(v(r, "history_max", "MPa"), 100.0)
    close(v(r, "history_min", "MPa"), -60.0)
    k = ok(rainflow, history="[0,1,-1,0]", history_kind="force", sample_scale="1 kN")
    assert k.data["history_max"] == 1000.0 and k.data["history_unit"] == "N"  # scaled to the base unit


def test_extremes_are_those_of_the_history_as_given_not_of_the_rotated_closed_one():
    r = ok(rainflow, history="[0,5,-8,3,0]", sample_scale="1 MPa", closed=True)
    assert r.data["history_extremes"]["min"]["index"] == 2 and r.data["history_extremes"]["max"]["index"] == 1


def test_extremes_carry_the_time_of_a_two_column_file(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("time_s,strain\n0.0,0\n0.5,10\n1.0,-20\n1.5,5\n2.0,0\n", encoding="utf-8")
    r = ok(rainflow, history_file=str(p), sample_scale="1 MPa")
    assert r.data["history_extremes"]["max"] == {"value": 10.0, "index": 1, "time": 0.5}
    assert r.data["history_extremes"]["min"]["time"] == 1.0 and r.data["history_extremes"]["time_column"] == "time_s"


# --------------------------------------------------------------------------------------------------- spikes
def test_one_sample_spike_is_found_by_index_and_time(tmp_path):
    x = noisy()
    x[120] += 1500.0  # one glitch sample on a +-100 record
    p = tmp_path / "logger.csv"
    p.write_text("time_s,load\n" + "".join(f"{i * 0.01!r},{float(val)!r}\n" for i, val in enumerate(x)), encoding="utf-8")
    r = ok(rainflow, history_file=str(p), **DAMAGE)
    spikes = r.data["spikes"]
    assert spikes["count"] == 1 and spikes["threshold_sigmas"] == 6.0
    (s,) = spikes["listed"]
    assert s["index"] == 120 and s["time"] == pytest.approx(1.2) and s["value"] == pytest.approx(float(x[120]))
    assert s["sigmas"] > 6
    warning = next(w for w in r.warnings if "spike" in w)
    assert "index 120" in warning and "time_s = 1.2" in warning
    assert r.data["history_max"] == pytest.approx(float(x[120]))  # the spike is also the maximum: the pair the judge asked for
    # the same record without the spike: nothing flagged
    clean = ok(rainflow, history=as_json(noisy()), **DAMAGE)
    assert clean.data["spikes"]["count"] == 0 and not any("spike" in w for w in clean.warnings)


def test_a_spike_dominates_the_damage_and_is_named_as_the_dominant_cycle():
    x = noisy()
    x[120] += 1500.0
    r = ok(rainflow, history=as_json(x), **DAMAGE)
    d = r.data["dominant_cycle"]
    rows = table(r)
    one = lambda row: float(row["damage_per_block"]) / (float(row["full_cycles"]) + float(row["half_cycles"]))  # damage of one occurrence
    worst = max(range(len(rows)), key=lambda i: one(rows[i]))
    assert d["index"] == worst and d["index_base"] == 0
    assert d["range"] == pytest.approx(float(rows[worst]["range_MPa"])) and d["mean"] == pytest.approx(float(rows[worst]["mean_MPa"]))
    total = sum(float(row["damage_per_block"]) for row in rows)
    assert d["damage_share"] == pytest.approx(one(rows[worst]) / total)
    # the glitch is one rising and one falling flank of about 1500 MPa each: together 99.9 % of the damage, the larger flank a little over half
    assert d["damage_share"] > 0.5 and d["range"] > 1400
    assert sum(one(row) for row in rows if float(row["range_MPa"]) > 1400) / total > 0.999
    assert any("One cycle carries" in w and "%" in w for w in r.warnings)
    # without the spike, the same S-N curve spreads the damage over many cycles: no warning, the share is reported anyway
    c = ok(rainflow, history=as_json(noisy()), **DAMAGE)
    assert c.data["dominant_cycle"]["damage_share"] < 0.5 and not any("One cycle carries" in w for w in c.warnings)


def test_spikes_are_refused_by_the_yield_check_when_yield_is_given():
    # the existing refusal stays: a spike above yield is not an elastic stress
    x = noisy()
    x[120] += 1500.0
    r = call(rainflow, history=as_json(x), yield_strength="355 MPa", **DAMAGE)
    assert not r.ok and "peak stress exceeds yield" in r.error.message


def test_at_most_five_spikes_are_listed_the_largest_first():
    x = noisy(n=400)
    for k, i in enumerate((40, 80, 120, 160, 200, 240, 280, 320)):
        x[i] += 600.0 + 100.0 * k
    r = ok(rainflow, history=as_json(x), sample_scale="1 MPa")
    s = r.data["spikes"]
    assert s["count"] == 8 and len(s["listed"]) == 5
    assert [e["index"] for e in s["listed"]] == [320, 280, 240, 200, 160]
    assert "largest ones listed" in next(w for w in r.warnings if "spike" in w)


def test_real_peaks_and_held_steps_are_not_spikes():
    t = np.arange(400)
    triangle = 100.0 * (2.0 * np.abs((t / 20.0) % 2 - 1) - 1)  # sharp turning points, but neighbours follow
    assert ok(rainflow, history=as_json(triangle), sample_scale="1 MPa").data["spikes"]["count"] == 0
    square = np.where((t // 20) % 2 == 0, 100.0, -100.0)  # steps that are held: more than one sample high
    assert ok(rainflow, history=as_json(square), sample_scale="1 MPa").data["spikes"]["count"] == 0
    short = ok(rainflow, history="[0,100,-100,0]", sample_scale="1 MPa")  # below 5 samples nothing is judged
    assert short.data["spikes"]["count"] == 0


def test_a_negative_spike_is_found_too_and_the_neighbours_are_returned():
    x = noisy()
    x[77] -= 1200.0
    r = ok(rainflow, history=as_json(x), sample_scale="1 MPa")
    (s,) = r.data["spikes"]["listed"]
    assert s["index"] == 77 and s["previous"] == pytest.approx(float(x[76])) and s["next"] == pytest.approx(float(x[78]))


def test_a_flat_record_with_one_blip_is_a_spike_and_not_division_by_zero():
    x = np.zeros(100)
    x[50] = 10.0
    r = ok(rainflow, history=as_json(x), sample_scale="1 MPa")
    assert r.data["spikes"]["count"] == 1 and r.data["spikes"]["listed"][0]["index"] == 50


# --------------------------------------------------------------------------------------------------- dominant cycle
def test_identical_cycles_have_no_dominance_warning():
    r = ok(rainflow, history=as_json([0, 100, -100] * 20 + [0]), closed=False, **DAMAGE)
    assert not any("One cycle carries" in w for w in r.warnings)
    d = r.data["dominant_cycle"]
    # all the damage is in one class of 39 identical cycles: one of them carries 1/39 of it, which is what the warning is about
    assert d["occurrences"] == 39 and d["damage_share"] == pytest.approx(d["damage_share_of_its_class"] / 39) and d["damage_share_of_its_class"] > 0.999


def test_one_big_cycle_among_small_ones_is_flagged_with_its_range_and_share():
    small = [0, 50, -50] * 40
    r = ok(rainflow, history=as_json(small + [0, 700, -700, 0]), **DAMAGE)
    d = r.data["dominant_cycle"]
    assert d["range"] == pytest.approx(1400.0) and d["damage_share"] > 0.99 and d["occurrences"] == 1
    assert any("One cycle carries" in w and "1400" in w for w in r.warnings)


# --------------------------------------------------------------------------------------------------- fatigue_sn: proportional loading
BOLT = dict(mode="correction", alternating_stress="3.08 MPa", mean_stress="447 MPa", mean_method="goodman", endurance_limit="129 MPa", ultimate_strength="800 MPa")


def test_correction_mode_warns_that_a_preloaded_bolt_is_not_a_proportional_case():
    r = ok(sn, **BOLT)
    # the judge's input (M3-1): n = 1/(Sa/Se + Sm/Su) = 1/(3.08/129 + 447/800) = 1.716, but a bolt with a fixed preload has n of about 12
    close(v(r, "safety_factor"), 1.0 / (3.08 / 129 + 447 / 800), rel=1e-9)
    warning = next(w for w in r.warnings if "proportional loading" in w)
    assert "NOT valid" in warning and "preloaded bolt" in warning and "constant preload" in warning
    assert "145 times the amplitude" in warning  # 447/3.08
    assumptions = r.data["assumptions"]
    assert "proportional loading" in assumptions and "NOT valid" in assumptions and "bolt" in assumptions and "mean stays fixed" in assumptions


def test_the_warning_appears_in_correction_mode_even_with_zero_mean_and_in_curve_mode_with_a_mean_only():
    zero = ok(sn, mode="correction", alternating_stress="100 MPa", endurance_limit="200 MPa")
    assert any("proportional loading" in w for w in zero.warnings)
    assert not any("times the amplitude" in w for w in zero.warnings)
    curve = dict(mode="curve", basquin_a="800 MPa", basquin_b=math.log10(0.5) / 3, endurance_limit="200 MPa", ultimate_strength="500 MPa", mean_method="goodman")
    with_mean = ok(sn, alternating_stress="100 MPa", mean_stress="50 MPa", **curve)
    assert any("proportional loading" in w for w in with_mean.warnings)
    no_mean = ok(sn, alternating_stress="100 MPa", mean_stress="0 MPa", **curve)
    assert not any("proportional loading" in w for w in no_mean.warnings)


@pytest.mark.asyncio
async def test_description_says_proportional_and_not_for_bolts():
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        description = {t.name: t.description for t in await client.list_tools()}["fatigue_sn"]
    assert "PROPORTIONAL" in description and "bolts" in description and len(description) < 650


@pytest.mark.parametrize(
    "extra,param",
    [({"cycles": "1.46e6 cycle"}, "cycles"), ({"points": "400 MPa,1000;200 MPa,1000000"}, "points"), ({"basquin_a": "1000 MPa"}, "basquin_a"), ({"knee_cycles": "1e6 cycle"}, "knee_cycles")],
)
def test_correction_mode_refusal_names_the_offending_argument(extra, param):
    r = call(sn, mode="correction", alternating_stress="100 MPa", endurance_limit="200 MPa", **extra)
    assert not r.ok and r.error.family == "input.invalid" and r.error.details["param"] == "/" + param
    assert param in r.error.message
