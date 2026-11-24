"""Round 3 (judge M1-12): the shaft static block at the maximum fatigue load, and the gear tool's pitch diameter (M1-9)."""

import math

import pytest

from eng_toolkit.domains.machine.gear import gear
from eng_toolkit.domains.machine.shaft import shaft
from tests.machine_testing import call, close, ok, v

# the judge's inputs: d = 60 mm, C45+QT, Kt/Kts 2.14/2.62 given for both the static and the fatigue stresses
M1 = dict(outer_diameter="60 mm", ultimate_strength="630 MPa", yield_strength="370 MPa", surface_finish="machined", reliability=99,
          kt=2.14, kts=2.62, kf=2.14, kfs=2.62)
D = 0.060
DENOM = math.pi * D**3  # pi d^3 in m^3: sigma_b = 32 M/(pi d^3), tau = 16 T/(pi d^3)


def vm(m, t, kt, kts):
    return math.hypot(kt * 32 * m / DENOM, math.sqrt(3) * kts * 16 * t / DENOM) / 1e6  # MPa


def test_only_alternating_and_mean_loads_give_a_static_check_at_the_maximum_load():
    # before: bending_stress 0, torsional_shear 0, von_mises 0 and no safety factor, although 523 N*m alternating bending and 848 N*m mean torque act
    r = ok(shaft, **M1, alternating_bending="523 N*m", mean_torque="848 N*m")
    close(v(r, "bending_stress", "MPa"), 32 * 523 / DENOM / 1e6)  # nominal bending stress at the peak bending moment: 24.66 MPa
    close(v(r, "torsional_shear", "MPa"), 2.62 * 16 * 848 / DENOM / 1e6)
    close(v(r, "von_mises", "MPa"), vm(523, 848, 2.14, 2.62))
    close(v(r, "static_safety_factor"), 370 / vm(523, 848, 2.14, 2.62))
    assert 3.5 < v(r, "static_safety_factor") < 3.6
    assert "maximum of the fatigue loads" in r.data["static_load_basis"]
    assert any("No static load was given" in w for w in r.warnings)
    assert "peak_von_mises" not in r.values  # the static block IS the peak here
    # the fatigue block is untouched: 2.168 (the judge's own number)
    close(v(r, "goodman_safety_factor"), 2.1680251107291504, rel=1e-9)


def test_the_maximum_load_is_mean_plus_alternating_with_the_sign_of_the_mean_dropped():
    r = ok(shaft, **M1, alternating_bending="200 N*m", mean_bending="-300 N*m", alternating_torque="100 N*m", mean_torque="-400 N*m")
    close(v(r, "von_mises", "MPa"), vm(500, 500, 2.14, 2.62))  # |-300| + 200 and |-400| + 100


def test_nominal_check_when_the_concentration_factors_are_one():
    # the judge's reference: nominal static von Mises of this shaft is about 41.6-42.5 MPa
    r = ok(shaft, **{**M1, "kt": 1, "kts": 1}, alternating_bending="523 N*m", mean_torque="848 N*m")
    assert 41.0 < v(r, "von_mises", "MPa") < 43.0
    assert not any("includes kt" in w for w in r.warnings)


def test_explicit_static_loads_are_used_alone_and_the_peak_values_carry_the_fatigue_loads():
    # the call the assistant made: torque 848 N*m as a static load AND mean_torque 848 N*m, alternating bending 523 N*m, no static bending
    r = ok(shaft, **M1, alternating_bending="523 N*m", mean_torque="848 N*m", torque="848 N*m")
    close(v(r, "bending_stress", "MPa"), 0.0)  # static block as given: torque only ...
    close(v(r, "von_mises", "MPa"), vm(0, 848, 2.14, 2.62))
    # ... and the new peak values add the bending that the old block left out: 104.97 MPa, safety 370/104.97 = 3.52 instead of 4.08
    close(v(r, "peak_von_mises", "MPa"), vm(523, 848, 2.14, 2.62))
    close(v(r, "peak_safety_factor"), 370 / vm(523, 848, 2.14, 2.62))
    assert v(r, "peak_safety_factor") < v(r, "static_safety_factor")
    assert any("alternating and mean loads are not added" in w and "peak_safety_factor" in w for w in r.warnings)
    assert "NOT in it" in r.data["static_load_basis"]


def test_a_pure_static_call_is_unchanged_and_says_that_kt_multiplies_the_static_stress():
    r = ok(shaft, outer_diameter="20 mm", bending_moment="100 N*m", yield_strength="300 MPa", kt=2.0)
    close(v(r, "static_safety_factor"), 300 / (2.0 * 32 * 100 / (math.pi * 0.02**3) / 1e6))
    assert r.data["static_load_basis"] == "bending_moment, torque and axial_force as given"
    assert any("kt = 2" in w and "kt = 1" in w for w in r.warnings)
    assert "peak_von_mises" not in r.values


def test_only_a_fatigue_load_of_zero_still_asks_for_a_load():
    assert not call(shaft, **M1, alternating_bending="0 N*m").ok


# --------------------------------------------------------------------------------------------------- gear (M1-9)
def test_machine_gear_is_spur_only_with_the_transverse_module_so_d_is_module_times_teeth():
    # the 284 mm vs 294 mm of the judge came from the assistant's own arithmetic: the tool has no helix_angle and returns d = m z.
    r = ok(gear, teeth_1=20, teeth_2=71, face_width="50 mm", module="4 mm", torque="100 N*m")
    close(v(r, "pitch_diameter_2", "mm"), 284.0)
    # a helical wheel of normal module 4 mm, 15 degrees has d = 71 * 4 / cos(15 deg) = 294.0 mm: the transverse module 4/cos(15) gives it
    transverse = 4.0 / math.cos(math.radians(15.0))
    h = ok(gear, teeth_1=20, teeth_2=71, face_width="50 mm", module=f"{transverse!r} mm", torque="100 N*m")
    close(v(h, "pitch_diameter_2", "mm"), 71 * 4 / math.cos(math.radians(15.0)))
    assert 293.9 < v(h, "pitch_diameter_2", "mm") < 294.1


@pytest.mark.asyncio
async def test_the_gear_description_says_spur_only_and_how_to_use_a_helical_module():
    from fastmcp import Client

    from eng_toolkit.gateway.server import gateway

    async with Client(gateway) as client:
        tools = {t.name: t.description for t in await client.list_tools()}
    assert "Spur only" in tools["machine_gear"] and "transverse module" in tools["machine_gear"] and len(tools["machine_gear"]) < 650
    assert "|mean|+alternating" in tools["machine_shaft"] and len(tools["machine_shaft"]) < 650
