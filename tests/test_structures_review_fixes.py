"""Exact review reproductions and independent numerical references."""

from decimal import Decimal, localcontext
import math

import numpy as np
import pytest
from fastmcp import Client

from eng_toolkit.domains.structures.column import column
from eng_toolkit.domains.structures.frame import frame
from eng_toolkit.domains.structures.modal import modal
from eng_toolkit.domains.structures.section import section
from eng_toolkit.domains.structures.stress import stress
from eng_toolkit.domains.structures.truss import truss
from eng_toolkit.gateway.server import gateway
from tests.structures_testing import COLUMN, FRAME, TRUSS, call, ok, quantity, v


STRESS_3D = dict(state="three_dimensional", sigma_x="0 Pa", sigma_y="0 Pa",
                 sigma_z="0 Pa", tau_xy="0 Pa", tau_yz="0 Pa", tau_zx="0 Pa")
RESTRAINED_TRUSS = {**TRUSS, "supports": "a,xy;b,xy", "loads": ""}


@pytest.mark.parametrize("unit", ["celsius", "degree_Celsius", "degree_Fahrenheit", "fahrenheit", "degC", "degF"])
@pytest.mark.parametrize("location", ["truss_default", "truss_member", "frame_mean", "frame_gradient"])
def test_offset_temperature_aliases_refused(unit, location):
    if location == "truss_default":
        result = call(truss, **RESTRAINED_TRUSS, temperature_change=f"0 {unit}", thermal_expansion="0.00001 1/K")
        pointer = "/temperature_change"
    elif location == "truss_member":
        result = call(truss, **RESTRAINED_TRUSS, thermal=f"e,0 {unit},0.00001 1/K")
        pointer = "/thermal/0/1"
    elif location == "frame_mean":
        result = call(frame, **FRAME, thermal=f"e,0.00001 1/K,0 {unit},0 K/m")
        pointer = "/thermal/0/2"
    else:
        result = call(frame, **FRAME, thermal=f"e,0.00001 1/K,0 K,0 {unit}/m")
        pointer = "/thermal/0/3"
    assert not result.ok
    assert result.error.details["param"] == pointer
    assert all(unit in result.error.message for unit in ("K", "delta_degC", "delta_degF"))


@pytest.mark.parametrize("unit", ["K", "kelvin", "degree_Kelvin", "delta_degC", "delta_degF"])
def test_zero_temperature_difference_and_gradient(unit):
    result = ok(truss, **RESTRAINED_TRUSS, temperature_change=f"0 {unit}", thermal_expansion="0.00001 1/K")
    # N=-EA*alpha*dT, so a genuine zero difference gives exactly zero force.
    assert quantity(result.data["members"][0]["axial_force"]) == 0
    result = ok(frame, **FRAME, thermal=f"e,0.00001 1/K,0 {unit},0 {unit}/m")
    assert v(result, "maximum_nodal_displacement") == 0


def determinant(matrix):
    """Small Decimal determinant, independent of the runtime SVD solver."""
    a = [row.copy() for row in matrix]
    result = Decimal(1)
    for j in range(len(a)):
        pivot = max(range(j, len(a)), key=lambda i: abs(a[i][j]))
        if a[pivot][j] == 0:
            return Decimal(0)
        if pivot != j:
            a[j], a[pivot] = a[pivot], a[j]
            result = -result
        result *= a[j][j]
        for i in range(j+1, len(a)):
            factor = a[i][j]/a[j][j]
            for k in range(j+1, len(a)):
                a[i][k] -= factor*a[j][k]
    return result


def spring_bending_frequency():
    # L=2, EI=200000, mu=10. Write the four bending DOFs explicitly,
    # add the 0.001 N/m and N*m/rad supports, then solve det(K-w^2*M)=0.
    kb = [[12, 12, -12, 12], [12, 16, -12, 8],
          [-12, -12, 12, -12], [12, 8, -12, 16]]
    mb = [[156, 44, 54, -26], [44, 16, 26, -12],
          [54, 26, 156, -44], [-26, -12, -44, 16]]
    with localcontext() as context:
        context.prec = 70
        k = [[Decimal(x)*25000 for x in row] for row in kb]
        m = [[Decimal(x)*20/420 for x in row] for row in mb]
        k[0][0] += Decimal("0.001")
        k[1][1] += Decimal("0.001")
        lo, hi = Decimal(0), Decimal("0.00004")
        for _ in range(160):
            mid = (lo+hi)/2
            value = determinant([[k[i][j]-mid*m[i][j] for j in range(4)] for i in range(4)])
            if value > 0:
                lo = mid
            else:
                hi = mid
        return math.sqrt(float((lo+hi)/2))/(2*math.pi)


