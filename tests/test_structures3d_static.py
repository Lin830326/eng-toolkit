"""Hand calculations and independent routes for the spatial frame solver."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures3d.static import static
from eng_toolkit.domains.structures3d._model import compile_frame, parse_frame, recover, response, solve_static
from tests.structures3d_testing import FRAME, call, close, ok, quantity, table, v


def tip(result):
    return np.array([quantity(result.data["nodes"][-1][axis]) for axis in ("ux", "uy", "uz", "rx", "ry", "rz")])


def test_cantilever_two_planes_axial_torsion_and_station_endpoints():
    r = ok(static, **FRAME, loads="b,1000 N,1000 N,1000 N,1000 N*m,0 N*m,0 N*m")
    # L=2; EA=2e8; EIy=2e5; EIz=4e5; GJ=8e4.
    # u=PL/EA; v=PL^3/(3EIz); w=PL^3/(3EIy); twist=TL/GJ.
    expected = [1000*2/2e8, 1000*8/(3*4e5), 1000*8/(3*2e5), 1000*2/8e4,
                -1000*4/(2*2e5), 1000*4/(2*4e5)]
    np.testing.assert_allclose(tip(r), expected, rtol=1e-11, atol=1e-13)
    row = table(r)[-1]
    np.testing.assert_allclose([float(row[x]) for x in ("u_m", "v_m", "w_m", "rx_rad", "ry_rad", "rz_rad")], expected, rtol=1e-11)
    for residual in r.data["equilibrium_residual"].values():
        close(quantity(residual), 0, abs=1e-8)


def test_l_shaped_frame_unit_load_integrals():
    r = ok(static, nodes="a,0 m,0 m,0 m;b,2 m,0 m,0 m;c,2 m,1 m,0 m",
           members="e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,1e6 mm^4,2e6 mm^4;f,b,c,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,1e6 mm^4,2e6 mm^4",
           supports="a,fixed", loads="c,10 N,20 N,30 N,0 N*m,0 N*m,0 N*m")
    # Castigliano/unit-load: Mz=(2-x)Py-Px on first span, Mx=Pz.
    # Cxx=2/EA+(2+1/3)/EI; Cxy=-2/EI; Cyy=8/(3EI)+1/EA.
    # Czz=8/(3EI)+2/GJ+1/(3EI). EA=2e8, EI=2e5, GJ=1.6e5.
    expected = [10*(2/2e8+7/(3*2e5))-20*2/2e5,
                -10*2/2e5+20*(8/(3*2e5)+1/2e8),
                30*(3/2e5+2/1.6e5)]
    np.testing.assert_allclose(tip(r)[:3], expected, rtol=2e-10, atol=1e-12)


@pytest.mark.parametrize("point", [False, True])
def test_exact_uniform_and_point_load_recovery_with_shear(point):
    load = "e,point,local,1 m,0 N,1000 N,1000 N" if point else "e,uniform,local,0 N/m,1000 N/m,1000 N/m"
    r = ok(static, **FRAME, member_loads=load, shear_properties="e,800 mm^2,800 mm^2")
    # Uniform: qL^4/(8EI)+qL^2/(2GAs); point a=1: Pa²(3L-a)/(6EI)+Pa/GAs.
    bend = 1000*5/6 if point else 1000*16/8
    shear = 1000/64e6 if point else 1000*4/(2*64e6)
    close(tip(r)[1], bend/4e5+shear)
    close(tip(r)[2], bend/2e5+shear)
    if point:
        at_load = [row for row in table(r) if float(row["x_m"]) == 1.]
        assert {row["side"] for row in at_load} == {"left", "right"}
        values = {row["side"]: float(row["Vy_N"]) for row in at_load}
        close(values["right"]-values["left"], 1000)


def test_released_tip_bending_and_static_condensation():
    r = ok(static, **FRAME, releases="e,end,ry+rz", loads="b,0 N,1000 N,1000 N,0 N*m,0 N*m,0 N*m")
    # End moment releases preserve the force-loaded cantilever solution.
    close(tip(r)[1], 1000*8/(3*4e5))
    close(tip(r)[2], 1000*8/(3*2e5))
    assert r.data["stability"]["statically_condensed_release_dofs"] == 2
    for name in ("My_end", "Mz_end"):
        close(quantity(r.data["members"][0]["end_forces"][name]), 0., abs=1e-8)


def test_pinned_beam_uniform_moment_extremum_and_deflection():
    r = ok(static, **{**FRAME, "supports": "a,pinned;a,rx;b,uy+uz+rx"},
           releases="e,start,ry+rz;e,end,ry+rz", member_loads="e,uniform,local,0 N/m,1000 N/m,0 N/m")
    # qL²/8=500 Nm; 5qL⁴/(384EI)=0.0005208333 m.
    close(v(r, "maximum_abs_Mz"), 1000*4/8)
    close(v(r, "maximum_abs_v"), 5*1000*16/(384*4e5))


def test_space_truss_tripod_from_frame_releases():
    root3 = math.sqrt(3)
    nodes = f"a,1 m,0 m,0 m;b,-0.5 m,{root3/2} m,0 m;c,-0.5 m,{-root3/2} m,0 m;t,0 m,0 m,1 m"
    members = ";".join(f"e{i},{name},t,200 GPa,80 GPa,100 mm^2,1e6 mm^4,1e6 mm^4,1e6 mm^4" for i, name in enumerate("abc"))
    releases = ";".join(f"e{i},{end},ry+rz" for i in range(3) for end in ("start", "end"))
    r = ok(static, nodes=nodes, members=members, supports="a,fixed;b,fixed;c,fixed;t,rx+ry+rz",
           releases=releases, loads="t,0 N,0 N,-3000 N,0 N*m,0 N*m,0 N*m")
    # Three bars L=sqrt(2), h=1: kz=3EA h²/L³; compression per bar=P L/(3h).
    close(tip(r)[2], -3000*math.sqrt(2)**3/(3*200e9*100e-6), rel=1e-10)
    close(tip(r)[0], 0, abs=1e-12)
    close(tip(r)[1], 0, abs=1e-12)
    for member in r.data["members"]:
        close(quantity(member["end_forces"]["Fx_end"]), -1000*math.sqrt(2), rel=1e-10)


@pytest.mark.parametrize("releases", ["", "f,end,ry+rz"])
def test_rigid_space_rotation_invariance_and_translated_origin(releases):
    coords = np.array([[0., 0., 0.], [2., 0., 0.], [2., 1., 0.]])
    force, moment = np.array([10., 20., 30.]), np.array([3., -2., 1.])
    if releases:
        moment[:] = 0.  # No applied joint moment in a released direction.
    axis = np.array([1., 2., 3.])/math.sqrt(14)
    skew = np.array([[0., -axis[2], axis[1]], [axis[2], 0., -axis[0]], [-axis[1], axis[0], 0.]])
    rotation = np.eye(3)+math.sin(.7)*skew+(1-math.cos(.7))*(skew@skew)
    args = dict(members="e,a,b,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4;f,b,c,200 GPa,80 GPa,1000 mm^2,1e6 mm^4,2e6 mm^4,1e6 mm^4", supports="a,fixed")

    def solve(rot, offset):
        points = coords@rot.T+offset
        orient = rot@np.array([0., 0., 1.])
        forces, moments = rot@force, rot@moment
        return ok(static, **args, releases=releases, nodes=";".join(name+","+",".join(f"{x:.16g} m" for x in point) for name, point in zip("abc", points)),
                  orientations=";".join(name+",vector,"+",".join(f"{x:.16g}" for x in orient) for name in "ef"),
                  loads="c,"+",".join([*(f"{x:.16g} N" for x in forces), *(f"{x:.16g} N*m" for x in moments)]))
    base = solve(np.eye(3), np.zeros(3))
    turned = solve(rotation, np.array([4., -3., 2.]))
    np.testing.assert_allclose(tip(turned)[:3], rotation@tip(base)[:3], rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(tip(turned)[3:], rotation@tip(base)[3:], rtol=1e-9, atol=1e-12)
    for a, b in zip(base.data["members"], turned.data["members"]):
        np.testing.assert_allclose([quantity(x) for x in a["end_forces"].values()], [quantity(x) for x in b["end_forces"].values()], rtol=1e-9, atol=1e-8)
    for residual in turned.data["equilibrium_residual"].values():
        close(quantity(residual), 0, abs=1e-8)


def test_thermal_free_and_restrained_and_spring_rest_settlement():
    r = ok(static, **FRAME, thermal="e,12e-6 1/K,50 K")
    # Expansion alpha*dT*L=12e-6*50*2=0.0012 m; no axial reaction.
    close(tip(r)[0], .0012)
    close(quantity(r.data["nodes"][0]["reaction_Fx"]), 0., abs=1e-7)
    r = ok(static, **{**FRAME, "supports": "a,fixed;b,ux"}, thermal="e,12e-6 1/K,50 delta_degC")
    close(quantity(r.data["nodes"][1]["reaction_Fx"]), -200e9*.001*12e-6*50)
    r = ok(static, **{**FRAME, "supports": "a,fixed;b,spring,ux,100 MN/m,1 mm"})
    # EA/L = 100 MN/m equals spring k. Rest 1mm gives 0.5mm displacement.
    close(tip(r)[0], .0005)
    r = ok(static, **{**FRAME, "supports": "a,fixed;b,settlement,ux,1 mm"})
    close(quantity(r.data["nodes"][1]["reaction_Fx"]), 200e9*.001/2*.001)


@pytest.mark.parametrize("units", ["metric", "imperial"])
def test_inch_psi_kip_torque_units(units):
    r = ok(static, nodes="a,0 inch,0 inch,0 inch;b,20 inch,0 inch,0 inch",
           members="e,a,b,30000000 psi,12000000 psi,2 inch^2,3 inch^4,4 inch^4,5 inch^4",
           supports="a,fixed", loads="b,1 kip,1 kip,1 kip,1 kip*inch,0 kN*m,0 kN*m", unit_system=units)
    node = r.data["nodes"][-1]
    # All arithmetic in inches/lbf: P=1000 lbf, L=20in, E=30e6psi.
    close(quantity(node["ux"], "inch"), 1000*20/(30e6*2))
    close(quantity(node["uy"], "inch"), 1000*20**3/(3*30e6*4))
    close(quantity(node["uz"], "inch"), 1000*20**3/(3*30e6*3))
    close(quantity(node["rx"], "rad"), 1000*20/(12e6*5))


def independent_beam_block(ei, length):
    # Independently integrate cubic Hermite curvatures at 3 Gauss points.
    points, weights = np.polynomial.legendre.leggauss(3)
    result = np.zeros((4, 4))
    for point, weight in zip(points, weights):
        s = (point+1)/2
        curvature = np.array([-6+12*s, length*(-4+6*s), 6-12*s, length*(-2+6*s)])/length**2
        result += ei*np.outer(curvature, curvature)*weight*length/2
    return result


def test_randomized_independent_hermite_assembly_and_reciprocity():
    rng = np.random.default_rng(412)
    for _ in range(6):
        lengths = rng.uniform(.4, 2., size=3)
        inertias = rng.uniform(.4e-6, 2e-6, size=3)
        coordinates = np.r_[0., np.cumsum(lengths)]
        nodes = ";".join(f"n{i},{x:.16g} m,0 m,0 m" for i, x in enumerate(coordinates))
        members = ";".join(f"e{i},n{i},n{i+1},200 GPa,80 GPa,1000 mm^2,1e-6 m^4,{inertia:.16g} m^4,1e-6 m^4" for i, inertia in enumerate(inertias))
        model = parse_frame(nodes, members, "n0,fixed", "n3,0 N,1 N,0 N,0 N*m,0 N*m,0 N*m")
        mesh = compile_frame(model)
        u, _, _ = solve_static(mesh)
        independent = np.zeros((8, 8))
        for i, (length, inertia) in enumerate(zip(lengths, inertias)):
            indices = np.arange(2*i, 2*i+4)
            independent[np.ix_(indices, indices)] += independent_beam_block(200e9*inertia, length)
        f = np.zeros(8)
        f[-2] = 1.
        expected = np.linalg.solve(independent[2:, 2:], f[2:])
        actual = np.array([u[6*i+j] for i in range(1, 4) for j in (1, 5)])
        np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-13)
        other = parse_frame(nodes, members, "n0,fixed", "n1,0 N,1 N,0 N,0 N*m,0 N*m,0 N*m")
        u_other, _, _ = solve_static(compile_frame(other))
        close(u[7], u_other[19], rel=1e-9, abs=1e-13)


def test_refined_static_exact_recovery_and_global_point_loads():
    model = parse_frame(**FRAME, member_loads="e,point,global,1 m,100 N,300 N,-200 N;e,uniform,local,10 N/m,20 N/m,30 N/m")
    coarse, fine = compile_frame(model), compile_frame(model, 8)
    uc, _, _ = solve_static(coarse)
    uf, _, _ = solve_static(fine)
    np.testing.assert_allclose(uc, uf[:len(uc)], rtol=1e-9, atol=1e-12)
    for element in fine.elements:
        d, end = recover(element, uf)
        np.testing.assert_allclose(response(element, d, end, element.length)[6:], d[6:], rtol=1e-8, atol=1e-12)


def test_refined_endpoint_load_survives_decimal_roundoff():
    # At a=0.7, repeated h=0.7/3 need not sum bit-for-bit to the input endpoint.
    args = {**FRAME, "nodes": "a,0 m,0 m,0 m;b,0.7 m,0 m,0 m"}
    model = parse_frame(**args, member_loads="e,point,local,0.7 m,0 N,1000 N,0 N")
    mesh = compile_frame(model, 3)
    displacement, _, _ = solve_static(mesh)
    close(displacement[7], 1000*.7**3/(3*4e5), rel=1e-10)


def test_refuse_kinematic_mechanism_without_material_threshold():
    r = call(static, **{**FRAME, "supports": ""})
    assert not r.ok and r.error.details["param"] == "/supports"
    # Very unequal section inertias must not be relabelled as a mechanism.
    r = ok(static, **{**FRAME, "members": "e,a,b,200 GPa,80 GPa,1000 mm^2,1e-15 m^4,1e-6 m^4,1e-6 m^4"}, loads="b,0 N,1 N,0 N,0 N*m,0 N*m,0 N*m")
    close(tip(r)[1], 8/(3*2e5))


def test_planar_reduction_and_default_vertical_axes():
    # Plane-frame EI equations are the independent reference, without importing another domain.
    r = ok(static, **FRAME, loads="b,0 N,-1000 N,0 N,0 N*m,0 N*m,0 N*m")
    close(tip(r)[1], -1000*2**3/(3*200e9*2e-6))
    np.testing.assert_allclose(tip(r)[[0, 2, 3, 4]], 0., atol=1e-13)
    vertical = ok(static, **{**FRAME, "nodes": "a,0 m,0 m,0 m;b,0 m,2 m,0 m"})
    np.testing.assert_allclose(vertical.data["members"][0]["local_axes"], [[0,1,0], [0,0,1], [1,0,0]], atol=1e-14)
