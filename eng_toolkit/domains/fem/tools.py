"""``fem`` sub-server: tetrahedral meshing (Gmsh) and linear static analysis (CalculiX) of a solid CAD file.

The tools read a STEP, IGES or BREP solid through the same loader as ``geometry_measure`` (millimetres), hand it to Gmsh as a BREP
file the toolkit writes, and write the CalculiX deck themselves.  No script, deck or include file ever comes from the caller; the
selector language (:mod:`.selectors`) is parsed into numbers.  ``docs/fem/design.md`` says what is exact and what is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import numpy as np
from fastmcp import FastMCP
from pydantic import Field

from eng_toolkit import __version__, brep
from eng_toolkit import external as ext
from eng_toolkit.contract import Artifact, QuantityOut, ToolResult
from eng_toolkit.domains.fem import mesh as meshing
from eng_toolkit.domains.fem import selectors as sel
from eng_toolkit.domains.fem import solver
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import CCX, GMSH
from eng_toolkit.licenses import install_hint
from eng_toolkit.runner import Computed, JobHandle, run_tool
from eng_toolkit.units import UnitsError, parse_quantity, require_positive, ureg
from eng_toolkit.workspace import sha256_file

ENGINE_MESH = "eng_toolkit.fem.mesh"
ENGINE_STATIC = "eng_toolkit.fem.static"
TOOL_NAMES: tuple[str, ...] = ("mesh", "static")
MIN_YOUNG_MPA, MAX_YOUNG_MPA = 1e-3, 1e7
MAX_POISSON = 0.5  # exclusive
MIN_POISSON = -1.0  # exclusive
MAX_NOMINAL_STRAIN = 10.0  # a traction this many times the modulus is refused: linear elasticity means nothing there
EQUILIBRIUM_FORCE_TOL = 1e-6
EQUILIBRIUM_MOMENT_TOL = 1e-5
SMALL_DISPLACEMENT = 0.05  # of the part's diagonal: above it the small-displacement assumption is called into question

server = FastMCP(
    "fem",
    instructions=(
        "Finite element meshing and linear static analysis of solid CAD files. Every quantity carries a unit. "
        "Results are unreviewed engineering estimates and must be checked by a qualified engineer."
    ),
)


def _invalid(message: str, **details: Any) -> UnitsError:
    return UnitsError("input.invalid", message, details)


def _job_dir(job: JobHandle) -> Path:
    # JobHandle has no public accessor for its directory yet; the programs need one to run in.
    return Path(job._job.dir)  # noqa: SLF001


def _program_dir(job: JobHandle, name: str) -> Path:
    path = _job_dir(job) / name
    path.mkdir(exist_ok=True)
    return path


def _keep(job: JobHandle, source: Path, name: str, fmt: str) -> Artifact:
    """Copy a file a program wrote into the job directory's top level so it is hashed into the manifest, and name it."""
    return job.write_bytes(name, source.read_bytes(), fmt=fmt)


def _require_programs(*tools) -> None:
    """Fail at once, before any file is read or meshed, when a program the tool needs is not installed."""
    for tool in tools:
        located, why = ext.locate(tool)
        if located is None:
            raise ext.unavailable(tool, why)


def _length_unit(text: str, name: str):
    unit = parse_quantity(f"1 {text}", name=name, dimension="length").units
    return unit, float(ureg.Quantity(1.0, "millimeter").to(unit).magnitude)


def _stress_unit(text: str, name: str):
    unit = parse_quantity(f"1 {text}", name=name, dimension="pressure").units
    return unit, float(ureg.Quantity(1.0, "megapascal").to(unit).magnitude)


def _q(value: float, unit: Any) -> QuantityOut:
    return QuantityOut(quantity=float(f"{value:.12g}"), unit=str(unit))


def _count(value: int) -> QuantityOut:
    return QuantityOut(quantity=float(value), unit="dimensionless")


def _element_size_mm(text: str) -> float:
    qty = require_positive(parse_quantity(text, name="element_size", dimension="length"), name="element_size")
    size = float(qty.to("millimeter").magnitude)
    if not math.isfinite(size):
        raise _invalid("element_size is not finite", param="element_size")
    return size


@dataclass
class Meshed:
    solid: Any
    volume_mm3: float
    mesh: meshing.TetMesh
    quality: meshing.Quality
    mesh_volume_mm3: float
    estimated: float
    record: ext.Record
    msh_path: Path
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    warnings: list[str]


