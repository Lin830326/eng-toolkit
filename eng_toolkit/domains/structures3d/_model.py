"""Space-frame records, element operators, kinematics and static recovery.

Local DOFs: ux,uy,uz,rx,ry,rz at i then j. End forces act on the element.
Private local rotations retain released-end inertia for spectral analyses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from ._common import choice, fields, identifier, invalid, q, records

MAX_DOFS = 1200
MAX_CONDITION_ERROR = 1e-8
AXES = ("ux", "uy", "uz", "rx", "ry", "rz")


@dataclass
class Member:
    id: str
    i: int
    j: int
    E: float
    G: float
    A: float
    Iy: float
    Iz: float
    J: float
    R: np.ndarray
    Asy: float | None = None
    Asz: float | None = None
    releases: tuple[int, ...] = ()


@dataclass(frozen=True)
class Load:
    kind: str
    vector: np.ndarray
    a: float = 0.


@dataclass
class Model:
    ids: list[str]
    xyz: np.ndarray
    members: list[Member]
    fixed: dict[int, float]
    springs: np.ndarray
    rest: np.ndarray
    nodal: np.ndarray
    loads: list[list[Load]]
    strains: np.ndarray


@dataclass
class Element:
    member: Member
    owner: int
    length: float
    start: float
    dofs: np.ndarray
    transform: np.ndarray
    stiffness: np.ndarray
    force: np.ndarray
    loads: list[Load]
    strain: float
    i: int
    j: int
    private: dict[int, int] = field(default_factory=dict)


@dataclass
class Mesh:
    ids: list[str]
    xyz: np.ndarray
    elements: list[Element]
    k: np.ndarray
    f: np.ndarray
    c: np.ndarray
    fixed: dict[int, float]
    springs: np.ndarray
    rest: np.ndarray
    labels: list[str]
    model: Model
    coordinate_transform: np.ndarray


def global_coordinates(mesh, vector):
    """Map internal joint-rotation coordinates to global axes; private DOFs stay local."""
    return mesh.coordinate_transform @ vector


def reference(raw, names, path):
    if raw not in names:
        invalid(path, f"unknown reference {raw!r}")
    return names.index(raw)


def orientation(delta, path, reference_y=None, roll=0.):
    length = float(np.linalg.norm(delta))
    if length < 1e-9 or length > 2e4:
        invalid(path, "member length must be between 1e-9 and 20000 m", "input.out_of_range")
    x = delta / length
    if reference_y is None:
        reference_y = np.array([0., 1., 0.]) if abs(x[1]) < .9 else np.array([0., 0., 1.])
    norm = float(np.linalg.norm(reference_y))
    if norm == 0:
        invalid(path, "orientation reference vector cannot be zero")
    y = reference_y / norm
    y -= x * (x @ y)
    transverse = float(np.linalg.norm(y))
    if transverse < 1e-8:
        invalid(path, "orientation reference must not be collinear or nearly collinear with the member")
    y /= transverse
    z = np.cross(x, y)
    return np.array([x, math.cos(roll)*y + math.sin(roll)*z,
                     -math.sin(roll)*y + math.cos(roll)*z])


def direction_literals(values, path):
    result = []
    for i, value in enumerate(values):
        if len(value) > 32:
            invalid(path, "orientation components are bounded numeric literals", "input.out_of_range")
        try:
            x = float(value)
        except ValueError:
            invalid(f"{path}/{i}", "orientation components must be finite numeric literals")
        if not math.isfinite(x) or abs(x) > 1e6 or (x != 0 and abs(x) < 1e-12):
            invalid(f"{path}/{i}", "orientation component magnitude outside 1e-12..1e6", "input.out_of_range")
        if x == 0 and any(c in "123456789" for c in value.lower().split("e")[0]):
            invalid(f"{path}/{i}", "orientation component underflows", "input.out_of_range")
        result.append(x)
    return np.array(result)


def parse_frame(nodes, members, supports="", loads="", member_loads="", orientations="", releases="", shear_properties="", thermal=""):
    ids, xyz = [], []
    for i, row in enumerate(records(nodes, "nodes", 200, required=True)):
        path = f"nodes/{i}"
        fields(row, (4,), path)
        name = identifier(row[0], path + "/0")
        if name in ids:
            invalid(path + "/0", "duplicate node id")
        ids.append(name)
        xyz.append([q(v, f"{path}/{j+1}", "length", signed=True, zero=True) for j, v in enumerate(row[1:])])
    xyz = np.array(xyz)
    member_ids, parsed, embedded_orientation = [], [], set()
    for i, row in enumerate(records(members, "members", 400, required=True)):
        path = f"members/{i}"
        fields(row, (9, 10, 12), path)
        name = identifier(row[0], path + "/0")
        if name in member_ids:
            invalid(path + "/0", "duplicate member id")
        ni, nj = [reference(row[j], ids, path + f"/{j}") for j in (1, 2)]
        props = [q(row[j], path + f"/{j}", kind) for j, kind in enumerate(("stress", "stress", "area", "inertia", "inertia", "inertia"), 3)]
        vector = direction_literals(row[9:], path + "/9") if len(row) == 12 else None
        roll = q(row[9], path + "/9", "angle", signed=True, zero=True) if len(row) == 10 else 0.
        r = orientation(xyz[nj] - xyz[ni], path, vector, roll)
        parsed.append(Member(name, ni, nj, *props, r))
        member_ids.append(name)
        if len(row) > 9:
            embedded_orientation.add(i)
    used_nodes = {n for m in parsed for n in (m.i, m.j)}
    if len(used_nodes) != len(ids):
        invalid("nodes", "every node must belong to a member")
    seen = set(embedded_orientation)
    for i, row in enumerate(records(orientations, "orientations", 400)):
        path = f"orientations/{i}"
        fields(row, (3, 5), path)
        owner = reference(row[0], member_ids, path + "/0")
        if owner in seen:
            invalid(path, "duplicate orientation, including optional member fields")
        seen.add(owner)
        choice(row[1], path + "/1", ("roll", "vector"))
        if (row[1] == "roll") != (len(row) == 3):
            invalid(path, "roll requires one angle; vector requires three numeric components")
        vector = direction_literals(row[2:], path + "/2") if row[1] == "vector" else None
        angle = q(row[2], path + "/2", "angle", signed=True, zero=True) if row[1] == "roll" else 0.
        m = parsed[owner]
        m.R = orientation(xyz[m.j] - xyz[m.i], path, vector, angle)
    seen = set()
    for i, row in enumerate(records(shear_properties, "shear_properties", 400)):
        path = f"shear_properties/{i}"
        fields(row, (3,), path)
        owner = reference(row[0], member_ids, path + "/0")
        if owner in seen:
            invalid(path, "duplicate shear areas")
        seen.add(owner)
        parsed[owner].Asy, parsed[owner].Asz = [q(row[j], path + f"/{j}", "area") for j in (1, 2)]
    seen = set()
    for i, row in enumerate(records(releases, "releases", 800)):
        path = f"releases/{i}"
        fields(row, (3,), path)
        owner = reference(row[0], member_ids, path + "/0")
        end = choice(row[1], path + "/1", ("start", "end"))
        axes = row[2].split("+")
        if len(set(axes)) != len(axes) or not set(axes) <= {"rx", "ry", "rz"}:
            invalid(path + "/2", "release requires distinct local rx, ry and/or rz")
        offsets = [AXES.index(axis) + (6 if end == "end" else 0) for axis in axes]
        for offset in offsets:
            if (owner, offset) in seen:
                invalid(path, "duplicate end release")
            seen.add((owner, offset))
        parsed[owner].releases += tuple(offsets)
    size = 6 * len(ids)
    fixed, springs, rest = {}, np.zeros(size), np.zeros(size)
    assigned = set()
    for i, row in enumerate(records(supports, "supports", 1200)):
        path = f"supports/{i}"
        fields(row, (2, 4, 5), path)
        node = reference(row[0], ids, path + "/0")
        if row[1] in ("spring", "settlement"):
            fields(row, (4, 5) if row[1] == "spring" else (4,), path)
            axis = choice(row[2], path + "/2", AXES)
            dof = 6*node + AXES.index(axis)
            if dof in assigned:
                invalid(path, "conflicting or duplicate support on this DOF")
            assigned.add(dof)
            if row[1] == "spring":
                springs[dof] = q(row[3], path + "/3", "spring" if axis[0] == "u" else "rotary_spring")
                rest[dof] = q(row[4], path + "/4", "length" if axis[0] == "u" else "angle", signed=True, zero=True) if len(row) == 5 else 0.
            else:
                fields(row, (4,), path)
                fixed[dof] = q(row[3], path + "/3", "length" if axis[0] == "u" else "angle", signed=True, zero=True)
        else:
            fields(row, (2,), path)
            aliases = {"fixed": AXES, "pinned": AXES[:3], "roller_x": ("ux",), "roller_y": ("uy",), "roller_z": ("uz",)}
            axes = aliases.get(row[1], row[1].split("+"))
            if not set(axes) <= set(AXES) or len(set(axes)) != len(axes):
                invalid(path + "/1", "expected fixed, pinned, roller_x/y/z or distinct ux+uy+uz+rx+ry+rz")
            for axis in axes:
                dof = 6*node + AXES.index(axis)
                if dof in assigned:
                    invalid(path, "conflicting or duplicate support on this DOF")
                assigned.add(dof)
                fixed[dof] = 0.
    nodal = np.zeros(size)
    for i, row in enumerate(records(loads, "loads", 800)):
        path = f"loads/{i}"
        fields(row, (7,), path)
        node = reference(row[0], ids, path + "/0")
        nodal[6*node:6*node+6] += [q(v, f"{path}/{j+1}", "force" if j < 3 else "moment", signed=True, zero=True) for j, v in enumerate(row[1:])]
    distributed = [[] for _ in parsed]
    for i, row in enumerate(records(member_loads, "member_loads", 800)):
        path = f"member_loads/{i}"
        fields(row, (6, 7), path)
        owner = reference(row[0], member_ids, path + "/0")
        kind = choice(row[1], path + "/1", ("uniform", "point"))
        space = choice(row[2], path + "/2", ("local", "global"))
        fields(row, (6 if kind == "uniform" else 7,), path)
        a = q(row[3], path + "/3", "length", zero=True) if kind == "point" else 0.
        m = parsed[owner]
        length = float(np.linalg.norm(xyz[m.j] - xyz[m.i]))
        if a > length:
            invalid(path + "/3", "point load position lies outside the member")
        first = 3 if kind == "uniform" else 4
        values = np.array([q(row[j], path + f"/{j}", "line_load" if kind == "uniform" else "force", signed=True, zero=True) for j in range(first, first+3)])
        distributed[owner].append(Load(kind, values if space == "local" else m.R @ values, a))
    strains = np.zeros(len(parsed))
    seen = set()
    for i, row in enumerate(records(thermal, "thermal", 400)):
        path = f"thermal/{i}"
        fields(row, (3,), path)
        owner = reference(row[0], member_ids, path + "/0")
        if owner in seen:
            invalid(path, "duplicate member thermal strain")
        seen.add(owner)
        alpha = q(row[1], path + "/1", "expansion", signed=True, zero=True)
        delta = q(row[2], path + "/2", "temperature", signed=True, zero=True)
        strains[owner] = alpha * delta
        if abs(strains[owner]) > .05:
            invalid(path, "thermal strain exceeds linear small-strain range 0.05", "input.out_of_range")
    return Model(ids, xyz, parsed, fixed, springs, rest, nodal, distributed, strains)


def bending_stiffness(ei, length, ga=None):
    l = length
    phi = 0. if ga is None else 12*ei/(ga*l*l)
    return ei/(l**3*(1+phi))*np.array([
        [12, 6*l, -12, 6*l], [6*l, (4+phi)*l*l, -6*l, (2-phi)*l*l],
        [-12, -6*l, 12, -6*l], [6*l, (2-phi)*l*l, -6*l, (4+phi)*l*l]])


def stiffness(member, length):
    m, l = member, length
    k = np.zeros((12, 12))
    for indices, coefficient in (([0, 6], m.E*m.A/l), ([3, 9], m.G*m.J/l)):
        k[np.ix_(indices, indices)] = coefficient*np.array([[1., -1.], [-1., 1.]])
    for indices, signs, ei, ga in (([1, 5, 7, 11], [1, 1, 1, 1], m.E*m.Iz, None if m.Asy is None else m.G*m.Asy),
                                  ([2, 4, 8, 10], [1, -1, 1, -1], m.E*m.Iy, None if m.Asz is None else m.G*m.Asz)):
        k[np.ix_(indices, indices)] = bending_stiffness(ei, l, ga)*np.outer(signs, signs)
    return k


def compatibility(length):
    l = length
    c = np.zeros((6, 12))
    c[0, [0, 6]] = [-1., 1.]
    c[1, [3, 9]] = [-1., 1.]
    c[2, [5, 11]] = [-1., 1.]
    c[3, [1, 5, 7, 11]] = [-1., -l/2, 1., -l/2]
    c[4, [4, 10]] = [1., -1.]
    c[5, [2, 4, 8, 10]] = [-1., l/2, 1., l/2]
    return c


def load_integral(loads, x, order, side="right"):
    total = np.zeros(3)
    for load in loads:
        if load.kind == "uniform":
            total += load.vector * x**(order+1) / math.factorial(order+1)
        elif load.a < x or (load.a == x and side == "right"):
            total += load.vector * (x-load.a)**order / math.factorial(order)
    return total


def equivalent_load(m, length, k, loads, strain):
    """Exact Timoshenko/EB fixed-end actions by integrated compatibility."""
    l = length
    i0, i1, i2, i3 = [load_integral(loads, l, j) for j in range(4)]
    particular = np.zeros(12)
    particular[6] = strain*l-i1[0]/(m.E*m.A)
    particular[7] = i3[1]/(m.E*m.Iz) - (i1[1]/(m.G*m.Asy) if m.Asy else 0.)
    particular[8] = i3[2]/(m.E*m.Iy) - (i1[2]/(m.G*m.Asz) if m.Asz else 0.)
    particular[10] = -i2[2]/(m.E*m.Iy)
    particular[11] = i2[1]/(m.E*m.Iz)
    end = np.zeros(12)
    end[6:9] = -i0
    end[10], end[11] = -i1[2], i1[1]
    return k @ particular - end


def rotation_coordinates(model, segments, nodes_count, size):
    """Remove unused *directions*, not just axis-aligned joint rotations.

    A released inclined end can leave a two-dimensional joint rotation gauge.
    Supported global axes retain their original slots. The remaining slots span
    the retained member axes, followed by unused directions with zero operators.
    """
    directions = [[] for _ in range(nodes_count)]
    for owner, ni, nj, _, _, segment in segments:
        member = model.members[owner]
        for node, end in ((ni, 0), (nj, 6)):
            # An original release applies only at that original endpoint.
            original_end = node == (member.i if end == 0 else member.j)
            for axis in range(3):
                if not original_end or end+3+axis not in member.releases:
                    directions[node].append(member.R[axis])
    transform = np.eye(size)
    unused = []
    for node, rows in enumerate(directions):
        constrained = [axis for axis in range(3) if 6*node+3+axis in model.fixed or
                       (6*node+3+axis < len(model.springs) and model.springs[6*node+3+axis] > 0)]
        available = [axis for axis in range(3) if axis not in constrained]
        if not available:
            continue
        matrix = np.array(rows).reshape(-1, 3)[:, available]
        _, singular, vt = np.linalg.svd(matrix, full_matrices=True)
        tolerance = 128*np.finfo(float).eps*max(1, len(rows))
        if np.any((singular > tolerance) & (singular < 100*tolerance)):
            invalid("releases", "retained joint rotation rank is numerically undecidable", "input.out_of_range")
        rank = int(np.count_nonzero(singular > tolerance))
        if rank == len(available):
            continue
        indices = [6*node+3+axis for axis in available]
        if rank:
            transform[np.ix_(indices, indices)] = vt.T
        unused.extend(indices[rank:])
    return transform, unused


def compile_frame(model, subdivisions=1):
    # The caller validates subdivisions; this check also protects direct internal use.
    if not isinstance(subdivisions, int) or not 1 <= subdivisions <= 20:
        invalid("elements_per_member", "subdivision count must be 1..20", "input.out_of_range")
    nodes_count = len(model.ids) + (subdivisions-1)*len(model.members)
    size = 6*nodes_count + sum(len(m.releases) for m in model.members)
    if size > MAX_DOFS:
        invalid("elements_per_member", f"assembled frame exceeds {MAX_DOFS} total DOFs including released rotations", "input.out_of_range")
    ids, xyz, segments = list(model.ids), list(model.xyz), []
    for owner, m in enumerate(model.members):
        chain = [m.i]
        for j in range(1, subdivisions):
            chain.append(len(xyz))
            xyz.append(model.xyz[m.i] + (model.xyz[m.j]-model.xyz[m.i])*(j/subdivisions))
            ids.append(f"@{m.id}:{j}")
        chain.append(m.j)
        length = float(np.linalg.norm(model.xyz[m.j]-model.xyz[m.i]))/subdivisions
        for j in range(subdivisions):
            segments.append((owner, chain[j], chain[j+1], length, j*length, j))
    coordinate_transform, unused_rotations = rotation_coordinates(model, segments, nodes_count, size)
    k, f = np.zeros((size, size)), np.zeros(size)
    original_dofs = len(model.nodal)
    f[:original_dofs] = coordinate_transform[:original_dofs, :original_dofs].T @ model.nodal
    for dof in unused_rotations:
        if dof < original_dofs:
            node = dof//6
            bound = 128*np.finfo(float).eps*float(np.linalg.norm(model.nodal[6*node+3:6*node+6]))
            if abs(f[dof]) > bound:
                invalid("loads", "a nodal moment acts in an unused released joint-rotation direction")
        f[dof] = 0.
    labels = [f"{name}/{axis}" for name in ids for axis in AXES]
    for dof in unused_rotations:
        labels[dof] = f"{ids[dof//6]}/unused_rotation_{dof%6-2}"
    rows, elements, private_dof = [], [], 6*len(ids)
    for owner, ni, nj, length, start, segment in segments:
        m = model.members[owner]
        released = [v for v in m.releases if (v < 6 and segment == 0) or (v >= 6 and segment == subdivisions-1)]
        dofs = list(range(6*ni, 6*ni+6)) + list(range(6*nj, 6*nj+6))
        t = np.zeros((12, 12+len(released)))
        for j in range(0, 12, 3):
            t[j:j+3, j:j+3] = m.R
        for local, node in ((3, ni), (9, nj)):
            start_rotation = 6*node+3
            t[local:local+3, local:local+3] = m.R @ coordinate_transform[start_rotation:start_rotation+3, start_rotation:start_rotation+3]
            for axis in range(3):
                if start_rotation+axis in unused_rotations:
                    t[local:local+3, local+axis] = 0.
        private = {}
        for j, local in enumerate(released):
            t[local, :] = 0.
            t[local, 12+j] = 1.
            dofs.append(private_dof)
            private[local] = private_dof
            labels.append(f"@{m.id}/{'start' if local < 6 else 'end'}/{AXES[local%6]}")
            private_dof += 1
        dofs = np.array(dofs)
        local_loads = []
        for load in model.loads[owner]:
            if load.kind == "uniform":
                local_loads.append(load)
            else:
                original_length = float(np.linalg.norm(model.xyz[m.j]-model.xyz[m.i]))
                load_segment = min(int(load.a/original_length*subdivisions), subdivisions-1)
                if segment == load_segment:
                    local_loads.append(Load("point", load.vector, min(length, max(0., load.a-start))))
        local_k = stiffness(m, length)
        force = equivalent_load(m, length, local_k, local_loads, model.strains[owner])
        k[np.ix_(dofs, dofs)] += t.T @ local_k @ t
        f[dofs] += t.T @ force
        c = np.zeros((6, size))
        c[:, dofs] = compatibility(length) @ t
        rows.append(c)
        elements.append(Element(m, owner, length, start, dofs, t, local_k, force, local_loads, model.strains[owner], ni, nj, private))
    springs, rest = np.zeros(size), np.zeros(size)
    springs[:len(model.springs)], rest[:len(model.rest)] = model.springs, model.rest
    k[np.diag_indices(size)] += springs
    f += springs * rest
    for index in np.flatnonzero(springs):
        row = np.zeros((1, size))
        row[0, index] = 1.
        rows.append(row)
    return Mesh(ids, np.array(xyz), elements, (k+k.T)/2, f, np.vstack(rows), dict(model.fixed), springs, rest, labels, model, coordinate_transform)


def nullspace(matrix, param="supports"):
    """Geometric rank with a guard band; never classify by stiffness/frequency."""
    if matrix.shape[0] == 0:
        return np.eye(matrix.shape[1])
    norms = np.linalg.norm(matrix, axis=1)
    normalized = matrix[norms > 0] / norms[norms > 0, None]
    if normalized.shape[0] == 0:
        return np.eye(matrix.shape[1])
    _, singular, vt = np.linalg.svd(normalized, full_matrices=True)
    tol = 8*np.finfo(float).eps*max(normalized.shape)*singular[0]
    if np.any((singular > tol) & (singular < 100*tol)):
        invalid(param, "geometric constraint rank is numerically undecidable", "input.out_of_range")
    rank = int(np.count_nonzero(singular > tol))
    return vt[rank:].T


def kinematic_space(mesh):
    size = len(mesh.k)
    used = np.any(mesh.c != 0, axis=0)
    inactive = np.flatnonzero(~used)
    if any(mesh.f[j] != 0 for j in inactive if j not in mesh.fixed):
        invalid("loads", "a load acts on an inactive released joint DOF")
    free = np.array([j for j in range(size) if j not in mesh.fixed and used[j]], dtype=int)
    span = max(e.length for e in mesh.elements)
    scale_full = np.ones(size)
    for j in range(3):
        scale_full[j:6*len(mesh.ids):6] = span
    scale = scale_full[free]
    z = nullspace(mesh.c[:, free]*scale)
    return free, scale[:, None]*z, scale


def rigid_basis(mesh, free):
    """Join analytic element rigid fields through retained end coordinates.

    A release couples translations but only the retained rotations. Thus rigid
    clusters may rotate independently, including at a fixed, released joint.
    """
    rows = []
    span = max(e.length for e in mesh.elements)
    scale = np.array([span if j < 6*len(mesh.ids) and j % 6 < 3 else 1. for j in free])
    for e in mesh.elements:
        local = np.zeros((12, 6))
        for end, offset in ((0, -e.length/2), (6, e.length/2)):
            local[end:end+3, :3] = np.eye(3)
            local[end+3:end+6, 3:] = np.eye(3)
            for axis in range(3):
                local[end:end+3, axis+3] = np.cross(np.eye(3)[axis], [offset, 0., 0.])
        constraints = nullspace(local.T).T
        row = np.zeros((6, len(mesh.k)))
        row[:, e.dofs] = constraints @ e.transform
        rows.append(row[:, free]*scale)
    for dof in np.flatnonzero(mesh.springs):
        rows.append((free == dof)[None, :]*scale)
    basis = scale[:, None]*nullspace(np.vstack(rows))
    return np.linalg.qr(basis, mode="reduced")[0]


def equilibrated_stiffness(mesh, matrix, null=None):
    """Jacobi scaling and a 2-norm condition estimate on the elastic space."""
    shortest = min(mesh.elements, key=lambda e: e.length)
    if not len(matrix):
        return np.ones(0), matrix.copy(), {"estimated_condition_number": 1., "relative_error_bound": 0.,
            "maximum_relative_error_bound": MAX_CONDITION_ERROR, "shortest_member": shortest.member.id}
    diagonal = np.diag(matrix)
    if np.any(diagonal <= 0):
        invalid("members", "elastic stiffness cannot be resolved", "input.out_of_range")
    scale = 1/np.sqrt(diagonal)
    scaled = matrix*np.outer(scale, scale)
    elastic = scaled
    if null is not None and null.shape[1]:
        complete, _ = np.linalg.qr(null/scale[:, None], mode="complete")
        complement = complete[:, null.shape[1]:]
        elastic = complement.T @ scaled @ complement
    condition = float(np.linalg.cond(elastic)) if len(elastic) else 1.
    bound = condition*np.finfo(float).eps
    if not math.isfinite(bound) or bound > MAX_CONDITION_ERROR:
        invalid("members", f"shortest member {shortest.member.id!r} (element length {shortest.length:.9g} m): "
                f"equilibrated stiffness condition number {condition:.6g}, cond*epsilon={bound:.6g} "
                f"exceeds {MAX_CONDITION_ERROR:g}", "input.out_of_range")
    return scale, scaled, {"estimated_condition_number": condition, "relative_error_bound": bound,
        "maximum_relative_error_bound": MAX_CONDITION_ERROR, "shortest_member": shortest.member.id,
        "method": "Jacobi-scaled stiffness 2-norm condition; rigid fields excluded; cond*float64 epsilon"}


def accurate_residual(matrix, solution, rhs):
    """Compensated summation also works where longdouble is only float64."""
    return np.array([math.fsum([float(b), *(-row*solution).tolist()]) for row, b in zip(matrix, rhs)])


def solve_static(mesh):
    free, null, _ = kinematic_space(mesh)
    if null.shape[1]:
        invalid("supports", f"frame has {null.shape[1]} unrestrained kinematic modes; add restraints or revise releases")
    u = np.zeros(len(mesh.k))
    for dof, value in mesh.fixed.items():
        u[dof] = value
    kk = mesh.k[np.ix_(free, free)]
    scale, ks, conditioning = equilibrated_stiffness(mesh, kk)
    if len(free):
        rhs = mesh.f[free] - mesh.k[free] @ u
        # Static condensation of private released-end coordinates. Modal analysis
        # retains these coordinates and never applies this static reduction to M.
        joint = np.flatnonzero(free < 6*len(mesh.ids))
        private = np.flatnonzero(free >= 6*len(mesh.ids))
        if len(private):
            pp = ks[np.ix_(private, private)]
            pj = ks[np.ix_(private, joint)]
            lp = np.linalg.cholesky(pp)
            inv_pj = np.linalg.solve(lp.T, np.linalg.solve(lp, pj))
            reduced = ks[np.ix_(joint, joint)] - pj.T @ inv_pj
            if len(joint):
                chol = np.linalg.cholesky((reduced+reduced.T)/2)
        else:
            chol = np.linalg.cholesky(ks)

        def solve(rhs):
            fs = rhs*scale
            if not len(private):
                return scale*np.linalg.solve(chol.T, np.linalg.solve(chol, fs))
            sol = np.zeros(len(free))
            inv_fp = np.linalg.solve(lp.T, np.linalg.solve(lp, fs[private]))
            if len(joint):
                load = fs[joint] - pj.T @ inv_fp
                sol[joint] = np.linalg.solve(chol.T, np.linalg.solve(chol, load))
            sol[private] = inv_fp-inv_pj@sol[joint]
            return scale*sol

        u[free] = solve(rhs)
        for _ in range(2):
            u[free] += solve(accurate_residual(kk, u[free], rhs))
        residual = accurate_residual(kk, u[free], rhs)
        relative = float(np.linalg.norm(residual)/(np.linalg.norm(kk)*np.linalg.norm(u[free])+np.linalg.norm(rhs)+1e-300))
        if relative > 1e-10:
            invalid("members", "static equilibrium solve did not converge accurately", "input.out_of_range")
    else:
        relative = 0.
    reactions = -mesh.springs*(u-mesh.rest)
    residual = mesh.k@u-mesh.f
    for dof in mesh.fixed:
        reactions[dof] += residual[dof]
    return u, reactions, {"stable": True, "kinematic_nullity": 0, "relative_residual": relative,
                           "conditioning": conditioning, "refinement_steps": 2 if len(free) else 0,
                           "inactive_dofs": [mesh.labels[j] for j in range(len(u)) if j not in free and j not in mesh.fixed],
                           "statically_condensed_release_dofs": int(np.sum(free >= 6*len(mesh.ids)))}


def recover(element, u):
    d = element.transform @ u[element.dofs]
    return d, element.stiffness@d-element.force


def response(element, d, end, x, side="right"):
    m = element.member
    i0, i1, i2, i3 = [load_integral(element.loads, x, order, side) for order in range(4)]
    n = -end[0]-i0[0]
    vy, vz = end[1]+i0[1], end[2]+i0[2]
    my, mz = -end[4]-end[2]*x-i1[2], -end[5]+end[1]*x+i1[1]
    axial = d[0]+element.strain*x+(-end[0]*x-i1[0])/(m.E*m.A)
    rz = d[5]+(-end[5]*x+end[1]*x*x/2+i2[1])/(m.E*m.Iz)
    ry = d[4]-(end[4]*x+end[2]*x*x/2+i2[2])/(m.E*m.Iy)
    v = d[1]+d[5]*x+(-end[5]*x*x/2+end[1]*x**3/6+i3[1])/(m.E*m.Iz)
    w = d[2]-d[4]*x+(end[4]*x*x/2+end[2]*x**3/6+i3[2])/(m.E*m.Iy)
    if m.Asy:
        v -= (end[1]*x+i1[1])/(m.G*m.Asy)
        w -= (end[2]*x+i1[2])/(m.G*m.Asz)
    torque = -end[3]
    rx = d[3]+torque*x/(m.G*m.J)
    return np.array([n, vy, vz, torque, my, mz, axial, v, w, rx, ry, rz])


def equilibrium(mesh, reactions):
    """All applied loads and ground reactions, moments about the global origin."""
    forces = mesh.model.nodal.reshape(-1, 6).copy()
    forces += global_coordinates(mesh, reactions)[:len(mesh.model.nodal)].reshape(-1, 6)
    total_force = np.sum(forces[:, :3], axis=0)
    total_moment = np.sum(forces[:, 3:] + np.cross(mesh.model.xyz, forces[:, :3]), axis=0)
    for owner, m in enumerate(mesh.model.members):
        delta = mesh.model.xyz[m.j]-mesh.model.xyz[m.i]
        length = float(np.linalg.norm(delta))
        for load in mesh.model.loads[owner]:
            applied = m.R.T@load.vector*(length if load.kind == "uniform" else 1.)
            point = mesh.model.xyz[m.i]+delta*(.5 if load.kind == "uniform" else load.a/length)
            total_force += applied
            total_moment += np.cross(point, applied)
    return np.r_[total_force, total_moment]
