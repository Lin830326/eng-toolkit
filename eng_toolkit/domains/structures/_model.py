"""Shared 2D frame model, exact static element recovery and consistent-mass modes.

Local DOFs are u_i,v_i,theta_i,u_j,v_j,theta_j. Local x runs i to j;
local y is 90 degrees counterclockwise. End actions act on the element.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np

from eng_toolkit.domains.structures._common import fields, identifier, invalid, q, records

MAX_NODES = 100
MAX_MEMBERS = 200
MAX_DOFS = 900


@dataclass(frozen=True)
class Member:
    id: str
    i: int
    j: int
    E: float
    A: float
    I: float
    release: str = "none"
    GA: float | None = None


@dataclass(frozen=True)
class Load:
    """Uniform/linear loads q0,q1, or point force q0 at a; local components."""
    kind: str
    q0: np.ndarray
    q1: np.ndarray
    a: float = 0.


@dataclass
class Model:
    ids: list[str]
    xy: np.ndarray
    members: list[Member]
    fixed: dict[int, float]
    springs: np.ndarray
    rest: np.ndarray
    nodal: np.ndarray
    loads: list[list[Load]]
    strains: list[tuple[float, float]]


@dataclass
class Element:
    member: Member
    length: float
    transform: np.ndarray
    dofs: np.ndarray
    stiffness: np.ndarray
    force: np.ndarray
    release: list[int]
    loads: list[Load]
    strain: tuple[float, float]


def parse_nodes(text, dimension=2, cap=MAX_NODES):
    ids, xyz = [], []
    for j,row in enumerate(records(text,"nodes",cap,required=True)):
        path = f"nodes/{j}"
        fields(row,(dimension+1,),path)
        name = identifier(row[0],path+"/0")
        if name in ids:
            invalid(path,"duplicate node id")
        ids.append(name)
        xyz.append([q(v,path+f"/{k+1}","length",signed=True) for k,v in enumerate(row[1:])])
    return ids,np.asarray(xyz)


def reference(name, ids, path):
    if name not in ids:
        invalid(path,f"unknown reference {name!r}; known ids: {', '.join(ids[:12])}"+(", ..." if len(ids) > 12 else ""))
    return ids.index(name)


def geometry(xy, member):
    delta = xy[member.j]-xy[member.i]
    length = float(np.linalg.norm(delta))
    if length < 1e-9:
        invalid("members/"+member.id,"zero or unresolved member length")
    if length > 1e4:
        invalid("members/"+member.id,"member length exceeds 10000 m","input.out_of_range")
    c,s = delta/length
    t = np.array([[c,s,0],[-s,c,0],[0,0,1.]])
    transform = np.zeros((6,6))
    transform[:3,:3] = transform[3:,3:] = t
    return length,transform


def parse_frame(nodes, members, supports, loads="", member_loads="", shear_properties="", thermal=""):
    ids, xy = parse_nodes(nodes)
    parsed = []
    names = []
    for j,row in enumerate(records(members,"members",MAX_MEMBERS,required=True)):
        path = f"members/{j}"
        fields(row,(6,7),path)
        name = identifier(row[0],path+"/0")
        if name in names:
            invalid(path,"duplicate member id")
        names.append(name)
        i,k = (reference(v,ids,path+f"/{n}") for n,v in enumerate(row[1:3],1))
        e,a,inertia = [q(v,path+f"/{n}",kind) for n,v,kind in zip((3,4,5),row[3:6],("stress","area","inertia"))]
        release = row[6] if len(row) == 7 else "none"
        if release not in ("none","start","end","both"):
            invalid(path+"/6","release must be none, start, end or both")
        m = Member(name,i,k,e,a,inertia,release)
        geometry(xy,m)
        parsed.append(m)
    seen = set()
    for j,row in enumerate(records(shear_properties,"shear_properties",MAX_MEMBERS)):
        path = f"shear_properties/{j}"
        fields(row,(3,),path)
        i = reference(row[0],names,path+"/0")
        if i in seen:
            invalid(path,"duplicate shear property")
        seen.add(i)
        g,area = q(row[1],path+"/1","stress"),q(row[2],path+"/2","area")
        parsed[i] = replace(parsed[i],GA=g*area)
    n = 3*len(ids)
    fixed, springs, rest = {},np.zeros(n),np.zeros(n)
    seen = set()
    types = {"fixed":(0,1,2),"pinned":(0,1),"roller_x":(0,),"roller_y":(1,)}
    for j,row in enumerate(records(supports,"supports",MAX_NODES)):
        path = f"supports/{j}"
        if len(row) < 2:
            invalid(path,"support needs node and type")
        i = reference(row[0],ids,path+"/0")
        if i in seen:
            invalid(path,"duplicate/conflicting support record")
        seen.add(i)
        kind = row[1]
        if kind == "spring":
            fields(row,(5,8),path)
            for k,unit in enumerate(("spring","spring","rotary_spring")):
                springs[3*i+k] = q(row[k+2],path+f"/{k+2}",unit,zero=True)
                if len(row) == 8:
                    rest[3*i+k] = q(row[k+5],path+f"/{k+5}","angle" if k == 2 else "length",signed=True)
        elif kind in types:
            axes = types[kind]
            fields(row,(2,2+len(axes)),path)
            for k,axis in enumerate(axes):
                fixed[3*i+axis] = 0. if len(row) == 2 else q(row[k+2],path+f"/{k+2}","angle" if axis == 2 else "length",signed=True)
        else:
            invalid(path+"/1","unknown support type")
    nodal = np.zeros(n)
    for j,row in enumerate(records(loads,"loads",400)):
        path = f"loads/{j}"
        fields(row,(4,),path)
        i = reference(row[0],ids,path+"/0")
        nodal[3*i:3*i+3] += [q(v,path+f"/{k+1}","moment" if k == 2 else "force",signed=True) for k,v in enumerate(row[1:])]
    distributed = [[] for _ in parsed]
    for j,row in enumerate(records(member_loads,"member_loads",400)):
        path = f"member_loads/{j}"
        if len(row) < 3:
            invalid(path,"expected member,uniform|linear|point,local|global,...")
        i = reference(row[0],names,path+"/0")
        kind,axes = row[1:3]
        if axes not in ("local","global"):
            invalid(path+"/2","load axes must be local or global")
        length,transform = geometry(xy,parsed[i])
        if kind == "uniform":
            fields(row,(5,),path)
            f = np.array([q(v,path+f"/{k+3}","line_load",signed=True) for k,v in enumerate(row[3:])])
            end = f.copy()
            at = 0.
        elif kind == "linear":
            fields(row,(7,),path)
            v = [q(v,path+f"/{k+3}","line_load",signed=True) for k,v in enumerate(row[3:])]
            f,end,at = np.array(v[:2]),np.array(v[2:]),0.
        elif kind == "point":
            fields(row,(6,),path)
            at = q(row[3],path+"/3","length",zero=True)
            if at > length:
                invalid(path+"/3","point load position must be on the member")
            f = np.array([q(v,path+f"/{k+4}","force",signed=True) for k,v in enumerate(row[4:])])
            end = f.copy()
        else:
            invalid(path+"/1","load type must be uniform, linear or point")
        if axes == "global":
            f,end = transform[:2,:2]@f,transform[:2,:2]@end
        distributed[i].append(Load(kind,f,end,at))
    strains = [(0.,0.) for _ in parsed]
    seen = set()
    for j,row in enumerate(records(thermal,"thermal",MAX_MEMBERS)):
        path = f"thermal/{j}"
        fields(row,(4,),path)
        i = reference(row[0],names,path+"/0")
        if i in seen:
            invalid(path,"duplicate thermal record")
        seen.add(i)
        alpha = q(row[1],path+"/1","expansion",zero=True)
        dt = q(row[2],path+"/2","temperature",signed=True)
        gradient = q(row[3],path+"/3","thermal_gradient",signed=True)
        strains[i] = (alpha*dt,-alpha*gradient)
        if max(abs(alpha*dt),abs(alpha*gradient)*geometry(xy,parsed[i])[0]) > .1:
            invalid(path,"thermal strain/rotation exceeds the small-deformation range","input.out_of_range")
    return Model(ids,xy,parsed,fixed,springs,rest,nodal,distributed,strains)


def stiffness(member, length):
    e,a,i,l = member.E,member.A,member.I,length
    phi = 0. if member.GA is None else 12*e*i/(member.GA*l*l)
    k = np.zeros((6,6))
    k[np.ix_([0,3],[0,3])] = e*a/l*np.array([[1.,-1.],[-1.,1.]])
    b = e*i/(l**3*(1+phi))*np.array([[12,6*l,-12,6*l],[6*l,(4+phi)*l*l,-6*l,(2-phi)*l*l],[-12,-6*l,12,-6*l],[6*l,(2-phi)*l*l,-6*l,(4+phi)*l*l]])
    k[np.ix_([1,2,4,5],[1,2,4,5])] = b
    return k


def mass_matrix(mass_length, length):
    l = length
    m = np.zeros((6,6))
    m[np.ix_([0,3],[0,3])] = mass_length*l/6*np.array([[2.,1.],[1.,2.]])
    m[np.ix_([1,2,4,5],[1,2,4,5])] = mass_length*l/420*np.array([[156,22*l,54,-13*l],[22*l,4*l*l,13*l,-3*l*l],[54,13*l,156,-22*l],[-13*l,-3*l*l,-22*l,4*l*l]])
    return m


def load_integrals(loads, length, x, axis, order, side="right"):
    """Integral of q(s)*(x-s)^order/order! over [0,x], including point forces."""
    total = 0.
    for load in loads:
        if load.kind == "point":
            if x > load.a or (side == "right" and x == load.a):
                total += load.q0[axis]*(x-load.a)**order/math.factorial(order)
        else:
            gradient = (load.q1[axis]-load.q0[axis])/length
            total += load.q0[axis]*x**(order+1)/math.factorial(order+1)+gradient*x**(order+2)/math.factorial(order+2)
    return total


def fixed_end_load(member, length, loads, strain):
    """Solve zero-end compatibility by integrating N,V,M. Exact for EB/Timoshenko."""
    l,ei,ea = length,member.E*member.I,member.E*member.A
    eps,kappa = strain
    # End actions at i are n0,v0,m0. Internal N=-n0-int(qx), M=-m0+v0*x+int(qy*(x-s)).
    n0 = ea*eps-load_integrals(loads,l,l,0,1)/l
    j0 = load_integrals(loads,l,l,1,0)
    j1 = load_integrals(loads,l,l,1,1)
    j2 = load_integrals(loads,l,l,1,2)
    j3 = load_integrals(loads,l,l,1,3)
    shear = 0. if member.GA is None else 1/member.GA
    mat = np.array([[l*l/(2*ei),-l/ei],[l**3/(6*ei)-l*shear,-l*l/(2*ei)]])
    rhs = np.array([-j2/ei-kappa*l,-j3/ei+shear*j1-kappa*l*l/2])
    v0,m0 = np.linalg.solve(mat,rhs)
    n1 = -n0-load_integrals(loads,l,l,0,0)
    v1 = -v0-j0
    m1 = -m0+v0*l+j1
    return -np.array([n0,v0,m0,n1,v1,m1])


def released_indices(member):
    return ([2] if member.release in ("start","both") else [])+([5] if member.release in ("end","both") else [])


def condense(k, force, releases):
    if not releases:
        return k.copy(),force.copy()
    active = [j for j in range(6) if j not in releases]
    reduced = np.zeros((6,6))
    loads = np.zeros(6)
    kar = k[np.ix_(active,releases)]
    krr = k[np.ix_(releases,releases)]
    reduced[np.ix_(active,active)] = k[np.ix_(active,active)]-kar@np.linalg.solve(krr,kar.T)
    loads[active] = force[active]-kar@np.linalg.solve(krr,force[releases])
    if len(releases) == 2:
        # A bar with both bending rotations released has no transverse stiffness.
        # Encode this exact identity instead of retaining Schur-complement roundoff.
        reduced[np.ix_([1,4],[1,4])] = 0.
    return reduced,loads


def assemble_static(model):
    n = len(model.nodal)
    k = np.zeros((n,n))
    f = model.nodal.copy()
    elements = []
    for j,member in enumerate(model.members):
        length,t = geometry(model.xy,member)
        dofs = np.array([3*member.i+d for d in range(3)]+[3*member.j+d for d in range(3)])
        local = stiffness(member,length)
        force = fixed_end_load(member,length,model.loads[j],model.strains[j])
        releases = released_indices(member)
        kc,fc = condense(local,force,releases)
        k[np.ix_(dofs,dofs)] += t.T@kc@t
        f[dofs] += t.T@fc
        elements.append(Element(member,length,t,dofs,local,force,releases,model.loads[j],model.strains[j]))
    return k,f,elements


def solve_static(k, f, fixed, labels, *, inactive_allowed=()):
    n = len(f)
    u = np.zeros(n)
    for dof,value in fixed.items():
        u[dof] = value
    free = [i for i in range(n) if i not in fixed]
    dropped = []
    for i in free[:]:
        if np.all(k[i,:] == 0) and f[i] == 0 and i in inactive_allowed:
            free.remove(i)
            dropped.append(labels[i])
    if not free:
        return u,{"stable":True,"scaled_minimum_eigenvalue":None,"inactive_dofs":dropped,"relative_residual":0.}
    block = k[np.ix_(free,free)]
    diag = np.diag(block)
    if np.any(diag <= 0):
        invalid("supports",f"mechanism or unsupported DOF: {labels[free[int(np.argmin(diag))]]}")
    scaling = 1/np.sqrt(diag)
    scaled = scaling[:,None]*block*scaling[None,:]
    eig,vec = np.linalg.eigh(scaled)
    if eig[0] <= max(1.,eig[-1])*1e-11:
        implicated = labels[free[int(np.argmax(np.abs(vec[:,0]*scaling)))]]
        invalid("supports",f"mechanism or ill-conditioned stiffness near {implicated}; scaled smallest eigenvalue {eig[0]:.3g}")
    rhs = (f-k@u)[free]
    u[free] = scaling*np.linalg.solve(scaled,scaling*rhs)
    residual = block@u[free]-rhs
    denom = np.abs(block)@np.abs(u[free])+np.abs(rhs)
    relative = float(np.max(np.abs(residual)/np.maximum(denom,1e-30)))
    if relative > 1e-7:
        invalid("members","stiffness solve failed its residual check","input.out_of_range")
    return u,{"stable":True,"scaled_minimum_eigenvalue":float(eig[0]),"inactive_dofs":dropped,"relative_residual":relative}


def recover(element, displacement):
    d = element.transform@displacement[element.dofs]
    released = element.release
    if released:
        active = [j for j in range(6) if j not in released]
        d[released] = np.linalg.solve(element.stiffness[np.ix_(released,released)],element.force[released]-element.stiffness[np.ix_(released,active)]@d[active])
    end = element.stiffness@d-element.force
    return d,end


def response(element, d, end, x, side="right"):
    member,l = element.member,element.length
    ea,ei = member.E*member.A,member.E*member.I
    n0,v0,m0 = end[:3]
    eps,kappa = element.strain
    integrals = lambda axis,order: load_integrals(element.loads,l,x,axis,order,side)
    axial = -n0-integrals(0,0)
    shear = v0+integrals(1,0)
    moment = -m0+v0*x+integrals(1,1)
    u = d[0]+eps*x+(-n0*x-integrals(0,1))/ea
    rotation = d[2]+kappa*x+(-m0*x+v0*x*x/2+integrals(1,2))/ei
    v = d[1]+d[2]*x+kappa*x*x/2+(-m0*x*x/2+v0*x**3/6+integrals(1,3))/ei
    if member.GA is not None:
        v -= (v0*x+integrals(1,1))/member.GA
    return axial,shear,moment,u,v,rotation


def extrema_stations(element, end):
    """End points, point-load sides, and real stationary M/V points within intervals."""
    l = element.length
    breaks = sorted({0.,l,*[load.a for load in element.loads if load.kind == "point"]})
    candidates = {(0.,"right"),(l,"left")}
    candidates.update((x,side) for x in breaks[1:-1] for side in ("left","right"))
    q0 = sum(load.q0[1] for load in element.loads if load.kind != "point")
    gradient = sum((load.q1[1]-load.q0[1])/l for load in element.loads if load.kind != "point")
    for a,b in zip(breaks,breaks[1:]):
        vconstant = end[1]+sum(load.q0[1] for load in element.loads if load.kind == "point" and load.a <= a)
        coefficients = [gradient*l*l/2,q0*l,vconstant]
        coeff = np.trim_zeros(coefficients,"f")
        roots = [] if len(coeff) <= 1 else np.roots(coeff)
        for root in roots:
            if abs(root.imag) < 1e-10 and a < root.real*l < b:
                candidates.add((float(root.real*l),"right"))
        if gradient and a < -q0/gradient < b:
            candidates.add((-q0/gradient,"right"))
    return sorted(candidates)


def equilibrium(xy, forces):
    nodal = forces.reshape(-1,3)
    coords = xy-xy[0]
    return np.array([sum(nodal[:,0]),sum(nodal[:,1]),sum(nodal[:,2]+coords[:,0]*nodal[:,1]-coords[:,1]*nodal[:,0])])


def refine(model, subdivisions):
    node_count = len(model.ids)+(subdivisions-1)*len(model.members)
    private = sum(len(released_indices(m)) for m in model.members)
    if 3*node_count+private > MAX_DOFS:
        invalid("elements_per_member",f"refined model exceeds {MAX_DOFS} DOFs","input.out_of_range")
    ids,xy,members,owners = list(model.ids),list(model.xy),[],[]
    for index,member in enumerate(model.members):
        chain = [member.i]
        for j in range(1,subdivisions):
            chain.append(len(xy))
            xy.append(model.xy[member.i]+(model.xy[member.j]-model.xy[member.i])*(j/subdivisions))
            ids.append(f"@{member.id}:{j}")
        chain.append(member.j)
        for j in range(subdivisions):
            start = j == 0 and member.release in ("start","both")
            end = j == subdivisions-1 and member.release in ("end","both")
            release = "both" if start and end else "start" if start else "end" if end else "none"
            members.append(replace(member,i=chain[j],j=chain[j+1],release=release))
            owners.append(index)
    return ids,np.array(xy),members,owners


def rigid_mode_count(xy, members, fixed, springs, active):
    """Project each connected frame's analytic translations/rotation onto restraints."""
    adjacent = [set() for _ in xy]
    for member in members:
        adjacent[member.i].add(member.j)
        adjacent[member.j].add(member.i)
    unseen = set(range(len(xy)))
    zeros = 0
    while unseen:
        component, pending = [],[unseen.pop()]
        while pending:
            node = pending.pop()
            component.append(node)
            neighbors = adjacent[node] & unseen
            unseen.difference_update(neighbors)
            pending.extend(neighbors)
        coords = xy[component]-xy[component[0]]
        span = max(float(np.max(np.linalg.norm(coords,axis=1))),1e-9)
        rigid = np.zeros((3*len(component),3))
        rigid[0::3,0] = rigid[1::3,1] = 1.
        rigid[0::3,2],rigid[1::3,2],rigid[2::3,2] = -coords[:,1]/span,coords[:,0]/span,1/span
        dofs = [3*node+j for node in component for j in range(3)]
        restrained = [j for j,dof in enumerate(dofs) if dof in fixed or (dof < len(springs) and springs[dof] > 0)]
        rows = rigid[restrained]
        norms = np.linalg.norm(rows,axis=1)
        rows = rows[norms > 0]/norms[norms > 0,None]
        _,singular,vt = np.linalg.svd(rows,full_matrices=True)
        tolerance = 8*np.finfo(float).eps*max(rows.shape)*(singular[0] if len(singular) else 1.)
        if np.any((singular > tolerance) & (singular < 100*tolerance)):
            invalid("supports","rigid-body restraint rank cannot be resolved reliably","input.out_of_range")
        rank = int(np.count_nonzero(singular > tolerance))
        unrestrained = rigid@vt[rank:].T
        dynamic = [j for j,dof in enumerate(dofs) if dof in active and dof not in fixed]
        if dynamic and rank < 3:
            zeros += int(np.linalg.matrix_rank(unrestrained[dynamic]))
    return zeros


