"""Frame verification against closed forms, equilibrium and an independent solve."""

import math

import numpy as np
import pytest

from eng_toolkit.domains.structures.frame import frame
from eng_toolkit.domains.structures._model import assemble_static, parse_frame
from tests.structures_testing import FRAME, call, close, ok, quantity, table, v


def node(r,index,name,unit=None):
    return quantity(r.data["nodes"][index][name],unit)


def end(r,name,index=0):
    return quantity(r.data["members"][index]["end_forces"][name])


def at(rows,x,side=None):
    return next(row for row in rows if abs(float(row["x_m"])-x)<1e-10 and (side is None or row["side"]==side))


def balanced(r,abs=1e-7):
    for out in r.data["equilibrium_residual"].values():
        close(quantity(out),0,abs=abs)


def test_cantilever_tip_and_moment():
    r = ok(frame,**FRAME,loads="b,0 N,-1000 N,0 N*m")
    # EI=200000 Nm², L=2m; v=-1000*8/(3*200000), theta=-1000*4/(2*200000).
    close(node(r,1,"uy"),-1/75)
    close(node(r,1,"rotation"),-.01)
    close(node(r,0,"reaction_y"),1000)
    close(node(r,0,"reaction_moment"),2000)
    close(v(r,"maximum_abs_moment"),2000)
    balanced(r)
    moment = ok(frame,**FRAME,loads="b,0 N,0 N,1 kN*m")
    # Pure end moment: v=M L²/(2EI)=.01, theta=ML/EI=.01.
    close(node(moment,1,"uy"),.01)
    close(node(moment,1,"rotation"),.01)


@pytest.mark.parametrize("support,release",[("a,pinned;b,roller_y","none"),("a,pinned;b,roller_y","both"),("a,fixed;b,fixed","both")])
def test_simply_supported_uniform_load(support,release):
    r = ok(frame,**{**FRAME,"members":FRAME["members"]+","+release,"supports":support},member_loads="e,uniform,local,0 N/m,-1000 N/m")
    # R=wL/2=1000; Mmax=wL²/8=500; vmax=5wL⁴/(384EI)=1/960 m.
    close(node(r,0,"reaction_y"),1000)
    close(node(r,1,"reaction_y"),1000)
    close(end(r,"M_start"),0,abs=1e-8)
    close(end(r,"M_end"),0,abs=1e-8)
    close(v(r,"maximum_abs_moment"),500)
    close(quantity(r.data["members"][0]["maximum_abs_moment"]),500)
    close(quantity(r.data["members"][0]["maximum_moment_x"]),1)
    close(float(at(table(r),1)["v_local_m"]),-1/960)
    balanced(r)


def test_fixed_fixed_and_propped_cantilever():
    r = ok(frame,**{**FRAME,"supports":"a,fixed;b,fixed"},member_loads="e,uniform,local,0 N/m,-1200 N/m")
    # End moment magnitudes wL²/12=400; centre v=wL⁴/(384EI)=.00025.
    close(end(r,"M_start"),400)
    close(end(r,"M_end"),-400)
    close(float(at(table(r),1)["v_local_m"]),-.00025)
    propped = ok(frame,**{**FRAME,"supports":"a,fixed;b,roller_y"},member_loads="e,uniform,local,0 N/m,-1200 N/m")
    # Fixed support R=5wL/8=1500, prop R=3wL/8=900.
    close(node(propped,0,"reaction_y"),1500)
    close(node(propped,1,"reaction_y"),900)
    balanced(propped)


def test_offcentre_point_load_and_exact_jump():
    r = ok(frame,**{**FRAME,"supports":"a,pinned;b,roller_y"},member_loads="e,point,local,0.5 m,0 N,-1000 N")
    # Rleft=P*b/L=750, Rright=P*a/L=250; moment under load=750*.5=375.
    close(node(r,0,"reaction_y"),750)
    close(node(r,1,"reaction_y"),250)
    close(v(r,"maximum_abs_moment"),375)
    rows = table(r)
    close(float(at(rows,.5,"left")["V_N"]),750)
    close(float(at(rows,.5,"right")["V_N"]),-250)
    # Deflection at load: -P*a²*b²/(3*EI*L)=-1000*.25*2.25/(1.2e6).
    close(float(at(rows,.5)["v_local_m"]),-1000*.5**2*1.5**2/(3*200000*2))
    balanced(r)