def test_weak_springs_are_positive_elastic_modes():
    result = ok(modal, **{**FRAME, "supports": "a,spring,0.001 N/m,0.001 N/m,0.001 N*m/rad"},
                mass_per_length="10 kg/m", elements_per_member=1, modes_count=6)
    expected = spring_bending_frequency()
    assert expected == pytest.approx(0.0007621334610557804, rel=1e-12, abs=0)
    assert v(result, "first_frequency") == pytest.approx(expected, rel=1e-10, abs=0)
    assert result.data["zero_mode_count"] == 0
    assert all(quantity(mode["frequency"]) > 0 for mode in result.data["modes"])


def test_slender_cantilever_frequency_is_rotation_invariant():
    # For one clamped cubic element, x=w^2*mu*L^4/EI satisfies
    # x^2-1224*x+15120=0. Use the product of roots for the smaller root.
    eigen_coefficient = 15120/(612+96*math.sqrt(39))
    expected = math.sqrt(eigen_coefficient*200e9*1e-15/(10*4))/(2*math.pi)
    assert expected == pytest.approx(0.0012572329940697857, rel=1e-12, abs=0)
    frequencies = []
    for end in ("1 m,1 m", f"{math.sqrt(2)} m,0 m", "-1 m,1 m"):
        result = ok(modal, nodes="a,0 m,0 m;b,"+end, members="e,a,b,200 GPa,1000 mm^2,1e-15 m^4",
                    supports="a,fixed", mass_per_length="10 kg/m", elements_per_member=1)
        frequencies.append(v(result, "first_frequency"))
        assert result.data["zero_mode_count"] == 0
    np.testing.assert_allclose(frequencies, expected, rtol=1e-10, atol=0)


@pytest.mark.parametrize("supports,zeros", [("", 3), ("a,pinned", 1), ("a,spring,0.001 N/m,0 N/m,0 N*m/rad", 2)])
def test_modal_zero_modes_follow_restraints(supports, zeros):
    result = ok(modal, **{**FRAME, "supports": supports}, mass_per_length="10 kg/m", elements_per_member=1)
    assert result.data["zero_mode_count"] == zeros
    assert all(quantity(mode["frequency"]) == 0 for mode in result.data["modes"][:zeros])
    assert all(quantity(mode["frequency"]) > 0 for mode in result.data["modes"][zeros:])


def test_disconnected_frames_have_six_rigid_modes():
    result = ok(modal, nodes="a,0 m,0 m;b,2 m,0 m;c,0 m,1 m;d,2 m,1 m",
                members="e,a,b,200 GPa,1000 mm^2,1e6 mm^4;f,c,d,200 GPa,1000 mm^2,1e6 mm^4",
                mass_per_length="10 kg/m", elements_per_member=1, modes_count=12)
    assert result.data["zero_mode_count"] == 6


def test_unresolvable_positive_modes_are_refused_instead_of_zeroed():
    result = call(modal, nodes="a,0 m,0 m;b,10000 m,0 m",
                  members="e,a,b,1e13 Pa,1e4 m^2,1e-24 m^4", supports="a,fixed",
                  mass_per_length="10 kg/m", elements_per_member=1)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == "/members"


def test_thick_i_omits_invalid_torsion_but_keeps_exact_properties():
    result = ok(section, shape="i", width="1 m", height="1 m", web_thickness="0.999 m", flange_thickness="0.001 m")
    # Two 1*.001 flanges and .999*.998 clear web. Outer minus side voids for Ix.
    assert v(result, "area") == pytest.approx(2*.001+.999*.998, rel=1e-12)
    assert v(result, "Ix") == pytest.approx((1-.001*.998**3)/12, rel=1e-12)
    assert "torsion_constant" not in result.values
    assert result.data["torsion_constant"] is None
    assert any("thin-wall formula is not valid" in warning for warning in result.warnings)


@pytest.mark.parametrize("shape", ["i", "h", "t", "channel"])
def test_valid_thin_open_wall_torsion(shape):
    result = ok(section, shape=shape, width="1 m", height="1 m", web_thickness="0.01 m", flange_thickness="0.01 m")
    flanges = 1 if shape == "t" else 2
    expected = (flanges*.01**3+(1-flanges*.01)*.01**3)/3
    assert v(result, "torsion_constant") == pytest.approx(expected, rel=1e-12, abs=0)


@pytest.mark.parametrize("width,height", [("1 m", "30 nm"), ("30 nm", "1 m"), ("1 m", "0.5 m")])
def test_rectangle_minor_principal_moment_without_subtractive_cancellation(width, height):
    result = ok(section, shape="rectangle", width=width, height=height)
    # bh^3/12 and hb^3/12, including the exact review input at 30 nm.
    expected = 1*.5**3/12 if height == "0.5 m" else 2.25e-24
    assert v(result, "principal_I2") == pytest.approx(expected, rel=1e-12, abs=0)