def assemble_modal(model, subdivisions, mass_lengths, lumped):
    ids,xy,members,owners = refine(model,subdivisions)
    joint_dofs = 3*len(ids)
    size = joint_dofs+sum(len(released_indices(m)) for m in members)
    k,mass = np.zeros((size,size)),np.zeros((size,size))
    next_private = joint_dofs
    private_labels = []
    compatibility, weights = [],[]
    for member,owner in zip(members,owners):
        length,t = geometry(xy,member)
        dofs = [3*member.i+j for j in range(3)]+[3*member.j+j for j in range(3)]
        for released in released_indices(member):
            dofs[released] = next_private
            private_labels.append(f"{member.id}:{'start' if released == 2 else 'end'} rotation")
            next_private += 1
        k[np.ix_(dofs,dofs)] += t.T@stiffness(member,length)@t
        mass[np.ix_(dofs,dofs)] += t.T@mass_matrix(mass_lengths[owner],length)@t
        # Exact EB energy: EA/L*du^2 + EI/L*dtheta^2
        # + 12*EI/L^3*(dv-L*(theta_i+theta_j)/2)^2.
        local = np.array([[-1.,0,0,1,0,0], [0,0,-1,0,0,1], [0,-1,-length/2,0,1,-length/2]])
        block = np.zeros((3,size))
        block[:,dofs] = local@t
        compatibility.extend(block)
        weights.extend((member.E*member.A/length,member.E*member.I/length,12*member.E*member.I/length**3))
    k[np.arange(len(model.springs)),np.arange(len(model.springs))] += model.springs
    mass[np.arange(len(lumped)),np.arange(len(lumped))] += lumped
    for dof in np.flatnonzero(model.springs):
        row = np.zeros(size)
        row[dof] = 1.
        compatibility.append(row)
        weights.append(model.springs[dof])
    active = {j for j in range(size) if np.any(k[j]) or np.any(mass[j])}
    rigid_zeros = None if private_labels else rigid_mode_count(xy,members,model.fixed,model.springs,active)
    return ids,xy,k,mass,private_labels,(np.asarray(compatibility),np.asarray(weights),rigid_zeros)