def _mesh_model(job: JobHandle, model_path: str, size_mm: float) -> Meshed:
    """Read the solid, refuse what cannot be meshed, mesh it with Gmsh and check the mesh."""
    model_file = job.input_file(model_path, param="model_path", extensions=brep.SOLID_EXTENSIONS)
    solid = brep.read_solid(model_file, param="model_path")
    shown = job.input_files[-1].path
    if solid.faces == 0:
        raise ToolFailure("input.invalid", f"model_path: no faces in {shown!r}", {"path": shown})
    props = brep.mass_properties(solid)
    if props.volume_mm3 is None:
        raise ToolFailure(
            "input.invalid",
            f"model_path: {shown!r} holds no closed solid with positive volume (solids found: {solid.solids}); a finite element model needs one"
            + ("; IGES stores surfaces unless the exporter wrote a solid" if model_file.suffix.lower() in (".iges", ".igs") else ""),
            {"path": shown, "solids": solid.solids},
        )
    if solid.solids != 1:
        raise ToolFailure(
            "input.unsupported",
            f"model_path: the file holds {solid.solids} solids; this tool meshes exactly one (separate bodies cannot be tied together here)",
            {"path": shown, "solids": solid.solids},
        )
    warnings: list[str] = []
    if solid.roots_transferred < solid.roots_in_file:
        warnings.append(
            f"Only {solid.roots_transferred} of {solid.roots_in_file} root entities of the file could be transferred: the model may be incomplete"
        )
    estimate = meshing.check_before_meshing(props.volume_mm3, props.bbox_min_mm, props.bbox_max_mm, size_mm)
    msh_path, record = meshing.run_gmsh(solid, size_mm, _program_dir(job, "gmsh"))
    tetra = meshing.read_msh(msh_path, size_mm)
    if len(tetra.tets) > meshing.MAX_ELEMENTS or len(tetra.nodes) > meshing.MAX_NODES:
        raise ToolFailure(
            "input.too_large",
            f"Gmsh made {len(tetra.tets)} elements and {len(tetra.nodes)} nodes; the limits are {meshing.MAX_ELEMENTS} and {meshing.MAX_NODES} "
            f"(the estimate was {estimate:.0f}: thin walls and curvature need more elements than the volume suggests). Use a larger element size",
            {"elements": len(tetra.tets), "nodes": len(tetra.nodes), "max_elements": meshing.MAX_ELEMENTS, "max_nodes": meshing.MAX_NODES, "estimated_elements": estimate},
        )
    quality, mesh_volume = meshing.check_and_measure(tetra)
    pieces = meshing.connected_components(tetra)
    if pieces != 1:
        raise ToolFailure("engine.failure", f"the mesh falls into {pieces} separate pieces", {"pieces": pieces})
    extent = [hi - lo for lo, hi in zip(props.bbox_min_mm, props.bbox_max_mm)]
    if min(extent) < 2.0 * size_mm:
        warnings.append(
            f"the element size ({size_mm:.4g} mm) is more than half the thinnest side of the bounding box ({min(extent):.4g} mm): "
            "fewer than two elements across it; bending and stress results will be poor"
        )
    if quality.slivers:
        warnings.append(
            f"{quality.slivers} sliver element(s) (mean ratio below {meshing.SLIVER_QUALITY}); the worst is element {quality.worst_element} with {quality.min_ratio:.3g}. "
            "Stresses near it are unreliable; try another element size"
        )
    elif quality.min_ratio < meshing.WEAK_QUALITY:
        warnings.append(f"the worst element has a mean ratio of {quality.min_ratio:.3g} (below {meshing.WEAK_QUALITY}); look at the stresses near it with care")
    relative = (mesh_volume - props.volume_mm3) / props.volume_mm3
    if abs(relative) > 1e-3:
        warnings.append(
            f"the meshed volume differs from the exact B-rep volume by {relative:+.3%}; curved surfaces are approximated by the element faces, "
            "so a smaller element size reduces it"
        )
    return Meshed(solid, props.volume_mm3, tetra, quality, mesh_volume, estimate, record, msh_path, props.bbox_min_mm, props.bbox_max_mm, warnings)