def test_hydrostatic_offset_preserves_small_deviator():
    result = ok(stress, **{**STRESS_3D, "sigma_x": "1e10 Pa", "sigma_y": "1e10 Pa", "sigma_z": "1e10 Pa", "tau_xy": "1e-6 Pa"})
    # The deviator has eigenvalues +tau, 0, -tau; hydrostatic stress contributes no yielding.
    assert v(result, "von_mises") == pytest.approx(math.sqrt(3)*1e-6, rel=1e-12, abs=0)
    assert v(result, "tresca") == pytest.approx(2e-6, rel=1e-12, abs=0)
    assert v(result, "maximum_shear_3d") == pytest.approx(1e-6, rel=1e-12, abs=0)
    assert [v(result, f"principal_stress_{j}") for j in (1, 2, 3)] == [1e10+1e-6, 1e10, 1e10-1e-6]


def test_out_of_plane_normal_stress_gives_largest_principal_strain():
    result = ok(stress, **{**STRESS_3D, "sigma_z": "100 MPa"}, youngs_modulus="200 GPa", poisson_ratio=.3)
    # epsilon_z=sz/E; epsilon_x=epsilon_y=-nu*sz/E.
    np.testing.assert_allclose([v(result, f"principal_strain_{j}") for j in (1, 2, 3)],
                               [.0005, -.00015, -.00015], rtol=1e-12, atol=0)


def test_three_dimensional_principal_strains_use_all_shears():
    tensor = np.array([[.001, .001, -.0005], [.001, -.0002, .0015], [-.0005, .0015, .0003]])
    result = ok(stress, state="three_dimensional", input_type="strain", youngs_modulus="200 GPa",
                epsilon_x="0.001 dimensionless", epsilon_y="-0.0002 dimensionless", epsilon_z="0.0003 dimensionless",
                gamma_xy="0.002 dimensionless", gamma_yz="0.003 dimensionless", gamma_zx="-0.001 dimensionless")
    np.testing.assert_allclose([v(result, f"principal_strain_{j}") for j in (1, 2, 3)],
                               np.linalg.eigvalsh(tensor)[::-1], rtol=1e-12, atol=0)


def test_secant_root_bracket_uses_yield_bound():
    result = ok(column, length="1e-9 m", youngs_modulus="1e13 Pa", area="1 m^2", inertia="1 m^4",
                yield_strength="1e-6 Pa", eccentricity="1 m", extreme_fibre="1 m")
    # r^2=1; secant argument squared = L^2*P/(4*E*I) ~1.25e-38.
    # cos(argument)=1 to double precision, so P=Sy*A/(1+e*c/r^2).
    expected = 1e-6/(1+1)
    assert v(result, "secant_yield_load") == pytest.approx(expected, rel=1e-12, abs=0)
    assert v(result, "estimated_capacity") == pytest.approx(expected, rel=1e-12, abs=0)


@pytest.mark.parametrize("tool,args,pointer", [
    (stress, dict(sigma_x="0 Pa", sigma_y="0 Pa", tau_xy="1e-300 yPa"), "/tau_xy"),
    (section, dict(width="1 m", height="1 m", at_height="1e-300 ym"), "/at_height"),
    (truss, {**TRUSS, "loads": "b,1e-300 yN,0 N"}, "/loads/0/1"),
    (frame, {**FRAME, "loads": "b,1e-300 yN,0 N,0 N*m"}, "/loads/0/1"),
    (modal, {**FRAME, "mass_per_length": "10 kg/m", "nodal_masses": "b,1e-300 yg,0 kg*m^2"}, "/nodal_masses/0/1"),
    (column, {**COLUMN, "applied_load": "1e-300 yN"}, "/applied_load"),
], ids=["stress", "section", "truss", "frame", "modal", "column"])
def test_conversion_underflow_is_not_literal_zero(tool, args, pointer):
    result = call(tool, **args)
    assert not result.ok
    assert result.error.family == "input.out_of_range"
    assert result.error.details["param"] == pointer
    genuine_zero = {key: value.replace("1e-300", "0") if isinstance(value, str) else value for key, value in args.items()}
    assert ok(tool, **genuine_zero).ok


async def test_gateway_parameter_descriptions_are_specific_and_unique():
    async with Client(gateway) as client:
        tools = [tool for tool in await client.list_tools() if tool.name.startswith("structures_")]
    assert len(tools) == 6
    for tool in tools:
        descriptions = {}
        # Checking every parameter also covers every quantity parameter.
        for name, schema in tool.input_schema["properties"].items():
            description = schema["description"]
            assert description not in descriptions, (tool.name, name, descriptions.get(description))
            assert len(description) <= 140, (tool.name, name, len(description))
            descriptions[description] = name