def test_triangular_load_and_thermal_gradient():
    r = ok(frame,**{**FRAME,"supports":"a,pinned;b,roller_y"},member_loads="e,linear,local,0 N/m,0 N/m,0 N/m,-600 N/m")
    # Triangle resultant=600*2/2=600 at 2L/3; left=200,right=400.
    close(node(r,0,"reaction_y"),200)
    close(node(r,1,"reaction_y"),400)
    close(quantity(r.data["maximum_moment_location"]["x"]),2/math.sqrt(3))
    thermal = ok(frame,**FRAME,thermal="e,0.00001 1/K,100 K,100 K/m")
    # Free expansion eps=.001: u=eps L=.002; kappa=-.001: theta=kL=-.002, v=kL²/2=-.002.
    close(node(thermal,1,"ux"),.002)
    close(node(thermal,1,"rotation"),-.002)
    close(node(thermal,1,"uy"),-.002)
    close(end(thermal,"M_start"),0,abs=1e-7)
    balanced(thermal)


@pytest.mark.parametrize("position",[0,2])
def test_member_endpoint_point_loads_use_interior_limits(position):
    r = ok(frame,**FRAME,member_loads=f"e,point,local,{position} m,0 N,-1000 N")
    # A load at the clamp transfers directly into its support: no internal shear.
    # At the tip it produces V=1000 and Mmax=1000*2 inside the member.
    close(v(r,"maximum_abs_shear"),0 if position == 0 else 1000,abs=1e-8)
    close(v(r,"maximum_abs_moment"),0 if position == 0 else 2000,abs=1e-8)
    balanced(r)


def test_timoshenko_tip_and_loaded_recovery():
    r = ok(frame,**FRAME,loads="b,0 N,-1000 N,0 N*m",shear_properties="e,80 GPa,100 mm^2")
    # Add shear PL/(GAs)=1000*2/(80e9*1e-4)=.00025m.
    close(node(r,1,"uy"),-1/75-.00025)
    close(float(at(table(r),2)["v_local_m"]),node(r,1,"uy"))
    simple = ok(frame,**{**FRAME,"supports":"a,pinned;b,roller_y"},member_loads="e,uniform,local,0 N/m,-1000 N/m",shear_properties="e,80 GPa,100 mm^2")
    # Simply supported uniform load shear midspan contribution wL²/(8GAs)=.0000625.
    close(float(at(table(simple),1)["v_local_m"]),-1/960-.0000625)
    balanced(simple)


def test_released_member_does_not_release_shared_joint_rotation():
    r = ok(frame,nodes="a,0 m,0 m;b,1 m,0 m;c,2 m,0 m",members="left,a,b,200 GPa,1000 mm^2,1e6 mm^4,end;right,b,c,200 GPa,1000 mm^2,1e6 mm^4",supports="a,fixed;c,fixed",loads="b,0 N,-1000 N,200 N*m")
    # Joint moment 200Nm goes wholly into the unreleased right member.
    # Left end at b remains moment-free despite sharing the node's translation.
    close(end(r,"M_end",0),0,abs=1e-8)
    close(end(r,"M_start",1),200,abs=1e-8)
    balanced(r)


def test_uniform_axial_load_and_thermal_restraint():
    r = ok(frame,**FRAME,member_loads="e,uniform,local,1000 N/m,0 N/m")
    # Integrate N=q(L-x): u(L)=qL²/(2EA)=1e-5m.
    close(node(r,1,"ux"),1000*4/(2*200e9*.001))
    close(float(at(table(r),1)["u_local_m"]),1000*(2*1-.5)/(200e9*.001))
    thermal = ok(frame,**{**FRAME,"supports":"a,fixed;b,fixed"},thermal="e,0.00001 1/K,100 K,100 K/m")
    # eps=.001 -> N=-EA eps=-200000N; kappa=-.001 -> internal M=-EI*kappa=200Nm.
    close(end(thermal,"N_start"),200000)
    close(end(thermal,"M_start"),-200)
    close(end(thermal,"M_end"),200)
    balanced(thermal)


