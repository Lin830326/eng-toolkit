"""Static plane frame tool; element physics is shared with modal analysis."""

import numpy as np
from typing import Annotated
from pydantic import Field

from eng_toolkit.domains.structures._common import FrameMembers, FrameSupports, MemberLoads, NodalLoads, Nodes2, Result, System, count, csv_text, run
from eng_toolkit.domains.structures._model import assemble_static, equilibrium, extrema_stations, parse_frame, recover, response, solve_static
from eng_toolkit.domains.structures._server import server


@server.tool(description="2D Euler-Bernoulli/Timoshenko direct-stiffness frames with statically condensed pin releases, settlements/springs, uniform/linear/point loads and thermal strain. Nodal results, end forces, exact member M/V extrema and station CSVs. Linear small displacement; no P-delta. Caps: 100 nodes, 200 members, 101 stations/member.")
def frame(
    nodes: Nodes2 = "", members: FrameMembers = "", supports: FrameSupports = "", loads: NodalLoads = "", member_loads: MemberLoads = "",
    shear_properties: Annotated[str, Field(description="Timoshenko shear modulus/effective area 'member,G,As', e.g. 'e,80 GPa,800 mm^2'; '' uses Euler-Bernoulli.")] = "",
    thermal: Annotated[str, Field(description="Initial strain/curvature 'member,alpha,dT,dT/dy', e.g. 'e,0.00001 1/K,50 K,0 K/m'; local +y gradient; '' omits.")] = "",
    stations: Annotated[int, Field(description="Number of equally spaced output stations per member, e.g. 21; range 2..101; load jumps and extrema are added.")] = 21,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        n = count(stations,"stations",2,101)
        model = parse_frame(nodes,members,supports,loads,member_loads,shear_properties,thermal)
        r = Result(unit_system, {
            "direct_stiffness": "K_global=sum(T^T*k*T); Kff*uf=Ff-Kfc*uc; support reactions=K*u-F",
            "Euler_Bernoulli": "axial EA/L; bending EI/L^3*[[12,6L,-12,6L],[6L,4L^2,-6L,2L^2],[-12,-6L,12,-6L],[6L,2L^2,-6L,4L^2]]",
            "Timoshenko": "phi=12EI/(G*As*L^2); bending divisor 1+phi; 4L^2 becomes (4+phi)L^2 and 2L^2 becomes (2-phi)L^2",
            "release": "Kaa-Kar*Krr^-1*Kra, fa-Kar*Krr^-1*fr; released displacement recovered from Krr*dr=fr-Kra*da",
            "fixed_end": "uniform qy equivalent nodal [qy*L/2,qy*L^2/12,qy*L/2,-qy*L^2/12]; point p uses Hermite N(a)*p (EB); general loads from exact integrated compatibility",
            "recovery": "N=-n_i-int(qx); V=v_i+int(qy); M=-m_i+v_i*x+int(qy*(x-s)); theta'=M/EI+kappa0; v'=theta-V/(GAs); u'=N/EA+epsilon0",
        }, "Local x from start to end, local y counterclockwise. End forces are actions on the element; internal N tension positive, V=dM/dx. Uniform E,A,I. Pin releases free member rotations independently of joint rotations. Thermal gradient dT/dy gives curvature -alpha*dT/dy.",
                   "100 nodes, 200 members, 400 nodal/member-load records each, text <=60000 characters, 2..101 stations. Linear static small displacement; no geometric stiffness, P-delta, plasticity or load combinations. Euler-Bernoulli unless shear_properties supply G and effective shear area.")
        k,f,elements = assemble_static(model)
        total = k+np.diag(model.springs)
        effective = f+model.springs*model.rest
        labels = [f"{name}/{axis}" for name in model.ids for axis in ("x","y","rotation")]
        u,stability = solve_static(total,effective,model.fixed,labels,inactive_allowed=range(2,len(f),3))
        reactions = -model.springs*(u-model.rest)
        residual = total@u-effective
        for dof in model.fixed:
            reactions[dof] += residual[dof]
        eq = equilibrium(model.xy,reactions+f)
        r.data["stability"] = stability
        r.data["nodes"] = [{"id":name,"ux":r.quantity(u[3*i],"length"),"uy":r.quantity(u[3*i+1],"length"),"rotation":r.quantity(u[3*i+2],"angle"),
                            "reaction_x":r.quantity(reactions[3*i],"force"),"reaction_y":r.quantity(reactions[3*i+1],"force"),"reaction_moment":r.quantity(reactions[3*i+2],"moment")} for i,name in enumerate(model.ids)]
        r.data["equilibrium_residual"] = {name:r.quantity(value,kind) for name,value,kind in zip(("Fx","Fy","Mz"),eq,("force","force","moment"))}
        r.data["equilibrium_origin"] = "First input node; includes all nodal and member loads, fixed reactions and spring forces."
        r.data["members"] = []
        max_m,max_v = (0.,model.members[0].id,0.),(0.,model.members[0].id,0.)
        for index,element in enumerate(elements):
            d,end = recover(element,u)
            samples = {(float(x),"left" if x == element.length else "right") for x in np.linspace(0,element.length,n)}
            samples |= set(extrema_stations(element,end))
            rows = []
            for x,side in sorted(samples):
                axial,shear,moment,ux,uy,rotation = response(element,d,end,x,side)
                rows.append([x,side,axial,shear,moment,ux,uy,rotation])
            member_m,member_v = (0.,0.),(0.,0.)
            for x,side in extrema_stations(element,end):
                _,shear,moment,*_ = response(element,d,end,x,side)
                if abs(moment) > member_m[0]:
                    member_m = (abs(moment),x)
                if abs(shear) > member_v[0]:
                    member_v = (abs(shear),x)
                if abs(moment) > max_m[0]:
                    max_m = (abs(moment),element.member.id,x)
                if abs(shear) > max_v[0]:
                    max_v = (abs(shear),element.member.id,x)
            artifact = handle.write_text(f"member_{index+1}_stations.csv",csv_text(["x_m","side","N_N","V_N","M_N_m","u_local_m","v_local_m","rotation_rad"],rows),fmt="csv")
            r.artifacts.append(artifact)
            r.data["members"].append({"id":element.member.id,"theory":"Euler-Bernoulli" if element.member.GA is None else "Timoshenko", "end_forces": {name:r.quantity(value,"moment" if name.startswith("M") else "force") for name,value in zip(("N_start","V_start","M_start","N_end","V_end","M_end"),end)},
                                      "maximum_abs_moment":r.quantity(member_m[0],"moment"),"maximum_moment_x":r.quantity(member_m[1],"length"),
                                      "maximum_abs_shear":r.quantity(member_v[0],"force"),"maximum_shear_x":r.quantity(member_v[1],"length"),
                                      "table": {"file":artifact.path,"rows":len(rows),"units":"SI; local member axes"}})
        r.add("maximum_abs_moment",max_m[0],"moment")
        r.add("maximum_abs_shear",max_v[0],"force")
        r.data["maximum_moment_location"] = {"member":max_m[1],"x":r.quantity(max_m[2],"length")}
        r.data["maximum_shear_location"] = {"member":max_v[1],"x":r.quantity(max_v[2],"length")}
        r.add("maximum_nodal_displacement",float(np.max(np.linalg.norm(u.reshape(-1,3)[:,:2],axis=1))),"length")
        if max(np.max(np.abs(u[2::3])),max(np.linalg.norm(u[e.dofs[3:5]]-u[e.dofs[:2]])/e.length for e in elements)) > .05:
            r.warnings.append("Rotation or relative displacement/length exceeds 0.05; small-displacement analysis needs review.")
        r.warnings.append("Station CSVs use SI units. Point-load jumps include left/right limits; M/V extrema include analytical stationary points.")
        return r.done("maximum_abs_moment")
    return run("frame",inputs,compute)
