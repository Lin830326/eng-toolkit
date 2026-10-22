"""Undamped modes of the shared three-dimensional frame model."""

from __future__ import annotations

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures3d._common import Result, System, count, csv_text, fields, invalid, q, records, run
from eng_toolkit.domains.structures3d._model import compile_frame, equilibrated_stiffness, global_coordinates, kinematic_space, parse_frame, rigid_basis
from eng_toolkit.domains.structures3d._server import server
from eng_toolkit.domains.structures3d._types import Members, Modes, Nodes, Orientations, Releases, Subdivisions, Supports


_POINTS, _WEIGHTS = np.polynomial.legendre.leggauss(3)


def _reference(name: str, ids: list[str], path: str) -> int:
    if name not in ids:
        invalid(path, f"unknown reference {name!r}")
    return ids.index(name)


def _mass_inputs(model, density, mass_per_length, member_masses, nodal_masses):
    rho = q(density, "density", "density", optional=True)
    mu = q(mass_per_length, "mass_per_length", "mass_length", optional=True)
    if rho is not None and mu is not None:
        invalid("mass_per_length", "supply density or mass_per_length, not both")
    lengths = [mu if mu is not None else rho * m.A if rho is not None else None for m in model.members]
    names = [m.id for m in model.members]
    seen = set()
    for row_index, row in enumerate(records(member_masses, "member_masses", 400)):
        path = f"member_masses/{row_index}"
        fields(row, (2,), path)
        member_index = _reference(row[0], names, path + "/0")
        if member_index in seen:
            invalid(path, "duplicate member mass")
        seen.add(member_index)
        lengths[member_index] = q(row[1], path + "/1", "mass_length")
    if any(value is None for value in lengths):
        invalid("density", "every member needs positive distributed mass from density, mass_per_length or member_masses")

    lumped = np.zeros(6 * len(model.ids))
    seen.clear()
    for row_index, row in enumerate(records(nodal_masses, "nodal_masses", 200)):
        path = f"nodal_masses/{row_index}"
        fields(row, (3, 7), path)
        node_index = _reference(row[0], model.ids, path + "/0")
        if node_index in seen:
            invalid(path, "duplicate nodal mass")
        seen.add(node_index)
        if len(row) == 3:
            mass = q(row[1], path + "/1", "mass", zero=True)
            inertia = q(row[2], path + "/2", "mass_inertia", zero=True)
            lumped[6 * node_index:6 * node_index + 6] = [mass] * 3 + [inertia] * 3
        else:
            lumped[6 * node_index:6 * node_index + 6] = [
                q(value, path + f"/{axis + 1}", "mass" if axis < 3 else "mass_inertia", zero=True)
                for axis, value in enumerate(row[1:])
            ]
    return np.asarray(lengths, dtype=float), lumped


def _hermite_mass(length: float, mass_per_length: float) -> np.ndarray:
    l = length
    return mass_per_length * l / 420 * np.array([
        [156, 22*l, 54, -13*l],
        [22*l, 4*l*l, 13*l, -3*l*l],
        [54, 13*l, 156, -22*l],
        [-13*l, -3*l*l, -22*l, 4*l*l],
    ])