MESH_CONVENTIONS: dict[str, str] = {
    "element": "10-node quadratic tetrahedron (CalculiX C3D10; Gmsh type 11), straight or curved along the boundary",
    "quality": (
        "mean ratio of the four corner nodes, 12 (3V)^(2/3) / (sum of the six squared edge lengths) (Liu and Joe): 1 for a regular tetrahedron, "
        "0 for a flat one. It is computed by the toolkit, not Gmsh's own SICN, which is a different number"
    ),
    "min_jacobian_ratio": "smallest det J over an element (corners, midside points, centre) divided by its largest; 1 for straight edges, 0 or less = invalid",
    "meshed_volume": "volume of the quadratic elements (exact quadrature), compared with the exact B-rep volume",
    "units": "STEP and IGES carry their unit and are read in millimetres; BREP numbers are taken as millimetres",
}


@server.tool(
    name="mesh",
    description=(
        "Tetrahedral finite element mesh (second-order, 10-node) of one solid in a CAD file (.step .stp .iges .igs .brep, read in "
        "millimetres, same loader as geometry_measure), made with Gmsh. Give the target element size with a unit, e.g. '5 mm'. Returns "
        "node and element counts, element quality statistics (mean ratio: 1 regular, 0 flat), the meshed volume against the exact B-rep "
        "volume, and warnings for slivers and coarse meshes; the mesh is an artifact (.msh, ASCII format 2). The work is capped before "
        "meshing: elements are estimated as 6*sqrt(2)*volume/size^3 and a size that needs more than "
        f"{meshing.MAX_ELEMENTS} elements, or is below 1/{round(1 / meshing.MIN_SIZE_FRACTION)} of the part's diagonal, is refused as "
        "input.too_large. One solid only. The caller never supplies a Gmsh script. "
        f"Needs the optional cad engine ({install_hint('cadquery-ocp-novtk')}) and Gmsh installed by you (set ENG_TOOLKIT_GMSH or put gmsh on PATH; "
        "run toolkit_doctor to see)."
    ),
)
def mesh(
    model_path: Annotated[str, Field(description="Path to a solid model: .step .stp .iges .igs or .brep")],
    element_size: Annotated[str, Field(description="Target element edge length with unit, e.g. '5 mm' or '0.2 inch'")],
    output_unit: Annotated[str, Field(description="Length unit of the volumes in the result, e.g. 'mm' (default), 'm', 'inch'")] = "mm",
) -> ToolResult:
    inputs = {"model_path": model_path, "element_size": element_size, "output_unit": output_unit}

    def compute(job: JobHandle) -> Computed:
        unit, per_mm = _length_unit(output_unit, "output_unit")
        size_mm = _element_size_mm(element_size)
        _require_programs(GMSH)
        meshed = _mesh_model(job, model_path, size_mm)
        q, t = meshed.quality, meshed.mesh
        relative = (meshed.mesh_volume_mm3 - meshed.volume_mm3) / meshed.volume_mm3
        artifact = _keep(job, meshed.msh_path, "mesh.msh", "msh")
        values = {
            "element_count": _count(len(t.tets)),
            "node_count": _count(len(t.nodes)),
            "mesh_volume": _q(meshed.mesh_volume_mm3 * per_mm**3, unit**3),
            "exact_volume": _q(meshed.volume_mm3 * per_mm**3, unit**3),
            "volume_relative_difference": QuantityOut(quantity=float(f"{relative:.6g}"), unit="dimensionless"),
            "min_quality": QuantityOut(quantity=float(f"{q.min_ratio:.6g}"), unit="dimensionless"),
            "mean_quality": QuantityOut(quantity=float(f"{q.mean_ratio:.6g}"), unit="dimensionless"),
        }
        data = {
            "element_type": "C3D10 (10-node tetrahedron)",
            "element_size_mm": size_mm,
            "estimated_elements": float(f"{meshed.estimated:.6g}"),
            "limits": {"max_elements": meshing.MAX_ELEMENTS, "max_nodes": meshing.MAX_NODES, "min_size_fraction_of_diagonal": meshing.MIN_SIZE_FRACTION},
            "quality": {
                "measure": "mean ratio of the corner nodes (1 regular, 0 flat)",
                "min": q.min_ratio,
                "mean": q.mean_ratio,
                "median": q.median_ratio,
                "slivers_below": meshing.SLIVER_QUALITY,
                "slivers": q.slivers,
                "worst_element": q.worst_element,
                "min_jacobian_ratio": q.min_jacobian_ratio,
            },
            "bbox_min_mm": list(meshed.bbox_min),
            "bbox_max_mm": list(meshed.bbox_max),
            "gmsh": {"found_via": meshed.record.source if hasattr(meshed.record, "source") else None, "seconds": round(meshed.record.seconds, 3), "version": _version(GMSH)},
            "conventions": MESH_CONVENTIONS,
        }
        return Computed(values=values, primary="element_count", warnings=meshed.warnings, data=data, artifacts=[artifact])

    return run_tool(
        tool_name="fem_mesh",
        engine=ENGINE_MESH,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "A mesh is not a result: element size drives every later number. Run fem_static on two sizes and compare before trusting a value.",
        ),
    )


