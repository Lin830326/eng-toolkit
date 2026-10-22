"""Linear three-dimensional frame tool and station artifacts."""

from typing import Annotated

import numpy as np
from pydantic import Field

from ._common import Result, System, count, csv_text, run
from ._model import compile_frame, equilibrium, global_coordinates, parse_frame, recover, response, solve_static
from ._server import server
from ._types import Loads, MemberLoads, Members, Nodes, Orientations, Releases, Shear, Supports, Thermal


def station_positions(element, end, count_):
    """Add both sides of point forces and exact extrema of the force polynomials."""
    length = element.length
    cuts = sorted({0., length, *[load.a for load in element.loads if load.kind == "point"]})
    points = {(float(x), "left" if x == length else "right") for x in np.linspace(0, length, count_)}
    points.update((x, side) for x in cuts for side in ("left", "right"))
    uniform = sum((load.vector for load in element.loads if load.kind == "uniform"), np.zeros(3))
    for a, b in zip(cuts, cuts[1:]):
        before = sum((load.vector for load in element.loads if load.kind == "point" and load.a <= a), np.zeros(3))
        for axis in (1, 2):
            if uniform[axis] != 0:
                root = -(end[axis]+before[axis])/uniform[axis]
                if a < root < b:
                    points.add((float(root), "right"))
    return sorted(points)


@server.tool(description="3D Euler-Bernoulli/Timoshenko direct-stiffness frames: axial, Saint-Venant torsion, biaxial bending, condensed end releases, springs, settlements and thermal strain. Local station CSVs and global equilibrium. Linear small displacement; no P-delta. Caps: 200 nodes, 400 members, 1200 total DOFs.")
def static(
    nodes: Nodes = "", members: Members = "", supports: Supports = "", loads: Loads = "",
    member_loads: MemberLoads = "", orientations: Orientations = "", releases: Releases = "",
    shear_properties: Shear = "", thermal: Thermal = "",
    stations: Annotated[int, Field(description="Equally spaced stations per member, e.g. 21; integer 2..101; force extrema and point-load sides are added.")] = 21,
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(handle):
        station_count = count(stations, "stations", 2, 101)
        model = parse_frame(nodes, members, supports, loads, member_loads, orientations, releases, shear_properties, thermal)
        mesh = compile_frame(model)
        u, reactions, stability = solve_static(mesh)
        global_u, global_reactions = global_coordinates(mesh, u), global_coordinates(mesh, reactions)
        r = Result(unit_system, {
            "direct_stiffness": "K=sum(A^T ke A); Kff uf=Ff-Kfc uc; six DOFs per node",
            "Euler_Bernoulli": "EA/L axial, GJ/L torsion, Hermite bending in v/rz and w/(-ry)",
            "Timoshenko": "phi_y=12 E Iz/(G Asy L^2); phi_z=12 E Iy/(G Asz L^2); exact constant-section shear flexibility",
            "release_condensation": "Kjj-Kjp Kpp^-1 Kpj; recover private local rotations after joint solve",
            "thermal": "epsilon0=alpha*dT; axial fixed-end actions=(-EA epsilon0,+EA epsilon0)",
            "recovery": "integrate equilibrium and N/EA, T/GJ, My/EIy, Mz/EIz, V/(GAs); point-force jumps retained",
        }, "Uniform prismatic members, linear elasticity and small displacement. Right-handed local axes; default y is projected global Y, or Z when |ex dot Y|>=0.9. End actions act on the element. N and T positive in tension/right-hand twist; internal My/Mz follow local right-hand axes. Loads act in supplied directions.",
                   "200 nodes, 400 members, 800 nodal and member loads each, 1200 assembled DOFs including release rotations, 60000 characters per record parameter. No geometric/material nonlinearity, warping torsion, plasticity, shells or design-code checks.")
        r.data["stability"] = stability
        r.data["conditioning"] = stability["conditioning"]
        r.data["nodes"] = []
        axes = ("ux", "uy", "uz", "rx", "ry", "rz")
        actions = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")
        for i, name in enumerate(model.ids):
            row = {"id": name}
            row.update({axis: r.quantity(global_u[6*i+j], "length" if j < 3 else "angle") for j, axis in enumerate(axes)})
            row.update({"reaction_"+axis: r.quantity(global_reactions[6*i+j], "force" if j < 3 else "moment") for j, axis in enumerate(actions)})
            r.data["nodes"].append(row)
        r.data["equilibrium_residual"] = {axis: r.quantity(value, "force" if j < 3 else "moment") for j, (axis, value) in enumerate(zip(actions, equilibrium(mesh, reactions)))}
        r.data["equilibrium_origin"] = "Global coordinate origin (0,0,0); applied nodal/member loads plus fixed and spring reactions."
        r.data["members"] = []
        names = ("N", "Vy", "Vz", "T", "My", "Mz", "u", "v", "w", "rx", "ry", "rz")
        maxima = {name: (0., model.members[0].id, 0.) for name in names}
        for index, element in enumerate(mesh.elements):
            d, end = recover(element, u)
            rows = []
            for x, side in station_positions(element, end, station_count):
                values = response(element, d, end, x, side)
                rows.append([x, side, *values.tolist()])
                for name, value in zip(names, values):
                    if abs(value) > maxima[name][0]:
                        maxima[name] = (abs(float(value)), element.member.id, x)
            artifact = handle.write_text(f"member_{index+1}_stations.csv", csv_text(
                ["x_m", "side", "N_N", "Vy_N", "Vz_N", "T_N_m", "My_N_m", "Mz_N_m", "u_m", "v_m", "w_m", "rx_rad", "ry_rad", "rz_rad"], rows), fmt="csv")
            r.artifacts.append(artifact)
            r.data["members"].append({"id": element.member.id, "local_axes": element.member.R.tolist(),
                "theory": "Euler-Bernoulli" if element.member.Asy is None else "Timoshenko",
                "end_forces": {f"{axis}_{end_name}": r.quantity(end[6*end_index+j], "force" if j < 3 else "moment")
                               for end_index, end_name in enumerate(("start", "end")) for j, axis in enumerate(actions)},
                "local_end_displacements": d.tolist(),
                "table": {"file": artifact.path, "rows": len(rows), "units": "SI, local axes"}})
        r.data["maxima"] = {}
        for j, name in enumerate(names):
            kind = "force" if j < 3 else "moment" if j < 6 else "length" if j < 9 else "angle"
            value, member, x = maxima[name]
            r.add("maximum_abs_"+name, value, kind)
            r.data["maxima"][name] = {"value": r.quantity(value, kind), "member": member, "x": r.quantity(x, "length")}
        r.add("maximum_nodal_displacement", float(np.max(np.linalg.norm(u[:6*len(model.ids)].reshape(-1, 6)[:, :3], axis=1))), "length")
        r.data["maximum_scope"] = "Force/moment maxima include exact stationary points between point-force jumps. Displacement/rotation maxima are sampled on the stated station grid."
        if any(np.linalg.norm(u[6*m.j:6*m.j+3]-u[6*m.i:6*m.i+3])/float(np.linalg.norm(model.xyz[m.j]-model.xyz[m.i])) > .05 for m in model.members) or np.max(np.abs(global_u[:6*len(model.ids)].reshape(-1, 6)[:, 3:])) > .05:
            r.warnings.append("Relative displacement/length or rotation exceeds 0.05; small-displacement validity needs review.")
        r.warnings.append("Station CSVs use SI units; displacement and rotation maxima are sampled. Point-force jumps include left and right limits.")
        return r.done("maximum_nodal_displacement")

    return run("static", inputs, compute)