def _element_mass(length: float, mass_per_length: float, polar_per_length: float,
                  rotary_y: float = 0., rotary_z: float = 0.) -> np.ndarray:
    """Centroidal translation, torsion and optional Rayleigh bending rotary mass."""
    local = np.zeros((12, 12))
    pair = np.array([[2., 1.], [1., 2.]]) * length / 6
    local[np.ix_([0, 6], [0, 6])] = mass_per_length * pair
    local[np.ix_([3, 9], [3, 9])] = polar_per_length * pair
    bend = _hermite_mass(length, mass_per_length)
    local[np.ix_([1, 5, 7, 11], [1, 5, 7, 11])] = bend
    z_sign = np.diag([1., -1., 1., -1.])
    local[np.ix_([2, 4, 8, 10], [2, 4, 8, 10])] = z_sign @ bend @ z_sign
    if rotary_y or rotary_z:
        rotary = np.zeros((4, 4))
        for point, weight in zip(_POINTS, _WEIGHTS):
            t = (point + 1) / 2
            slope = np.array([6 * (t*t - t) / length, 1 - 4*t + 3*t*t,
                              6 * (t - t*t) / length, -2*t + 3*t*t])
            rotary += weight * length / 2 * np.outer(slope, slope)
        local[np.ix_([1, 5, 7, 11], [1, 5, 7, 11])] += rotary_z * rotary
        local[np.ix_([2, 4, 8, 10], [2, 4, 8, 10])] += rotary_y * (z_sign @ rotary @ z_sign)
    return local


def assemble_mass(mesh, mass_lengths: np.ndarray, lumped: np.ndarray,
                  include_rotary_inertia: bool = False) -> np.ndarray:
    mass = np.zeros_like(mesh.k)
    for element in mesh.elements:
        member = element.member
        linear_mass = mass_lengths[element.owner]
        polar_mass = linear_mass * (member.Iy + member.Iz) / member.A
        density = linear_mass / member.A if include_rotary_inertia else 0.
        local = _element_mass(element.length, linear_mass, polar_mass,
                              density * member.Iy, density * member.Iz)
        mass[np.ix_(element.dofs, element.dofs)] += element.transform.T @ local @ element.transform
    coordinates = mesh.coordinate_transform[:len(lumped), :len(lumped)]
    mass[:len(lumped), :len(lumped)] += coordinates.T @ (lumped[:, None]*coordinates)
    return (mass + mass.T) / 2


def _influence(mesh, axis: int, rotation: bool) -> np.ndarray:
    field = np.zeros(len(mesh.k))
    direction = np.eye(3)[axis]
    for joint, xyz in enumerate(mesh.xyz):
        if rotation:
            field[6 * joint:6 * joint + 3] = np.cross(direction, xyz)
            field[6 * joint + 3:6 * joint + 6] = direction
        else:
            field[6 * joint:6 * joint + 3] = direction
    if rotation:
        for element in mesh.elements:
            for local, dof in element.private.items():
                field[dof] = float(element.member.R[local % 3] @ direction)
    field = mesh.coordinate_transform.T @ field
    return field


def _cantilever_reference(model, mass_lengths, lumped, mesh, result):
    if len(model.members) != 1 or np.any(model.springs) or np.any(lumped) or model.members[0].releases:
        return
    member = model.members[0]
    first = set(range(6 * member.i, 6 * member.i + 6))
    second = set(range(6 * member.j, 6 * member.j + 6))
    if set(mesh.fixed) not in (first, second):
        return
    length = float(np.linalg.norm(model.xyz[member.j] - model.xyz[member.i]))
    mu = mass_lengths[0]
    polar = mu * (member.Iy + member.Iz) / member.A
    betas = (1.875104068711961, 4.694091132974174, 7.854757438237612)
    bending = lambda inertia: [beta**2 * math.sqrt(member.E * inertia / (mu * length**4)) / (2 * math.pi)
                                for beta in betas]
    odd = (1, 3, 5)
    result.data["closed_form_comparison"] = {
        "boundary": "cantilever",
        "bending_about_local_y_hz": bending(member.Iy),
        "bending_about_local_z_hz": bending(member.Iz),
        "torsion_hz": [n / (4 * length) * math.sqrt(member.G * member.J / polar) for n in odd],
        "axial_hz": [n / (4 * length) * math.sqrt(member.E * member.A / mu) for n in odd],
        "note": "Continuous Euler-Bernoulli beam references; frame modes can interleave the two bending directions, torsion and axial motion.",
    }