def _version(tool) -> str | None:
    located, _ = ext.locate(tool)
    return ext.version(tool, located) if located else None


# --------------------------------------------------------------------------- fem_static

STATIC_CONVENTIONS: dict[str, str] = {
    **MESH_CONVENTIONS,
    "frame": "global coordinates of the file, millimetres; displacement components ux, uy, uz follow x, y, z",
    "face_selector": (
        "'x = 0 mm' selects every boundary facet whose nodes lie in the plane x = 0 within 1e-6 of the part's diagonal; "
        "'normal +z at z = 20 mm' also needs the outward normal of the facet to point to +z"
    ),
    "support": "'fixed' sets ux = uy = uz = 0 on the selected nodes; 'ux', 'uy', 'uz' constrain only that component; 'point(...)' is the nearest mesh node",
    "force": "'force (Fx, Fy, Fz) on <face>' is a total force, spread over the selected facets by area (uniform traction), applied as consistent nodal forces of the quadratic facets",
    "pressure": "'pressure p on <face>' is normal to the surface, positive pressing into the material",
    "reaction": "sum of CalculiX's nodal forces RF (the internal force K u) at the constrained nodes, less any load applied at those nodes: the force the supports exert on the body, equal to minus the applied load",
    "von_mises": "sqrt(0.5 ((sxx-syy)^2 + (syy-szz)^2 + (szz-sxx)^2) + 3 (sxy^2 + syz^2 + szx^2)) of the nodal stresses CalculiX writes (extrapolated from the integration points and averaged between elements)",
    "equilibrium": (
        f"force residual |reaction + applied| / (sum of the nodal applied forces' magnitudes) must be below {EQUILIBRIUM_FORCE_TOL:g}, and the moment "
        f"residual about the origin below {EQUILIBRIUM_MOMENT_TOL:g} of its scale, or the call fails"
    ),
}

CSV_COLUMNS = ("node", "x_mm", "y_mm", "z_mm", "ux_mm", "uy_mm", "uz_mm", "u_mag_mm", "sxx_mpa", "syy_mpa", "szz_mpa", "sxy_mpa", "syz_mpa", "szx_mpa", "von_mises_mpa")


def _csv(mesh_: meshing.TetMesh, u: np.ndarray, s: np.ndarray, vm: np.ndarray) -> str:
    mag = np.linalg.norm(u, axis=1)
    table = np.column_stack([np.arange(1, len(mesh_.nodes) + 1), mesh_.nodes, u, mag, s, vm])
    rows = [",".join(CSV_COLUMNS)]
    rows += [f"{int(r[0])}," + ",".join(f"{v:.9g}" for v in r[1:]) for r in table]
    return "\n".join(rows) + "\n"


