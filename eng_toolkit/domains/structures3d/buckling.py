"""Linearized geometric buckling of a three-dimensional frame reference state."""

from __future__ import annotations

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures3d._common import Result, System, count, csv_text, invalid, run
from eng_toolkit.domains.structures3d._model import compile_frame, equilibrated_stiffness, global_coordinates, kinematic_space, parse_frame, recover, response, solve_static
from eng_toolkit.domains.structures3d._server import server
from eng_toolkit.domains.structures3d._types import Loads, MemberLoads, Members, Modes, Nodes, Orientations, Releases, Subdivisions, Supports


_POINTS, _WEIGHTS = np.polynomial.legendre.leggauss(3)


def _slopes(length: float, x: float) -> np.ndarray:
    """Derivatives of the cubic Hermite displacement functions at x."""
    t = x / length
    return np.array([6 * (t * t - t) / length, 1 - 4 * t + 3 * t * t,
                     6 * (t - t * t) / length, -2 * t + 3 * t * t])


def _element_geometric(element, displacement: np.ndarray) -> tuple[np.ndarray, float, float]:
    local_displacement, end = recover(element, displacement)
    geometric = np.zeros((12, 12))
    twist = np.zeros(12)
    twist[[3, 9]] = [-1/element.length, 1/element.length]
    polar_radius2 = (element.member.Iy+element.member.Iz)/element.member.A
    breaks = sorted({0., element.length, *(load.a for load in element.loads if load.kind == "point")})
    minimum, maximum = math.inf, -math.inf
    for start, stop in zip(breaks[:-1], breaks[1:]):
        for x, side in ((start, "right"), (stop, "left")):
            axial = float(response(element, local_displacement, end, x, side)[0])
            minimum, maximum = min(minimum, axial), max(maximum, axial)
        half = (stop - start) / 2
        middle = (stop + start) / 2
        for point, weight in zip(_POINTS, _WEIGHTS):
            x = middle + half * point
            axial = float(response(element, local_displacement, end, x)[0])
            derivative = _slopes(element.length, x)
            by = np.zeros(12)
            bz = np.zeros(12)
            by[[1, 5, 7, 11]] = derivative
            bz[[2, 4, 8, 10]] = derivative * np.array([1., -1., 1., -1.])
            geometric += weight * half * axial * (np.outer(by, by) + np.outer(bz, bz)
                                                  + polar_radius2*np.outer(twist, twist))
    return geometric, minimum, maximum


def assemble_geometric(mesh, displacement: np.ndarray):
    geometric = np.zeros_like(mesh.k)
    forces = []
    for element in mesh.elements:
        local, minimum, maximum = _element_geometric(element, displacement)
        geometric[np.ix_(element.dofs, element.dofs)] += element.transform.T @ local @ element.transform
        # A rotated pure bending state can leave tiny axial forces after
        # cancellation. Bound that cancellation in the original coordinates.
        absolute_local = np.abs(element.transform) @ np.abs(displacement[element.dofs])
        recovery_scale = float(np.abs(element.stiffness[0]) @ absolute_local + abs(element.force[0]))
        recovery_scale += sum(float(np.linalg.norm(load.vector))*(element.length if load.kind == "uniform" else 1.) for load in element.loads)
        resolution = 128*np.finfo(float).eps*recovery_scale
        forces.append({"member": element.member.id, "segment_start": element.start,
                       "minimum_axial_force": minimum, "maximum_axial_force": maximum,
                       "axial_resolution": resolution})
    return (geometric + geometric.T) / 2, forces


def solve_buckling(mesh, geometric: np.ndarray, requested: int):
    free, null_basis, _ = kinematic_space(mesh)
    if null_basis.shape[1]:
        invalid("supports", "reference frame has an unrestrained rigid motion or mechanism")
    if not len(free):
        invalid("supports", "no active degrees of freedom remain")
    kk = mesh.k[np.ix_(free, free)]
    gg = geometric[np.ix_(free, free)]
    scale, scaled_k, conditioning = equilibrated_stiffness(mesh, kk)
    try:
        chol = np.linalg.cholesky((scaled_k + scaled_k.T) / 2)
    except np.linalg.LinAlgError:
        invalid("members", "reference stiffness is not positive definite or numerically resolvable", "input.out_of_range")
    scaled_g = scale[:, None] * gg * scale[None, :]
    whitened = np.linalg.solve(chol, scaled_g)
    whitened = np.linalg.solve(chol, whitened.T).T
    eigenvalues, vectors = np.linalg.eigh((whitened + whitened.T) / 2)
    tolerance = 64 * np.finfo(float).eps * max(float(np.max(np.abs(eigenvalues))), 1e-30)
    negative = np.flatnonzero(eigenvalues < -tolerance)
    if not len(negative):
        if np.any(eigenvalues < 0):
            invalid("members", "compressive buckling eigenvalue is too small to resolve reliably", "input.out_of_range")
        invalid("loads", "reference state has no positive linear buckling factor; apply a compressive reference load")
    selected = negative[:requested]
    factors = -1 / eigenvalues[selected]
    shapes = np.zeros((len(mesh.k), len(selected)))
    shapes[free] = scale[:, None] * np.linalg.solve(chol.T, vectors[:, selected])
    residuals = []
    for mode, factor in enumerate(factors):
        shape = shapes[free, mode]
        energy = math.sqrt(float(shape @ kk @ shape))
        shapes[:, mode] /= energy
        pivot = int(np.argmax(np.abs(shapes[:, mode])))
        if shapes[pivot, mode] < 0:
            shapes[:, mode] *= -1
        shape = shapes[free, mode]
        residual = kk @ shape + factor * gg @ shape
        denominator = np.linalg.norm(kk, np.inf) * np.linalg.norm(shape) + factor * np.linalg.norm(gg @ shape)
        residuals.append(float(np.linalg.norm(residual) / max(denominator, 1e-30)))
    if max(residuals) > 1e-7:
        invalid("members", "buckling solution failed its residual check", "input.out_of_range")
    return factors, shapes, residuals, conditioning


