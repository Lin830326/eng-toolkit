"""Consistent-mass undamped frame modes, without scipy at runtime."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures._common import FrameMembers, FrameSupports, Nodes2, Result, System, count, csv_text, fields, invalid, q, records, run
from eng_toolkit.domains.structures._model import assemble_modal, geometry, modes, parse_frame, reference
from eng_toolkit.domains.structures._server import server


@server.tool(description="Undamped 2D Euler-Bernoulli frame modes with consistent mass and a strain-energy SVD. Frequencies, effective mass fractions and mode CSV; shared frame supports/releases. Refine 1..20 elements/member (default 4), <=900 DOFs. Linear unstressed model; zero modes determined from kinematics.")
def modal(
    nodes: Nodes2 = "", members: FrameMembers = "", supports: FrameSupports = "",
    density: Annotated[str, Field(description="Default material mass/volume, e.g. '7800 kg/m^3'; multiplied by member area; '' omits; conflicts with mass_per_length.")] = "",
    mass_per_length: Annotated[str, Field(description="Default member mass/length, e.g. '10 kg/m'; '' omits; use this, density or member_masses for every member.")] = "",
    member_masses: Annotated[str, Field(description="Distributed-mass overrides 'member,mass/length', e.g. 'e,10 kg/m'; '' omits; required for every member lacking a default.")] = "",
    nodal_masses: Annotated[str, Field(description="Added joint masses 'node,m,J' or 'node,mx,my,J', e.g. 'b,10 kg,0 kg*m^2'; masses nonnegative; '' omits.")] = "",
    modes_count: Annotated[int, Field(description="Number of lowest modes requested, including zero modes, e.g. 6; range 1..30; limited by available dynamic DOFs.")] = 6,
    elements_per_member: Annotated[int, Field(description="Equal finite elements per input member, e.g. 4; range 1..20 and <=900 total DOFs; increase to check frequency convergence.")] = 4,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        subdivisions = count(elements_per_member,"elements_per_member",1,20)
        requested = count(modes_count,"modes_count",1,30)
        model = parse_frame(nodes,members,supports)
        rho = q(density,"density","density",optional=True)
        mu = q(mass_per_length,"mass_per_length","mass_length",optional=True)
        if rho is not None and mu is not None:
            invalid("mass_per_length","supply density or mass_per_length, not both")
        mass_lengths = [mu if mu is not None else rho*m.A if rho is not None else None for m in model.members]
        seen = set()
        names = [m.id for m in model.members]
        for j,row in enumerate(records(member_masses,"member_masses",200)):
            path = f"member_masses/{j}"
            fields(row,(2,),path)
            i = reference(row[0],names,path+"/0")
            if i in seen:
                invalid(path,"duplicate member mass")
            seen.add(i)
            mass_lengths[i] = q(row[1],path+"/1","mass_length")
        if any(x is None for x in mass_lengths):
            invalid("density","every member needs positive distributed mass from density, mass_per_length or member_masses")
        lumped = np.zeros(len(model.nodal))
        seen = set()
        for j,row in enumerate(records(nodal_masses,"nodal_masses",100)):
            path = f"nodal_masses/{j}"
            fields(row,(3,4),path)
            i = reference(row[0],model.ids,path+"/0")
            if i in seen:
                invalid(path,"duplicate nodal mass")
            seen.add(i)
            if len(row) == 3:
                mass = q(row[1],path+"/1","mass",zero=True)
                lumped[3*i:3*i+3] = [mass,mass,q(row[2],path+"/2","mass_inertia",zero=True)]
            else:
                lumped[3*i:3*i+3] = [q(v,path+f"/{k+1}","mass_inertia" if k == 2 else "mass",zero=True) for k,v in enumerate(row[1:])]
        ids,xy,k,mass,private,strain = assemble_modal(model,subdivisions,mass_lengths,lumped)
        values,phi,zeros,residuals = modes(k,mass,model.fixed,requested,strain)
        r = Result(unit_system, {
            "eigenproblem":"K=C^T*W*C; M=L*L^T after scaling; SVD(sqrt(W)*C*L^-T); omega=singular values; zeros from kinematic constraint rank",
            "consistent_mass":"axial mu*L/6*[[2,1],[1,2]]; bending mu*L/420*[[156,22L,54,-13L],[22L,4L^2,13L,-3L^2],[54,13L,156,-22L],[-13L,-3L^2,-22L,4L^2]]",
            "effective_mass":"fraction_j=(phi_j^T*M*r)^2/((phi_j^T*M*phi_j)*(r^T*M*r)); r=unit global translation at unrestrained joint DOFs",
            "uniform_beam":"omega_n=beta_n^2*sqrt(EI/(mu*L^4)); cantilever beta=1.87510407,4.69409113,7.85475744; simply supported beta=n*pi",
        }, "Undamped, linear unstressed Euler-Bernoulli members, consistent translational inertia without cross-section rotary inertia. Optional nodal rotary inertia. Pin-release rotations are private DOFs, not Guyan-condensed mass. Springs add stiffness. Settlements do not change this linear perturbation eigenproblem.",
                   "100 original nodes, 200 members, 1..20 elements/member and <=900 total DOFs including private rotations. Request 1..30 modes. Positive distributed mass required on every member. No damping, prestress, geometric stiffness or forced response.")
        if np.any(model.rest) or any(model.fixed.values()):
            r.warnings.append("Support settlements/rest positions are ignored in unstressed linear modes; stiffness is unchanged.")
        r.data["convergence"] = f"Each original member has {subdivisions} elements. Increase elements_per_member and compare frequencies; one cubic element is coarse for higher modes."
        r.data["zero_mode_count"] = zeros
        r.data["private_release_rotations"] = private
        r.data["eigen_residuals"] = residuals
        if zeros:
            r.warnings.append(f"{zeros} zero modes detected. A connected free-free frame has three rigid-body modes; other zero modes can indicate mechanisms or disconnected parts.")
        directions = []
        for axis in (0,1):
            influence = np.zeros(len(k))
            influence[axis:3*len(ids):3] = 1.
            influence[list(model.fixed)] = 0.
            directions.append(influence)
        r.data["modes"] = []
        rows = []
        for j,eigenvalue in enumerate(values):
            omega = math.sqrt(eigenvalue)
            fractions = []
            for influence in directions:
                total = float(influence@mass@influence)
                fractions.append(float((phi[:,j]@mass@influence)**2/total) if total > 0 else 0.)
            r.data["modes"].append({"mode":j+1,"frequency":r.quantity(omega/(2*math.pi),"frequency"),"omega":r.quantity(omega,"omega"),"effective_mass_fraction_x":fractions[0],"effective_mass_fraction_y":fractions[1]})
            for i,name in enumerate(ids):
                rows.append([j+1,name,*phi[3*i:3*i+3,j].tolist()])
            for i,name in enumerate(private):
                rows.append([j+1,"@release:"+name,0.,0.,float(phi[3*len(ids)+i,j])])
        artifact = handle.write_text("mode_shapes.csv",csv_text(["mode","node","ux_mass_normalized","uy_mass_normalized","rotation_mass_normalized"],rows),fmt="csv")
        r.artifacts.append(artifact)
        r.data["mode_shape_table"] = {"file":artifact.path,"rows":len(rows),"normalization":"phi^T M phi=1 with SI M; columns are modal coefficients, not physical vibration amplitudes; sign set by largest coefficient"}
        r.add("first_frequency",math.sqrt(values[0])/(2*math.pi),"frequency")
        for j,eigenvalue in enumerate(values):
            r.add(f"frequency_mode_{j+1}",math.sqrt(eigenvalue)/(2*math.pi),"frequency")
        r.add("model_dofs",len(k))
        if len(model.members) == 1 and not np.any(model.springs) and not np.any(lumped):
            member = model.members[0]
            length,transform = geometry(model.xy,member)
            dofi,dofj = set(range(3*member.i,3*member.i+3)),set(range(3*member.j,3*member.j+3))
            fixed = set(model.fixed)
            beta = None
            label = ""
            if member.release == "none" and (fixed == dofi or fixed == dofj):
                beta = [1.875104068711961,4.694091132974174,7.854757438237612]
                label = "cantilever"
            # Full translations restrained at both ends also satisfy simple transverse supports.
            # For axis-aligned beams, allow one axial roller and one pinned end.
            transverse = 1 if abs(transform[0,0]) > 1-1e-12 else 0 if abs(transform[0,1]) > 1-1e-12 else None
            rot_free = all(3*n+2 not in fixed or member.release in ("both","start" if n == member.i else "end") for n in (member.i,member.j))
            if rot_free and (({3*member.i,3*member.i+1,3*member.j,3*member.j+1} <= fixed) or (transverse is not None and {3*member.i+transverse,3*member.j+transverse} <= fixed)):
                beta,label = [math.pi*n for n in (1,2,3)],"simply supported"
            if beta is not None:
                r.data["closed_form_comparison"] = {"boundary":label,"bending_frequencies_hz":[b*b*math.sqrt(member.E*member.I/(mass_lengths[0]*length**4))/(2*math.pi) for b in beta],"note":"Bending-only frequencies; computed frame modes can interleave axial modes."}
        return r.done("first_frequency")
    return run("modal",inputs,compute)