def solve_modes(mesh, mass: np.ndarray, requested: int):
    free, null_basis, _ = kinematic_space(mesh)
    if not len(free):
        invalid("supports", "no active dynamic degrees of freedom remain")
    zeros = null_basis.shape[1]
    rigid = rigid_basis(mesh, free)
    if zeros != rigid.shape[1]:
        invalid("members", "compatibility nullity disagrees with released rigid clusters", "input.out_of_range")
    if zeros and np.linalg.norm(null_basis - rigid @ (rigid.T @ null_basis)) > 1e-8 * np.linalg.norm(null_basis):
        invalid("members", "kinematic zero modes disagree with the analytic rigid-body motions", "input.out_of_range")
    kk = mesh.k[np.ix_(free, free)]
    mm = mass[np.ix_(free, free)]
    scale, scaled_k, conditioning = equilibrated_stiffness(mesh, kk, rigid)
    diagonal = np.diag(mm)
    if np.any(diagonal <= 0):
        invalid("nodal_masses", "active massless degree of freedom; supply distributed or nodal mass")
    scaled_mass = scale[:, None] * mm * scale[None, :]
    try:
        chol = np.linalg.cholesky((scaled_mass + scaled_mass.T) / 2)
    except np.linalg.LinAlgError:
        invalid("density", "mass matrix is not positive definite or numerically resolvable", "input.out_of_range")
    whitened = np.linalg.solve(chol, scaled_k)
    whitened = np.linalg.solve(chol, whitened.T).T
    if zeros:
        # x = diag(scale) L^-T y; transform admitted rigid motions into y.
        rigid_whitened = chol.T @ (rigid / scale[:, None])
        complete, _ = np.linalg.qr(rigid_whitened, mode="complete")
        elastic_space = complete[:, zeros:]
        zero_vectors = complete[:, :zeros]
    else:
        elastic_space = np.eye(len(free))
        zero_vectors = np.zeros((len(free), 0))
    elastic_operator = elastic_space.T @ ((whitened + whitened.T) / 2) @ elastic_space
    positive, elastic_vectors = np.linalg.eigh((elastic_operator + elastic_operator.T) / 2)
    eigenvalues = np.r_[np.zeros(zeros), positive]
    vectors = np.column_stack((zero_vectors, elastic_space @ elastic_vectors))
    if len(eigenvalues) > zeros:
        if positive[0] <= 64 * np.finfo(float).eps * abs(eigenvalues[-1]):
            invalid("members", "positive modal frequencies cannot be resolved reliably", "input.out_of_range")
    count_out = min(requested, len(free))
    shapes = np.zeros((len(mesh.k), count_out))
    shapes[free] = scale[:, None] * np.linalg.solve(chol.T, vectors[:, :count_out])
    residuals = []
    for mode in range(count_out):
        shape = shapes[free, mode]
        norm = math.sqrt(float(shape @ mm @ shape))
        shapes[:, mode] /= norm
        shape = shapes[free, mode]
        pivot = int(np.argmax(np.abs(shapes[:, mode])))
        if shapes[pivot, mode] < 0:
            shapes[:, mode] *= -1
            shape = shapes[free, mode]
        residual = kk @ shape - eigenvalues[mode] * (mm @ shape)
        denominator = np.linalg.norm(kk, np.inf) * np.linalg.norm(shape) + eigenvalues[mode] * np.linalg.norm(mm @ shape)
        residuals.append(float(np.linalg.norm(residual) / max(denominator, 1e-30)))
    if residuals and max(residuals) > 1e-7:
        invalid("members", "modal solution failed its residual check", "input.out_of_range")
    return eigenvalues[:count_out], shapes, zeros, residuals, conditioning