def modes(k, mass, fixed, requested, strain):
    free = [j for j in range(len(k)) if j not in fixed and (np.any(k[j] != 0) or np.any(mass[j] != 0))]
    if not free:
        invalid("supports","no dynamic degrees of freedom remain")
    kk,mm = k[np.ix_(free,free)],mass[np.ix_(free,free)]
    if np.any(np.diag(mm) <= 0):
        invalid("nodal_masses","active massless DOF; supply positive distributed/nodal mass")
    scale = 1/np.sqrt(np.diag(mm))
    ms = scale[:,None]*mm*scale[None,:]
    try:
        chol = np.linalg.cholesky(ms)
    except np.linalg.LinAlgError:
        invalid("density","mass matrix is not positive definite")
    compatibility,weights,rigid_zeros = strain
    constraints = compatibility[:,free]
    norms = np.linalg.norm(constraints,axis=1)
    active = norms > 0
    geometric = constraints[active]/norms[active,None]
    # Rank uses only geometry and the presence of a restraint, never E, I or
    # spring magnitude. Three strain-free motions per planar element remain
    # subject to joint compatibility and supports, including private hinges.
    singular = np.linalg.svd(geometric,compute_uv=False)
    rank_tol = 8*np.finfo(float).eps*max(geometric.shape)*singular[0] if len(singular) else 0.
    if np.any((singular > rank_tol) & (singular < 100*rank_tol)):
        invalid("members","geometric constraint rank cannot be resolved reliably","input.out_of_range")
    rank = int(np.count_nonzero(singular > rank_tol))
    zero_count = len(free)-rank
    if rigid_zeros is not None and zero_count != rigid_zeros:
        invalid("members","kinematic rank disagrees with the restrained rigid-body motions; geometry is numerically unresolved","input.out_of_range")
    factor = np.sqrt(weights[:,None])*constraints*scale[None,:]
    whitened = np.linalg.solve(chol,factor.T).T
    # SVD avoids squaring the condition number by forming L^-1*K*L^-T.
    _,omega,vt = np.linalg.svd(whitened,full_matrices=True)
    if rank and omega[rank-1] <= 8*np.finfo(float).eps*max(whitened.shape)*omega[0]:
        invalid("members","positive modal frequencies cannot be resolved reliably; reduce stiffness or mass contrast","input.out_of_range")
    eigenvalues = np.zeros(len(free))
    eigenvalues[zero_count:] = omega[:rank][::-1]**2
    y = vt[::-1].T
    n = min(requested,len(eigenvalues))
    phi = np.zeros((len(k),n))
    phi[free] = scale[:,None]*np.linalg.solve(chol.T,y[:,:n])
    for j in range(n):
        pivot = int(np.argmax(np.abs(phi[:,j])))
        if phi[pivot,j] < 0:
            phi[:,j] *= -1
    residuals = []
    for j in range(n):
        p = phi[free,j]
        residual = kk@p-eigenvalues[j]*(mm@p)
        denominator = np.linalg.norm(kk,ord=np.inf)*np.linalg.norm(p)+eigenvalues[j]*np.linalg.norm(mm@p)
        residuals.append(float(np.linalg.norm(residual)/max(denominator,1e-30)))
    if max(residuals) > 1e-7:
        invalid("members","modal solution failed its residual check","input.out_of_range")
    return eigenvalues[:n],phi,zero_count,residuals