@server.tool(
    name="static",
    description=(
        "Linear static finite element analysis (small displacements, linear elastic isotropic material, 10-node tetrahedra) of one solid in a CAD file "
        "(.step .stp .iges .igs .brep, read in millimetres), meshed by Gmsh and solved by CalculiX; the toolkit writes both inputs, the caller never "
        "supplies a script or deck. Returns the maximum displacement magnitude and where it is, the maximum von Mises stress and where, and the total "
        "reaction force against the applied load (equilibrium must hold to 1e-6 or the call fails). Artifacts: the CalculiX deck, .frd and .dat results, "
        "and results.csv with every node's displacement and stress. "
        "'supports' and 'loads' are ';'-separated clauses that pick faces by plane. Face selector: 'x = 0 mm' (all boundary faces lying in that plane) "
        "or 'normal +z at z = 20 mm' (only faces whose outward normal is +z); axis x, y or z. Support: '<face> [: dofs]' with dofs 'fixed' (default, "
        "ux=uy=uz=0) or any of ux uy uz, or 'point(0 mm, 5 mm, 0 mm) : uy' (the nearest mesh node, within one element size). Load: "
        "'force (0 N, 0 N, -10 kN) on x = 100 mm' (total force spread evenly over the face by area) or 'pressure 5 MPa on z = 20 mm' (positive "
        "pushes into the material). A selector that matches no face, a support face that is also loaded, and a model whose supports leave a rigid-body "
        "motion free are errors that say so. Fixing a whole face over-constrains the Poisson contraction there (the stress near it is higher than the "
        "textbook value); to avoid that, constrain only ux on the face and add point supports on the remaining motions. "
        f"The work is capped (max {meshing.MAX_ELEMENTS} elements, estimated before meshing). Linear only: no plasticity, contact, large "
        "displacement, dynamics or temperature. "
        f"Needs the optional cad engine ({install_hint('cadquery-ocp-novtk')}) and Gmsh and CalculiX installed by you (ENG_TOOLKIT_GMSH, ENG_TOOLKIT_CCX; run toolkit_doctor)."
    ),
)
def static(
    model_path: Annotated[str, Field(description="Path to a solid model: .step .stp .iges .igs or .brep")],
    element_size: Annotated[str, Field(description="Target element edge length with unit, e.g. '2.5 mm'; halve it to check convergence")],
    youngs_modulus: Annotated[str, Field(description="Young's modulus with unit, e.g. '210 GPa' or '210000 MPa'")],
    poisson_ratio: Annotated[float, Field(description="Poisson's ratio, a plain number between -1 and 0.5 (exclusive), e.g. 0.3")],
    supports: Annotated[
        str,
        Field(description="';'-separated supports, e.g. 'x = 0 mm' (fixed face) or 'x = 0 mm : ux; point(0 mm, 5 mm, 0 mm) : uy; point(0 mm, 0 mm, 5 mm) : uz'"),
    ],
    loads: Annotated[
        str,
        Field(description="';'-separated loads, e.g. 'force (10 kN, 0 N, 0 N) on x = 100 mm' or 'pressure 2 MPa on normal +z at z = 20 mm'"),
    ],
    output_length_unit: Annotated[str, Field(description="Length unit of displacements, e.g. 'mm' (default), 'um', 'inch'")] = "mm",
    output_stress_unit: Annotated[str, Field(description="Unit of stresses, e.g. 'MPa' (default), 'psi', 'bar'")] = "MPa",
) -> ToolResult:
    inputs = {
        "model_path": model_path,
        "element_size": element_size,
        "youngs_modulus": youngs_modulus,
        "poisson_ratio": poisson_ratio,
        "supports": supports,
        "loads": loads,
        "output_length_unit": output_length_unit,
        "output_stress_unit": output_stress_unit,
    }

    def compute(job: JobHandle) -> Computed:
        length_unit, per_mm = _length_unit(output_length_unit, "output_length_unit")
        stress_unit, per_mpa = _stress_unit(output_stress_unit, "output_stress_unit")
        young = require_positive(parse_quantity(youngs_modulus, name="youngs_modulus", dimension="pressure"), name="youngs_modulus")
        young_mpa = float(young.to("megapascal").magnitude)
        if not (math.isfinite(young_mpa) and MIN_YOUNG_MPA <= young_mpa <= MAX_YOUNG_MPA):
            raise UnitsError(
                "input.out_of_range",
                f"'youngs_modulus' = {young:~P} is outside the supported range {MIN_YOUNG_MPA:g} .. {MAX_YOUNG_MPA:g} MPa",
                {"param": "youngs_modulus", "min": MIN_YOUNG_MPA, "max": MAX_YOUNG_MPA, "unit": "MPa"},
            )
        if isinstance(poisson_ratio, bool) or not isinstance(poisson_ratio, (int, float)) or not math.isfinite(poisson_ratio):
            raise _invalid(f"'poisson_ratio' must be a plain number, got {poisson_ratio!r}", param="poisson_ratio")
        nu = float(poisson_ratio)
        if not (MIN_POISSON < nu < MAX_POISSON):
            raise _invalid(
                f"'poisson_ratio' must be above {MIN_POISSON:g} and below {MAX_POISSON:g}, got {nu:g}; at 0.5 the material is incompressible and the elasticity matrix is singular",
                param="poisson_ratio",
            )
        size_mm = _element_size_mm(element_size)
        support_list = sel.parse_supports(supports)
        load_list = sel.parse_loads(loads)
        _require_programs(GMSH, CCX)

        meshed = _mesh_model(job, model_path, size_mm)
        warnings = list(meshed.warnings)
        if nu >= 0.45:
            warnings.append(f"Poisson's ratio {nu:g} is close to incompressible; second-order tetrahedra lock volumetrically and stresses become noisy as it approaches 0.5")
        tetra = meshed.mesh
        facets = meshing.boundary_facets(tetra)
        diagonal = tetra.diagonal_mm
        n_nodes = len(tetra.nodes)

        # ----- supports -> constrained degrees of freedom
        fixed = np.zeros((n_nodes, 3), dtype=bool)
        support_report: list[dict[str, Any]] = []
        support_facets: dict[int, str] = {}  # facet -> the support clause that holds it
        point_nodes: dict[int, str] = {}  # node -> the point support clause that holds it
        for item in support_list:
            if isinstance(item.target, sel.PointSelector):
                gap = np.linalg.norm(tetra.nodes - np.array(item.target.xyz_mm), axis=1)
                nearest = int(np.argmin(gap))
                if gap[nearest] > size_mm:
                    raise ToolFailure(
                        "fem.point_not_on_model",
                        f"the point selector {item.target.text!r} is {gap[nearest]:.4g} mm from the nearest mesh node, more than the element size ({size_mm:.4g} mm); it is not on the model",
                        {"selector": item.target.text, "distance_mm": float(gap[nearest]), "element_size_mm": size_mm},
                    )
                fixed[nearest] |= np.array(item.dofs)
                point_nodes[nearest] = item.text
                support_report.append(
                    {
                        "text": item.text,
                        "kind": "point",
                        "dofs": [a for a, on in zip("xyz", item.dofs) if on],
                        "node": nearest + 1,
                        "node_mm": [float(v) for v in tetra.nodes[nearest]],
                        "snap_distance_mm": float(gap[nearest]),
                    }
                )
                if gap[nearest] > 1e-6 * diagonal:
                    warnings.append(
                        f"support {item.text!r} was moved {gap[nearest]:.4g} mm to the nearest mesh node at {tuple(round(float(v), 6) for v in tetra.nodes[nearest])} mm; "
                        "a point support is exact only where the mesh has a node"
                    )
            else:
                chosen = sel.select_facets(item.target, tetra.nodes, facets.corners, facets.mids, facets.normals, diagonal)
                if len(chosen) == 0:
                    raise sel.empty_selection(item.target.text, "support", diagonal)
                nodes = meshing.nodes_of(facets, chosen)
                fixed[np.ix_(nodes, [d for d in range(3) if item.dofs[d]])] = True
                support_facets.update({int(f): item.text for f in chosen})
                support_report.append(
                    {
                        "text": item.text,
                        "kind": "face",
                        "dofs": [a for a, on in zip("xyz", item.dofs) if on],
                        "facets": int(len(chosen)),
                        "nodes": int(len(nodes)),
                        "area_mm2": float(facets.areas[chosen].sum()),
                    }
                )

        # ----- loads -> nodal forces
        force = np.zeros((n_nodes, 3))
        load_report: list[dict[str, Any]] = []
        loaded_facets: dict[int, str] = {}
        loaded_nodes: set[int] = set()
        strain_scale = 0.0
        for item in load_list:
            chosen = sel.select_facets(item.target, tetra.nodes, facets.corners, facets.mids, facets.normals, diagonal)
            if len(chosen) == 0:
                raise sel.empty_selection(item.target.text, "load", diagonal)
            scalar, vector, area = meshing.facet_integrals(tetra, facets, chosen)
            total_area = float(area.sum())
            nodes6 = np.concatenate([facets.corners[chosen], facets.mids[chosen]], axis=1)
            if item.kind == "force":
                total = np.array(item.force_n)
                share = scalar / total_area  # (f, 6)
                contribution = share[:, :, None] * total[None, None, :]
                resultant = total
                strain_scale = max(strain_scale, float(np.linalg.norm(total)) / total_area / young_mpa)
            else:
                contribution = -item.pressure_mpa * vector  # n is outward: positive pressure acts against it
                resultant = contribution.sum(axis=(0, 1))
                strain_scale = max(strain_scale, abs(item.pressure_mpa) / young_mpa)
            np.add.at(force, nodes6.ravel(), contribution.reshape(-1, 3))
            loaded_nodes.update(int(n) for n in nodes6.ravel())
            loaded_facets.update({int(f): item.text for f in chosen})
            load_report.append(
                {
                    "text": item.text,
                    "kind": item.kind,
                    "facets": int(len(chosen)),
                    "nodes": int(len(np.unique(nodes6))),
                    "area_mm2": total_area,
                    "resultant_n": [float(v) for v in resultant],
                }
            )
        if strain_scale > MAX_NOMINAL_STRAIN:
            raise UnitsError(
                "input.out_of_range",
                f"the load is {strain_scale:.3g} times the Young's modulus as a stress on its face; a linear elastic analysis has no meaning at that size (the limit is {MAX_NOMINAL_STRAIN:g})",
                {"nominal_strain": strain_scale, "max": MAX_NOMINAL_STRAIN},
            )
        shared = sorted(set(support_facets) & set(loaded_facets))
        if shared:
            raise ToolFailure(
                "fem.overlapping_selection",
                f"{len(shared)} face(s) are both a support ({support_facets[shared[0]]!r}) and loaded ({loaded_facets[shared[0]]!r}): "
                "a load on a support goes straight into the reaction and tells nothing. Select different faces",
                {"facets": len(shared), "support": support_facets[shared[0]], "load": loaded_facets[shared[0]]},
            )
        on_load = sorted(n for n in point_nodes if n in loaded_nodes)
        if on_load:
            node = on_load[0]
            raise ToolFailure(
                "fem.overlapping_selection",
                f"the point support {point_nodes[node]!r} was placed on node {node + 1} at {tuple(round(float(v), 6) for v in tetra.nodes[node])} mm, "
                f"which belongs to a loaded face ({len(on_load)} such node(s)): a load on a support goes straight into the reaction. Move the point",
                {"nodes": len(on_load), "example_node": node + 1, "support": point_nodes[node]},
            )

        # ----- rigid body motion
        free = solver.free_rigid_motions(tetra.nodes, fixed)
        if free:
            raise ToolFailure(
                "fem.not_constrained",
                f"the supports do not hold the model: {len(free)} rigid-body motion(s) stay free ({'; '.join(free)}). "
                "Prevent all of translation along x, y, z and rotation about x, y, z, for instance by fixing a face or with three non-collinear point supports",
                {"free_motions": free},
            )

        # ----- solve
        solved = solver.solve(tetra, young_mpa, nu, fixed, force, _program_dir(job, "calculix"))
        u = solved.displacement
        stress = solved.stress
        vm = solver.von_mises(stress)
        magnitude = np.linalg.norm(u, axis=1)
        imax, ivm = int(np.argmax(magnitude)), int(np.argmax(vm))

        # ----- equilibrium
        applied = force.sum(axis=0)
        # CalculiX's RF is the internal force K u of a node: at a constrained node that is the support's reaction plus whatever load acts there
        # (loads and supports share only edge nodes, never a facet, but a shared node can carry both), so the load is taken off again.
        held = solved.dat.reaction_nodes
        node_reaction = solved.dat.reaction - force[held]  # per constrained node, in the print precision of CalculiX (seven digits)
        reaction = solved.dat.reaction_total - force[held].sum(axis=0)  # the total CalculiX summed itself before printing: more accurate than the sum of node_reaction
        scale_force = float(np.linalg.norm(force, axis=1).sum())
        residual = reaction + applied
        relative_force = float(np.linalg.norm(residual)) / scale_force
        centre = tetra.nodes.mean(axis=0)
        support_x = tetra.nodes[held] - centre
        applied_x = tetra.nodes - centre
        moment_residual = np.cross(support_x, node_reaction).sum(axis=0) + np.cross(applied_x, force).sum(axis=0)
        scale_moment = float((np.linalg.norm(applied_x, axis=1) * np.linalg.norm(force, axis=1)).sum() + (np.linalg.norm(support_x, axis=1) * np.linalg.norm(node_reaction, axis=1)).sum())
        relative_moment = float(np.linalg.norm(moment_residual)) / scale_moment if scale_moment > 0 else 0.0
        if relative_force > EQUILIBRIUM_FORCE_TOL or relative_moment > EQUILIBRIUM_MOMENT_TOL:
            raise ToolFailure(
                "fem.equilibrium_failed",
                f"the reactions do not balance the applied load: force residual {relative_force:.3g} (limit {EQUILIBRIUM_FORCE_TOL:g}), moment residual {relative_moment:.3g} "
                f"(limit {EQUILIBRIUM_MOMENT_TOL:g}); the result is discarded",
                {
                    "applied_n": [float(v) for v in applied],
                    "reaction_n": [float(v) for v in reaction],
                    "force_residual_relative": relative_force,
                    "moment_residual_relative": relative_moment,
                },
            )

        if float(magnitude[imax]) > SMALL_DISPLACEMENT * diagonal:
            warnings.append(
                f"the largest displacement ({magnitude[imax]:.4g} mm) is more than {SMALL_DISPLACEMENT:.0%} of the part's diagonal ({diagonal:.4g} mm): "
                "the small-displacement linear analysis is probably not valid for this load"
            )
        if support_report and any(r["kind"] == "face" and r["dofs"] == ["x", "y", "z"] for r in support_report):
            warnings.append(
                "a fully fixed face prevents the cross-section from contracting (Poisson effect) at the support: stress and deflection near it differ from beam theory"
            )

        # ----- artifacts
        artifacts = [
            _keep(job, _program_dir(job, "calculix") / f"{solver.JOB}.inp", "model.inp", "inp"),
            _keep(job, _program_dir(job, "calculix") / f"{solver.JOB}.frd", "result.frd", "frd"),
            _keep(job, _program_dir(job, "calculix") / f"{solver.JOB}.dat", "result.dat", "dat"),
            job.write_text("results.csv", _csv(tetra, u, stress, vm), fmt="csv"),
            _keep(job, meshed.msh_path, "mesh.msh", "msh"),
        ]

        values = {
            "max_displacement": _q(float(magnitude[imax]) * per_mm, length_unit),
            "max_von_mises": _q(float(vm[ivm]) * per_mpa, stress_unit),
            "applied_force_x": _q(float(applied[0]), "newton"),
            "applied_force_y": _q(float(applied[1]), "newton"),
            "applied_force_z": _q(float(applied[2]), "newton"),
            "reaction_force_x": _q(float(reaction[0]), "newton"),
            "reaction_force_y": _q(float(reaction[1]), "newton"),
            "reaction_force_z": _q(float(reaction[2]), "newton"),
            "equilibrium_residual": QuantityOut(quantity=float(f"{relative_force:.6g}"), unit="dimensionless"),
            "element_count": _count(len(tetra.tets)),
            "node_count": _count(n_nodes),
        }
        q = meshed.quality
        data = {
            "max_displacement_at_mm": [float(v) for v in tetra.nodes[imax]],
            "max_displacement_node": imax + 1,
            "max_displacement_vector_mm": [float(v) for v in u[imax]],
            "max_von_mises_at_mm": [float(v) for v in tetra.nodes[ivm]],
            "max_von_mises_node": ivm + 1,
            "max_displacement_components_mm": [float(np.abs(u[:, k]).max()) for k in range(3)],
            "equilibrium": {
                "applied_n": [float(v) for v in applied],
                "reaction_n": [float(v) for v in reaction],
                "residual_n": [float(v) for v in residual],
                "force_residual_relative": relative_force,
                "force_limit": EQUILIBRIUM_FORCE_TOL,
                "moment_residual_relative": relative_moment,
                "moment_limit": EQUILIBRIUM_MOMENT_TOL,
                "scale_n": scale_force,
            },
            "supports": support_report,
            "loads": load_report,
            "material": {"youngs_modulus_mpa": young_mpa, "poisson_ratio": nu, "model": "linear elastic, isotropic, small displacements"},
            "mesh": {
                "element_type": "C3D10 (10-node tetrahedron)",
                "element_size_mm": size_mm,
                "elements": len(tetra.tets),
                "nodes": n_nodes,
                "estimated_elements": float(f"{meshed.estimated:.6g}"),
                "volume_relative_difference": (meshed.mesh_volume_mm3 - meshed.volume_mm3) / meshed.volume_mm3,
                "quality_min": q.min_ratio,
                "quality_mean": q.mean_ratio,
                "slivers": q.slivers,
                "min_jacobian_ratio": q.min_jacobian_ratio,
            },
            "solver": {
                "ccx_version": _version(CCX),
                "gmsh_version": _version(GMSH),
                "ccx_seconds": round(solved.record.seconds, 3),
                "gmsh_seconds": round(meshed.record.seconds, 3),
                "files": "model.inp, result.frd, result.dat, results.csv, mesh.msh in the job directory",
                "csv_columns": list(CSV_COLUMNS),
            },
            "conventions": STATIC_CONVENTIONS,
        }
        return Computed(values=values, primary="max_displacement", warnings=warnings, data=data, artifacts=artifacts)

    return run_tool(
        tool_name="fem_static",
        engine=ENGINE_STATIC,
        version=__version__,
        inputs=inputs,
        compute=compute,
        engines=("cadquery-ocp-novtk", "numpy"),
        standing_warnings=(
            "Linear static, small-displacement analysis of one elastic solid: no plasticity, contact, large displacement, buckling, dynamics or temperature. "
            "Peak stresses depend on the mesh and grow without bound at point loads, supports and sharp corners; compare two element sizes. Not a design verification.",
        ),
    )