@server.tool(description="Undamped three-dimensional Euler-Bernoulli frame modes with consistent distributed mass, local end releases and springs. Returns frequencies, effective translation masses and a mass-normalized shape CSV. Increase elements_per_member to check convergence.")
def modal(
    nodes: Nodes = "", members: Members = "", supports: Supports = "",
    orientations: Orientations = "", releases: Releases = "",
    shear_properties: Annotated[str, Field(description="Must be ''; effective shear areas such as 'e,800 mm^2,600 mm^2' are supported only by the static tool.")] = "",
    density: Annotated[str, Field(description="Default mass density, e.g. '7800 kg/m^3'; '' omits; conflicts with mass_per_length.")] = "",
    mass_per_length: Annotated[str, Field(description="Default distributed mass, e.g. '10 kg/m'; '' omits; alternative to density.")] = "",
    member_masses: Annotated[str, Field(description="Optional per-member overrides 'member,mass/length', e.g. 'e,10 kg/m'; '' omits; every member needs positive distributed mass.")] = "",
    nodal_masses: Annotated[str, Field(description="Optional joint additions 'node,m,J' for equal x/y/z mass and inertia, or 'node,mx,my,mz,Jx,Jy,Jz'; e.g. 'b,10 kg,0 kg*m^2'; '' omits.")] = "",
    include_rotary_inertia: Annotated[bool, Field(description="Include Rayleigh cross-section rotary inertia rho*Iy and rho*Iz in bending mass; default false retains centroidal Euler-Bernoulli mass.")] = False,
    modes_count: Modes = 6, elements_per_member: Subdivisions = 4,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        subdivision = count(elements_per_member, "elements_per_member", 1, 20)
        requested = count(modes_count, "modes_count", 1, 30)
        if not isinstance(include_rotary_inertia, bool):
            invalid("include_rotary_inertia", "expected a boolean")
        if not isinstance(shear_properties, str) or shear_properties.strip():
            invalid("shear_properties", "modal analysis currently supports Euler-Bernoulli members only")
        model = parse_frame(nodes, members, supports, orientations=orientations, releases=releases)
        mass_lengths, lumped = _mass_inputs(model, density, mass_per_length, member_masses, nodal_masses)
        mesh = compile_frame(model, subdivision)
        coordinates = mesh.coordinate_transform[:len(lumped), :len(lumped)]
        nodal_diagonal = np.sum(lumped[:, None]*coordinates**2, axis=0)
        inactive_mass = [dof for dof in range(len(lumped))
                         if dof not in mesh.fixed and nodal_diagonal[dof] > 0 and not np.any(mesh.c[:, dof])]
        if inactive_mass:
            invalid("nodal_masses", "nodal inertia acts on an unrestrained joint coordinate disconnected by releases")
        mass = assemble_mass(mesh, mass_lengths, lumped, include_rotary_inertia)
        eigenvalues, shapes, zeros, residuals, conditioning = solve_modes(mesh, mass, requested)
        global_shapes = global_coordinates(mesh, shapes)
        result = Result(unit_system, {
            "eigenproblem": "K phi = omega^2 M phi; project analytic rigid motions in mass-whitened coordinates, then solve the elastic complement by eigh",
            "distributed_mass": "integral(rho A N_trans^T N_trans + rho(Iy+Iz) N_rx^T N_rx) dx; optional Rayleigh bending mass adds rho*Iz*v_prime^2 + rho*Iy*w_prime^2",
            "effective_mass": "fraction=(phi^T M r)^2 / ((phi^T M phi)(r^T M r)); r is unit global translation or rigid rotation about the global origin",
        }, ("Linear unstressed Euler-Bernoulli stiffness with Rayleigh bending rotary inertia, centroidal translational mass and torsional rotary inertia. "
            if include_rotary_inertia else "Linear unstressed Euler-Bernoulli stiffness with centroidal translational mass and torsional rotary inertia; bending cross-section rotary inertia excluded. ")
           + "Shear flexibility, damping, prestress and forced response are excluded. Pin-release rotations remain independent dynamic coordinates. Springs change stiffness; rest positions do not change unstressed modes.",
                        "Up to 200 nodes, 400 members, 1..20 elements/member, 1200 total DOFs including private rotations and 30 modes. Positive distributed mass is required for every member. Compare meshes for frequency convergence.")
        result.data["zero_mode_count"] = zeros
        result.data["conditioning"] = conditioning
        result.data["mass_model"] = "Rayleigh bending rotary inertia" if include_rotary_inertia else "Euler-Bernoulli centroidal mass"
        result.data["references"] = [
            "https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf#page=17, section 3.6, consistent Euler-Bernoulli mass",
            "https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf#page=27, section 4.5, cross-section rotary inertia without shear deformation",
            "https://aeresources.gatech.edu/strdyn/Webpage/BeamBending/Examples/BeamBending_p2.php, cantilever frequency roots",
        ]
        result.data["eigen_residuals"] = residuals
        result.data["convergence"] = f"{subdivision} elements per original member; increase elements_per_member and compare frequencies."
        if zeros:
            result.warnings.append(f"{zeros} rigid-body zero modes remain after supports and springs.")
        if np.any(model.rest) or any(model.fixed.values()):
            result.warnings.append("Support settlements and spring rest positions do not change unstressed natural frequencies.")
        rows = []
        result.data["modes"] = []
        cumulative = np.zeros(6)
        for mode, value in enumerate(eigenvalues):
            omega = math.sqrt(value)
            fractions = []
            for rotation in (False, True):
                for axis in range(3):
                    influence = _influence(mesh, axis, rotation)
                    total = float(influence @ mass @ influence)
                    fractions.append(float((shapes[:, mode] @ mass @ influence)**2 / total) if total > 0 else 0.)
            cumulative += fractions
            result.data["modes"].append({
                "mode": mode + 1,
                "frequency": result.quantity(omega / (2 * math.pi), "frequency"),
                "omega": result.quantity(omega, "omega"),
                "effective_mass_fraction_x": fractions[0],
                "effective_mass_fraction_y": fractions[1],
                "effective_mass_fraction_z": fractions[2],
                "effective_mass_fraction_rx": fractions[3],
                "effective_mass_fraction_ry": fractions[4],
                "effective_mass_fraction_rz": fractions[5],
                "cumulative_effective_mass_fractions": dict(zip(("x", "y", "z", "rx", "ry", "rz"), cumulative.tolist())),
            })
            for joint, name in enumerate(mesh.ids):
                rows.append([mode + 1, name, *global_shapes[6 * joint:6 * joint + 6, mode]])
            for dof in range(6 * len(mesh.ids), len(mesh.k)):
                rows.append([mode + 1, mesh.labels[dof], *([0.] * 6), float(shapes[dof, mode])])
        result.data["cumulative_effective_mass_fractions"] = dict(zip(("x", "y", "z", "rx", "ry", "rz"), cumulative.tolist()))
        result.data["mass_participation"] = (
            "Gamma=phi_f^T(M_ff r_f+M_fs r_s), phi^T M phi=1; total=r^T M r over all coordinates. "
            "Rigid translation r includes restrained translations. The fraction remaining after all free modes "
            "is carried by the support; a truncated mode list also omits higher-mode participation.")
        artifact = handle.write_text("mode_shapes.csv", csv_text([
            "mode", "node_or_release", "ux_mass_normalized", "uy_mass_normalized", "uz_mass_normalized",
            "rx_mass_normalized", "ry_mass_normalized", "rz_mass_normalized", "private_rotation_mass_normalized",
        ], [row if len(row) == 9 else [*row, 0.] for row in rows]), fmt="csv")
        result.artifacts.append(artifact)
        result.data["mode_shape_table"] = {"file": artifact.path, "rows": len(rows), "normalization": "phi.T M phi = 1 in SI; coefficients are not physical vibration amplitudes"}
        result.add("first_frequency", math.sqrt(eigenvalues[0]) / (2 * math.pi), "frequency")
        result.add("model_dofs", len(mesh.k))
        if not include_rotary_inertia:
            _cantilever_reference(model, mass_lengths, lumped, mesh, result)
        return result.done("first_frequency")

    return run("modal", inputs, compute)