def _euler_comparison(model, forces, factor: float, subdivision: int, result) -> None:
    if len(model.members) != 1 or model.members[0].releases or model.loads[0] or np.any(model.springs):
        return
    member = model.members[0]
    axes = np.argmax(np.abs(member.R), axis=1)
    if len(set(axes)) != 3 or not np.allclose(np.abs(member.R[np.arange(3), axes]), 1., atol=1e-12):
        return
    low = min(item["minimum_axial_force"] for item in forces)
    high = max(item["maximum_axial_force"] for item in forces)
    if low >= 0 or high - low > 1e-8 * max(abs(low), 1.):
        return
    compression = -low
    length = float(np.linalg.norm(model.xyz[member.j] - model.xyz[member.i]))
    boundaries = {}
    loads = {}
    for bend_axis, translation_axis, rotation_axis, inertia in (
        ("local_y", 2, 1, member.Iy), ("local_z", 1, 2, member.Iz),
    ):
        states = []
        for node in (member.i, member.j):
            translation = 6 * node + int(axes[translation_axis]) in model.fixed
            rotation = 6 * node + 3 + int(axes[rotation_axis]) in model.fixed
            states.append("clamped" if translation and rotation else "pinned" if translation else
                          "free" if not rotation else "guided")
        pair = tuple(states)
        coefficient = {("pinned", "pinned"): 1.,
                       ("clamped", "clamped"): 4.,
                       ("clamped", "free"): .25,
                       ("free", "clamped"): .25}.get(pair)
        if coefficient is None:
            continue
        boundaries[bend_axis] = "-".join(pair)
        loads[bend_axis] = math.pi**2 * member.E * inertia * coefficient / length**2
    if not loads:
        return
    predicted = min(loads.values()) / compression
    result.data["closed_form_comparison"] = {
        "boundary": boundaries,
        "critical_load": {axis: result.quantity(load, "force") for axis, load in loads.items()},
        "first_euler_load_factor": predicted,
        "computed_load_factor": factor,
        "relative_error": abs(factor - predicted) / predicted,
        "elements_per_member": subdivision,
        "note": "Constant compression, straight prismatic column and ideal pin/clamp/free bending restraints; compare refinements to reduce discretization error.",
    }