def test_settlement_and_spring():
    r = ok(frame,**{**FRAME,"supports":"a,fixed;b,fixed,1 mm,0 m,0 rad"})
    # Axial reaction EA*settlement/L=200e9*.001*.001/2=100000N.
    close(node(r,1,"reaction_x"),100000)
    close(node(r,0,"reaction_x"),-100000)
    spring = ok(frame,**{**FRAME,"supports":"a,fixed;b,spring,100000000 N/m,0 N/m,0 N*m/rad"},loads="b,1000 N,0 N,0 N*m")
    # Member stiffness EA/L=1e8 equals spring stiffness: u=1000/2e8=5e-6, spring force=-500.
    close(node(spring,1,"ux"),5e-6)
    close(node(spring,1,"reaction_x"),-500)
    balanced(spring)


def test_frame_rotation_90_and_imperial():
    original = ok(frame,**FRAME,loads="b,100 N,-1000 N,20 N*m",member_loads="e,uniform,global,10 N/m,-30 N/m")
    rotated = ok(frame,**{**FRAME,"nodes":"a,0 m,0 m;b,0 m,2 m"},loads="b,1000 N,100 N,20 N*m",member_loads="e,uniform,global,30 N/m,10 N/m")
    for name in ("N_start","V_start","M_start","N_end","V_end","M_end"):
        close(end(original,name),end(rotated,name),abs=1e-7)
    close(node(original,1,"ux"),node(rotated,1,"uy"))
    close(node(original,1,"uy"),-node(rotated,1,"ux"))
    imperial = ok(frame,nodes="a,0 inch,0 inch;b,100 inch,0 inch",members="e,a,b,30000000 psi,2 inch^2,10 inch^4",supports="a,fixed",loads="b,0 kip,-1 kip,0 kip*inch",unit_system="imperial")
    # -PL³/(3EI)=-1000*100³/(3*30e6*10)=-10/9 inch.
    close(node(imperial,1,"uy"),-10/9)


@pytest.mark.parametrize("seed",range(10))
def test_random_cantilever_and_uniform_differential(seed):
    rng = np.random.default_rng(seed)
    length,ei,p,w = rng.uniform(1,5),rng.uniform(1e5,1e6),rng.uniform(10,1000),rng.uniform(10,1000)
    r = ok(frame,nodes=f"a,0 m,0 m;b,{length} m,0 m",members=f"e,a,b,200 GPa,1000 mm^2,{ei/200e9} m^4",supports="a,fixed",loads=f"b,0 N,{-p} N,0 N*m",member_loads=f"e,uniform,local,0 N/m,{-w} N/m")
    # Superposition of hand-integrated tip and uniform-load cantilever: PL³/(3EI)+wL⁴/(8EI).
    expected = -p*length**3/(3*ei)-w*length**4/(8*ei)
    close(node(r,1,"uy"),expected,rel=1e-9)
    close(float(at(table(r),length)["v_local_m"]),expected,rel=1e-9)
    balanced(r)


def test_portal_sway_rigid_beam_limit():
    r = ok(frame,nodes="a,0 m,0 m;b,0 m,3 m;c,4 m,3 m;d,4 m,0 m",members="l,a,b,200 GPa,1 m^2,1e-5 m^4;beam,b,c,200 GPa,1 m^2,.1 m^4;r,d,c,200 GPa,1 m^2,1e-5 m^4",supports="a,fixed;d,fixed",loads="b,1000 N,0 N,0 N*m")
    # Rigid beam gives two fixed-guided columns: total k=24EI/h³, sway=1000*27/(24*2e6).
    close(node(r,1,"ux"),1000*27/(24*2e6),rel=2e-4)
    balanced(r,abs=1e-6)


