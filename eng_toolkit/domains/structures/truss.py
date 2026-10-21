"""2D/3D pin-jointed trusses by the direct stiffness method."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures._common import Result, System, count, fields, identifier, invalid, number, q, records, run
from eng_toolkit.domains.structures._model import parse_nodes, reference, solve_static
from eng_toolkit.domains.structures._server import server


@server.tool(description="2D/3D pin-jointed truss direct stiffness, thermal initial strain, nodal displacement/reactions, axial tension/compression, stress and elongation. Reports equilibrium residual and refuses mechanisms. Optional member I gives pinned Euler load. Linear small displacement, axial bars only; <=200 nodes/400 members.")
def truss(
    nodes: Annotated[str, Field(description="Required nodes 'id,x,y[,z]' with lengths, e.g. 'a,0 m,0 m;b,2 m,0 m'; <=200, semicolon/newline separators.")] = "",
    members: Annotated[str, Field(description="Required bars 'id,node_i,node_j,E,A[,I]', e.g. 'e,a,b,200 GPa,100 mm^2'; I enables Euler compression checks; <=400.")] = "",
    supports: Annotated[str, Field(description="Restrained global directions 'node,axes', e.g. 'a,xy;b,y'; unique x/y/z letters, <=200; '' omits but model must be stable.")] = "",
    loads: Annotated[str, Field(description="Joint forces 'node,Fx,Fy[,Fz]', e.g. 'b,10 kN,0 N'; '' omits loads; <=400 records, repeated loads add.")] = "",
    dimension: Annotated[int, Field(description="Spatial dimension, 2 or 3; node/load rows must have that many components.")] = 2,
    thermal: Annotated[str, Field(description="Member temperature/expansion overrides 'member,dT,alpha', e.g. 'e,100 K,0.00001 1/K'; '' omits overrides.")] = "",
    temperature_change: Annotated[str, Field(description="Default member temperature difference, e.g. '50 K'; requires thermal_expansion; '' omits; absolute offset units refused.")] = "",
    thermal_expansion: Annotated[str, Field(description="Default linear thermal expansion per temperature, e.g. '0.00001 1/K'; requires temperature_change; '' omits.")] = "",
    buckling_factor: Annotated[float, Field(description="Dimensionless effective-length K for Euler checks of members with I, e.g. 1.0; range 0.1..10.")] = 1.,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(_):
        dim = count(dimension,"dimension",2,3)
        ids,coords = parse_nodes(nodes,dim,cap=200)
        kfactor = number(buckling_factor,"buckling_factor",.1,10)
        dt = q(temperature_change,"temperature_change","temperature",signed=True,optional=True)
        alpha = q(thermal_expansion,"thermal_expansion","expansion",zero=True,optional=True)
        if (dt is None) != (alpha is None):
            invalid("temperature_change","temperature change and expansion coefficient are required together")
        default_strain = 0. if dt is None else dt*alpha
        parsed,names = [],[]
        for j,row in enumerate(records(members,"members",400,required=True)):
            path = f"members/{j}"
            fields(row,(5,6),path)
            name = identifier(row[0],path+"/0")
            if name in names:
                invalid(path,"duplicate member id")
            names.append(name)
            i,k = [reference(v,ids,path+f"/{n}") for n,v in enumerate(row[1:3],1)]
            e,a = q(row[3],path+"/3","stress"),q(row[4],path+"/4","area")
            inertia = q(row[5],path+"/5","inertia") if len(row) == 6 else None
            delta = coords[k]-coords[i]
            length = float(np.linalg.norm(delta))
            if length < 1e-9:
                invalid(path,"zero or unresolved member length")
            if length > 1e4:
                invalid(path,"member length exceeds 10000 m","input.out_of_range")
            parsed.append((name,i,k,e,a,inertia,length,delta/length))
        strains = [default_strain]*len(parsed)
        seen = set()
        for j,row in enumerate(records(thermal,"thermal",400)):
            path = f"thermal/{j}"
            fields(row,(3,),path)
            i = reference(row[0],names,path+"/0")
            if i in seen:
                invalid(path,"duplicate thermal member")
            seen.add(i)
            strains[i] = q(row[1],path+"/1","temperature",signed=True)*q(row[2],path+"/2","expansion",zero=True)
        if any(abs(x) > .1 for x in strains):
            invalid("thermal","initial strain exceeds 0.1","input.out_of_range")
        size = dim*len(ids)
        stiffness,force = np.zeros((size,size)),np.zeros(size)
        fixed = {}
        seen = set()
        for j,row in enumerate(records(supports,"supports",200)):
            path = f"supports/{j}"
            fields(row,(2,),path)
            i = reference(row[0],ids,path+"/0")
            axes = row[1]
            if not axes or any(ch not in "xyz"[:dim] for ch in axes) or len(set(axes)) != len(axes):
                invalid(path+"/1","fixed directions must be unique x/y/z letters valid for the dimension")
            if i in seen:
                invalid(path,"duplicate support record")
            seen.add(i)
            for axis in axes:
                fixed[dim*i+"xyz".index(axis)] = 0.
        external = np.zeros(size)
        for j,row in enumerate(records(loads,"loads",400)):
            path = f"loads/{j}"
            fields(row,(dim+1,),path)
            i = reference(row[0],ids,path+"/0")
            external[dim*i:dim*i+dim] += [q(v,path+f"/{k+1}","force",signed=True) for k,v in enumerate(row[1:])]
        force += external
        element_data = []
        for member,strain in zip(parsed,strains):
            name,i,j,e,a,inertia,length,direction = member
            dofs = np.array(list(range(dim*i,dim*i+dim))+list(range(dim*j,dim*j+dim)))
            b = np.concatenate((-direction,direction))
            local = e*a/length*np.outer(b,b)
            stiffness[np.ix_(dofs,dofs)] += local
            force[dofs] += e*a*strain*b
            element_data.append((dofs,b))
        labels = [f"{name}/{axis}" for name in ids for axis in "xyz"[:dim]]
        u,stability = solve_static(stiffness,force,fixed,labels)
        residual = stiffness@u-force
        reactions = np.zeros(size)
        for dof in fixed:
            reactions[dof] = residual[dof]
        r = Result(unit_system, {"direct_stiffness":"b=[-n,n]; ke=EA/L*b*b^T; thermal equivalent load=EA*alpha*dT*b; solve free DOFs after support elimination", "axial":"extension=b^T*u; N=EA*(extension/L-alpha*dT), tension positive; stress=N/A", "Euler":"Pcr=pi^2*E*I/(K*L)^2 for compression members with I supplied"},
                   "Straight pin-jointed axial members, small displacement and linear elastic material. No member bending, self-weight or automatic imperfection model. Euler compression check assumes supplied I about the relevant buckling axis and default K=1.",
                   "2D/3D; <=200 nodes, <=400 members and load records; text <=60000 characters. Nonzero member lengths <=10000 m. Mechanisms and unresolved ill-conditioning are refused. No second-order or design-code capacity check.")
        r.data["stability"] = stability
        r.data["nodes"] = [{"id":name,"displacement":{axis:r.quantity(u[dim*i+j],"length") for j,axis in enumerate("xyz"[:dim])},"reaction":{axis:r.quantity(reactions[dim*i+j],"force") for j,axis in enumerate("xyz"[:dim])}} for i,name in enumerate(ids)]
        r.data["members"] = []
        for member,strain,(dofs,b) in zip(parsed,strains,element_data):
            name,i,j,e,a,inertia,length,direction = member
            extension = float(b@u[dofs])
            axial = e*a*(extension/length-strain)
            if max(abs(extension/length),abs(axial/(e*a))) > .05:
                r.warnings.append(f"Member {name} has elongation/length or elastic strain above 0.05; small-displacement linear analysis needs review.")
            entry = {"id":name,"axial_force":r.quantity(axial,"force"),"stress":r.quantity(axial/a,"stress"),"elongation":r.quantity(extension,"length"),"elastic_elongation":r.quantity(axial*length/(e*a),"length"),"state":"tension" if axial > 1e-10 else "compression" if axial < -1e-10 else "zero"}
            if inertia is not None and axial < 0:
                critical = math.pi**2*e*inertia/(kfactor*length)**2
                entry["euler_critical_load"] = r.quantity(critical,"force")
                entry["euler_safety_factor"] = critical/abs(axial)
                if abs(axial) >= critical:
                    r.warnings.append(f"Member {name} exceeds its ideal Euler compression load.")
            r.data["members"].append(entry)
        applied = (external+reactions).reshape(-1,dim)
        r.data["equilibrium_residual"] = {axis:r.quantity(float(sum(applied[:,i])),"force") for i,axis in enumerate("xyz"[:dim])}
        position = coords-coords[0]
        if dim == 2:
            moment = sum(position[:,0]*applied[:,1]-position[:,1]*applied[:,0])
            r.data["equilibrium_residual"]["Mz"] = r.quantity(moment,"moment")
        else:
            moments = np.sum(np.cross(position,applied),axis=0)
            r.data["equilibrium_residual"].update({"M"+axis:r.quantity(moments[j],"moment") for j,axis in enumerate("xyz")})
        r.add("maximum_displacement",float(np.max(np.linalg.norm(u.reshape(-1,dim),axis=1))),"length")
        return r.done("maximum_displacement")
    return run("truss",inputs,compute)