@server.tool(description="Linear eigenvalue buckling of a stable three-dimensional Euler-Bernoulli frame under proportional reference loads. Builds geometric stiffness from the recovered axial-force field. Returns positive load factors and mode shapes; increase elements_per_member to check convergence.")
def buckling(
    nodes: Nodes = "", members: Members = "", supports: Supports = "", loads: Loads = "",
    member_loads: MemberLoads = "", orientations: Orientations = "", releases: Releases = "",
    shear_properties: Annotated[str, Field(description="Must be ''; effective shear areas such as 'e,800 mm^2,600 mm^2' are supported only by the static tool.")] = "",
    thermal: Annotated[str, Field(description="Must be ''; thermal strain such as 'e,12e-6 1/K,20 K' is outside this mechanical-reference buckling model.")] = "",
    modes_count: Modes = 6, elements_per_member: Subdivisions = 4,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        subdivision = count(elements_per_member, "elements_per_member", 1, 20)
        requested = count(modes_count, "modes_count", 1, 30)
        if not isinstance(shear_properties, str) or shear_properties.strip():
            invalid("shear_properties", "buckling currently supports Euler-Bernoulli members only")
        if not isinstance(thermal, str) or thermal.strip():
            invalid("thermal", "buckling requires a reference state from proportional mechanical loads")
        model = parse_frame(nodes, members, supports, loads, member_loads, orientations, releases)
        if np.any(model.rest) or any(model.fixed.values()):
            invalid("supports", "buckling requires zero support settlements and spring rest positions")
        mesh = compile_frame(model, subdivision)
        displacement, _, stability = solve_static(mesh)
        geometric, forces = assemble_geometric(mesh, displacement)
        if all(item["minimum_axial_force"] >= 0 for item in forces):
            invalid("loads", "reference state has no compressive member force")
        if all(item["minimum_axial_force"] >= -item["axial_resolution"] for item in forces):
            invalid("loads", "reference compression cannot be distinguished from axial-force recovery roundoff", "input.out_of_range")
        factors, shapes, residuals, conditioning = solve_buckling(mesh, geometric, requested)
        global_shapes = global_coordinates(mesh, shapes)
        result = Result(unit_system, {
            "reference": "K u = F for the supplied reference loads; N(x) is recovered from each member's local axial equilibrium",
            "geometric_stiffness": "Kg = sum integral N(x) (v_prime.T v_prime + w_prime.T w_prime + (Iy+Iz)/A * theta_x_prime.T theta_x_prime) dx; tension-positive N",
            "eigenproblem": "(K + lambda Kg) phi = 0; positive lambda = -1 / negative eigenvalue of K^(-1/2) Kg K^(-1/2)",
        }, "Linearized bifurcation about a linearly elastic small-displacement reference state. Every supplied nodal and member load scales by the reported factor. Axial-stress geometric stiffness includes bending and twist. Shear centre equals centroid; Saint-Venant torsion has zero warping stiffness. Thin-walled open sections needing warping, bending-moment, shear, follower-load and imperfection effects are outside this model.",
                        "Stable Euler-Bernoulli reference frame, nonzero compressive effect, zero imposed settlements and thermal strain. Up to 200 nodes, 400 members, 1..20 elements/member, 1200 total DOFs including private rotations and 30 factors. Compare refinements for convergence; factors are not design strengths.")
        result.data["reference_stability"] = stability
        result.data["conditioning"] = conditioning
        result.data["references"] = [
            "https://people.duke.edu/~hpgavin/StructuralDynamics/StructuralElements.pdf#page=17, section 3.6, beam geometric stiffness",
            "https://ocw.mit.edu/courses/2-002-mechanics-and-materials-ii-spring-2004/9aebe9fc6669d928aa716a5033cc9c9f_lab_1_s04.pdf#page=6, ideal Euler effective-length factors",
        ]
        result.data["eigen_residuals"] = residuals
        result.data["convergence"] = f"{subdivision} elements per original member; increase elements_per_member and compare load factors."
        result.data["axial_force_ranges"] = [{"member": item["member"], "segment_start": item["segment_start"],
                                               "minimum": result.quantity(item["minimum_axial_force"], "force"),
                                               "maximum": result.quantity(item["maximum_axial_force"], "force"),
                                               "roundoff_bound": result.quantity(item["axial_resolution"], "force")}
                                              for item in forces]
        result.data["buckling_modes"] = [{"mode": index + 1, "load_factor": result.quantity(float(factor))}
                                         for index, factor in enumerate(factors)]
        if len(model.members) == 1:
            member = model.members[0]
            length = float(np.linalg.norm(model.xyz[member.j] - model.xyz[member.i]))
            result.data["pinned_column_reference"] = {
                "about_local_y": result.quantity(math.pi**2 * member.E * member.Iy / length**2, "force"),
                "about_local_z": result.quantity(math.pi**2 * member.E * member.Iz / length**2, "force"),
                "note": "Euler pin-pin reference for comparison; actual frame restraints and load path govern the computed factors.",
            }
        _euler_comparison(model, forces, float(factors[0]), subdivision, result)
        rows = []
        for mode in range(len(factors)):
            for joint, name in enumerate(mesh.ids):
                rows.append([mode + 1, name, *global_shapes[6 * joint:6 * joint + 6, mode], 0.])
            for dof in range(6 * len(mesh.ids), len(mesh.k)):
                rows.append([mode + 1, mesh.labels[dof], *([0.] * 6), float(shapes[dof, mode])])
        artifact = handle.write_text("buckling_modes.csv", csv_text([
            "mode", "node_or_release", "ux_stiffness_normalized", "uy_stiffness_normalized", "uz_stiffness_normalized",
            "rx_stiffness_normalized", "ry_stiffness_normalized", "rz_stiffness_normalized", "private_rotation_stiffness_normalized",
        ], rows), fmt="csv")
        result.artifacts.append(artifact)
        result.data["mode_shape_table"] = {"file": artifact.path, "rows": len(rows), "normalization": "phi.T K phi = 1 in SI; coefficients are not physical displacements"}
        result.add("first_load_factor", float(factors[0]))
        result.add("model_dofs", len(mesh.k))
        return result.done("first_load_factor")

    return run("buckling", inputs, compute)