def test_symmetry_reciprocity_and_scipy_solve():
    scipy = pytest.importorskip("scipy.linalg")
    model = parse_frame(**FRAME,loads="b,100 N,-1000 N,30 N*m")
    k,f,_ = assemble_static(model)
    # Maxwell-Betti reciprocity: symmetric K and compliance; independent LAPACK solve agrees.
    np.testing.assert_allclose(k,k.T,rtol=0,atol=1e-10)
    free = [3,4,5]
    expected = scipy.solve(k[np.ix_(free,free)],f[free],assume_a="pos")
    r = ok(frame,**FRAME,loads="b,100 N,-1000 N,30 N*m")
    np.testing.assert_allclose([node(r,1,x) for x in ("ux","uy","rotation")],expected,rtol=1e-10,atol=1e-12)
    compliance = np.linalg.solve(k[np.ix_(free,free)],np.eye(3))
    np.testing.assert_allclose(compliance,compliance.T,rtol=1e-12,atol=1e-15)


@pytest.mark.parametrize("supports,members,loads",[("",FRAME["members"],""),("a,pinned",FRAME["members"],""),("a,pinned;b,roller_y",FRAME["members"]+",both","a,0 N,0 N,1 N*m")])
def test_mechanisms(supports,members,loads):
    r = call(frame,**{**FRAME,"supports":supports,"members":members},loads=loads)
    assert not r.ok
    assert r.error.details["param"] == "/supports"


def test_max_member_deflection_is_the_in_span_maximum_not_the_nodal_one():
    # UAT S1-3: the nodal maximum is at the point-load node, the true maximum is elsewhere in the span.
    # Simply supported, L = 2 m, EI = 200000 N m^2, P = 1000 N at a = 0.5 m from the left support (node c).
    # Under the load v = P a^2 b^2 / (3 EI L) = 4.6875e-4 m; the maximum lies in the longer part at x = L - sqrt((L^2 - a^2)/3) = 0.88197 m
    # and equals P a (L^2 - a^2)^1.5 / (9 sqrt(3) EI L) = 5.8231e-4 m (12 % more).
    L, a, EI, P = 2.0, 0.5, 200000.0, 1000.0
    r = ok(frame,nodes="a,0 m,0 m;c,0.5 m,0 m;b,2 m,0 m",members="e1,a,c,200 GPa,1000 mm^2,1e6 mm^4;e2,c,b,200 GPa,1000 mm^2,1e6 mm^4",
           supports="a,pinned;b,roller_y",loads="c,0 N,-1000 N,0 N*m")
    nodal = v(r,"maximum_nodal_displacement")
    under_load = P*a**2*(L-a)**2/(3*EI*L)
    close(nodal,under_load,rel=1e-9)
    expected = P*a*(L**2-a**2)**1.5/(9*math.sqrt(3)*EI*L)
    close(v(r,"max_member_deflection"),expected,rel=1e-9)
    assert v(r,"max_member_deflection") > 1.12*nodal
    location = r.data["max_member_deflection_location"]
    assert location["member"] == "e2"
    close(quantity(location["x"]),L-math.sqrt((L**2-a**2)/3)-a,rel=1e-6)  # local x on member e2, which starts at c
    assert quantity(location["signed_local_deflection"]) < 0
    assert any("max_member_deflection" in w and "exceeds the largest nodal" in w for w in r.warnings)
    per_member = {m["id"]: quantity(m["maximum_abs_deflection"]) for m in r.data["members"]}
    close(per_member["e2"],expected,rel=1e-9)
    assert per_member["e1"] < expected


def test_max_member_deflection_of_the_classical_cases():
    # Cantilever tip P L^3/(3 EI) = 1000*8/(3*200000) = 1/75 m at x = L; uniform simply supported 5 w L^4/(384 EI) = 1/960 m at midspan.
    tip = ok(frame,**FRAME,loads="b,0 N,-1000 N,0 N*m")
    close(v(tip,"max_member_deflection"),1/75)
    close(quantity(tip.data["max_member_deflection_location"]["x"]),2.0,rel=1e-9)
    assert not any("exceeds the largest nodal" in w for w in tip.warnings)  # the nodal and the member maximum agree
    uniform = ok(frame,**{**FRAME,"supports":"a,pinned;b,roller_y"},member_loads="e,uniform,local,0 N/m,-1000 N/m")
    close(v(uniform,"max_member_deflection"),1/960)
    close(quantity(uniform.data["max_member_deflection_location"]["x"]),1.0,rel=1e-7)
