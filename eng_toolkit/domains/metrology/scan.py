"""Scan-to-model deviation: a point cloud measured against a STEP model.

Pure computation on millimetres and NumPy arrays: no MCP and no pint.  The model is read and
tessellated by :mod:`eng_toolkit.brep` (the one owner of OpenCASCADE), an Open3D ``RaycastingScene``
finds the closest triangle of each scan point, the distance is then recomputed in float64 and the
optional registration is Open3D point-to-plane ICP.  Both engines are optional (the ``scan`` extra),
so they are imported inside functions and a missing one raises :class:`EngineUnavailable`.

Deviation is the signed distance from a scan point to the model surface, positive outside the
material.  A mesh that is not one closed solid has no well-defined inside, so it is measured
unsigned.
"""

from __future__ import annotations

import contextlib
import math
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from eng_toolkit import brep
from eng_toolkit.errors import EngineUnavailable, ToolFailure
from eng_toolkit.licenses import install_hint

SCAN_EXTENSIONS: tuple[str, ...] = (".ply", ".pcd", ".xyz", ".xyzn", ".xyzrgb", ".pts")
MODEL_EXTENSIONS: tuple[str, ...] = (".step", ".stp")
REGISTRATIONS: tuple[str, ...] = ("none", "icp")

MIN_POINTS_LIMIT = 1_000
MAX_POINTS_LIMIT = 5_000_000
INSTALL_COMMAND = install_hint("open3d")  # the scan extra installs both engines

SEED = 0
HISTOGRAM_BINS = 20
WORST_POINT_COUNT = 10
MAX_REGIONS = 20
MAX_CSV_ROWS = 100_000
DBSCAN_EPS_FACTOR = 5.0  # 3 splits a uniformly sampled patch into 6 to 41 regions, 5 into one
DBSCAN_MIN_POINTS = 3
DBSCAN_EPS_FLOOR_MM = 1e-6
ICP_TARGET_POINTS = 100_000
ICP_MAX_ITERATIONS = 100
LOW_FITNESS = 0.5
MERGE_EPS_MM = 1e-6
MAX_COORDINATE_MM = 1e7
ICP_MAX_SOURCE_POINTS = 200_000
WEAK_EIGENVALUE_RATIO = 1e-3
OCCUPANCY_SAMPLES = 3
PAIR_CHUNK_QUERIES = 8_192
HISTOGRAM_MIN_TOP_MM = 1e-7
ICP_NOTE = (
    "ICP results can differ in the 1e-5 mm range between identical runs (multithreaded float summation)"
)


# --------------------------------------------------------------------------- engines


def _scan_engine_unavailable(module: str, dist: str, cause: BaseException) -> EngineUnavailable:
    return EngineUnavailable(
        module, dist, cause, install=INSTALL_COMMAND, needed_by="metrology_check_scan needs the optional scan engines"
    )


def _open3d():
    try:
        import open3d as o3d
    except (ImportError, OSError) as exc:
        raise _scan_engine_unavailable("open3d", "open3d", exc) from exc
    # Open3D logs warnings to stdout, which is the MCP stdio channel.
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    return o3d


def _require_ocp() -> None:
    try:
        brep.require_engine()
    except EngineUnavailable as exc:
        raise _scan_engine_unavailable(exc.details["module"], exc.details["dist"], exc.__cause__ or exc) from exc


def require_engines() -> None:
    _open3d()
    _require_ocp()


# --------------------------------------------------------------------------- inputs


_PLY_SCALAR_BYTES = {
    "char": 1, "int8": 1, "uchar": 1, "uint8": 1,
    "short": 2, "int16": 2, "ushort": 2, "uint16": 2,
    "int": 4, "int32": 4, "uint": 4, "uint32": 4, "float": 4, "float32": 4,
    "double": 8, "float64": 8,
}  # fmt: skip
_PLY_HEADER_LIMIT = 1 << 20


def _ply_layout(path: Path) -> tuple[str, int, list[tuple[int, int | None]]] | None:
    """``(format, body offset, [(count, bytes per row or None)])`` of a PLY header, or None if it cannot be parsed."""
    with path.open("rb") as fh:
        head = fh.read(_PLY_HEADER_LIMIT)
    end = head.find(b"end_header")
    newline = head.find(b"\n", end) if end >= 0 else -1
    if not head.startswith(b"ply") or newline < 0:
        return None
    fmt = ""
    elements: list[tuple[int, int | None]] = []
    for line in head[:newline].decode("ascii", "replace").splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "format" and len(words) >= 2:
            fmt = words[1]
        elif words[0] == "element" and len(words) == 3 and words[2].isdigit():
            elements.append((int(words[2]), 0))
        elif words[0] == "property" and elements:
            count, row = elements[-1]
            size = _PLY_SCALAR_BYTES.get(words[1]) if len(words) == 3 else None
            elements[-1] = (count, row + size if row is not None and size is not None else None)
    return fmt, newline + 1, elements


def _check_ply_complete(path: Path) -> None:
    """Reject a PLY that holds fewer rows than its header declares.

    Open3D returns the declared number of vertices and leaves the rows missing from a truncated file
    uninitialised, reporting it only as a warning.
    """
    layout = _ply_layout(path)
    if layout is None:
        return
    fmt, body_offset, elements = layout
    size = path.stat().st_size - body_offset
    declared = sum(count for count, _ in elements)
    if fmt == "ascii":
        with path.open("rb") as fh:
            fh.seek(body_offset)
            rows = sum(1 for line in fh if line.strip())
        short = rows < declared
        found = f"{rows} data lines"
        needed = f"{declared} rows"
    elif fmt in ("binary_little_endian", "binary_big_endian"):
        needed_bytes = 0
        for count, row in elements:
            if row is None:  # a list property makes the size of this and later elements unknown
                break
            needed_bytes += count * row
        short = size < needed_bytes
        found = f"{size} data bytes"
        needed = f"at least {needed_bytes} bytes"
    else:
        return
    if short:
        raise ToolFailure(
            "input.invalid",
            f"scan_path: {str(path)!r} is truncated or corrupt: its PLY header declares {needed} but the file has "
            f"{found}",
            {"path": str(path), "declared": needed, "found": found},
        )


@contextlib.contextmanager
def _ascii_alias(path: Path) -> Iterator[Path]:
    """A path to the same file that Open3D can open.

    Open3D opens files through the ANSI code page on Windows, so a name with characters outside that code page cannot be
    read: a scan named in Chinese reads on a zh-TW machine and fails on an en-US one.  For a non-ASCII path this gives the
    file an ASCII name for the duration of the read (a hard link where the volume allows it, otherwise a copy), next to the
    file or in the temporary directory, whichever has an ASCII path.  With no such place the original path is tried as is.
    """
    if str(path).isascii():
        yield path
        return
    folder = next((f for f in (path.parent, Path(tempfile.gettempdir())) if str(f).isascii()), None)
    if folder is None:
        yield path
        return
    alias = folder / f"eng-toolkit-scan-{uuid.uuid4().hex}{path.suffix.lower()}"
    try:
        try:
            os.link(path, alias)
        except OSError:
            shutil.copyfile(path, alias)
        yield alias
    finally:
        with contextlib.suppress(OSError):
            alias.unlink()


def read_scan_points(path: Path, to_mm: float) -> np.ndarray:
    o3d = _open3d()
    if path.suffix.lower() == ".ply":
        _check_ply_complete(path)
    try:
        with _ascii_alias(path) as readable:
            cloud = o3d.io.read_point_cloud(str(readable))
    except Exception as exc:  # noqa: BLE001 - Open3D raises RuntimeError for unreadable files
        raise ToolFailure("input.invalid", f"scan_path: cannot read {str(path)!r}: {exc}", {"path": str(path)}) from exc
    points = np.asarray(cloud.points, dtype=np.float64)
    if len(points) == 0:
        raise ToolFailure("input.invalid", f"scan_path: {str(path)!r} holds no readable points", {"path": str(path)})
    with np.errstate(over="ignore", invalid="ignore"):
        scaled = points * to_mm
        finite = bool(np.isfinite(scaled).all())
        largest = float(np.abs(scaled).max()) if finite else math.inf
    if not finite:
        raise ToolFailure("input.invalid", f"scan_path: {str(path)!r} has non-finite coordinates", {"path": str(path)})
    if largest > MAX_COORDINATE_MM:
        raise ToolFailure(
            "input.invalid",
            f"scan_path: {str(path)!r} has a coordinate of {largest:.6g} mm after unit conversion, beyond the "
            f"{int(MAX_COORDINATE_MM)} mm a scan can plausibly hold; the file is corrupt or scan_unit is wrong",
            {"path": str(path), "largest_coordinate_mm": largest, "limit_mm": MAX_COORDINATE_MM},
        )
    return scaled


def downsample(points: np.ndarray, max_points: int) -> np.ndarray:
    if len(points) <= max_points:
        return points
    keep = np.sort(np.random.default_rng(SEED).choice(len(points), size=max_points, replace=False))
    return points[keep]


# --------------------------------------------------------------------------- model mesh


@dataclass(frozen=True)
class ModelMesh:
    vertices: np.ndarray  # (n, 3) float64, millimetres
    triangles: np.ndarray  # (m, 3) int32
    watertight: bool
    deflection_mm: float
    solids: int = 1  # solids found in the transferred shape
    roots_in_file: int = 1  # root entities the STEP file offers for transfer
    roots_transferred: int = 1

    @property
    def signed(self) -> bool:
        """Inside and outside are defined only for one closed body."""
        return self.watertight and self.solids <= 1


def read_model_mesh(path: Path, deflection_mm: float) -> ModelMesh:
    """Read a STEP file (in millimetres) and tessellate every face with the given linear deflection."""
    _require_ocp()

    def unreadable(why: str) -> ToolFailure:
        return ToolFailure("input.invalid", f"model_path: {why} in {str(path)!r}", {"path": str(path)})

    solid = brep.read_solid(path, param="model_path")
    raw = brep.triangulate(solid, deflection_mm)
    if len(raw.triangles) == 0:
        raise unreadable("no faces to measure against")

    # Each face carries its own nodes; welding the shared edge nodes is what makes a closed shell closed.
    mesh = brep.weld(raw)
    if len(mesh.triangles) == 0:
        raise unreadable("no faces to measure against")
    return ModelMesh(
        vertices=mesh.vertices,
        triangles=mesh.triangles,
        watertight=brep.mesh_topology(mesh).watertight,
        deflection_mm=deflection_mm,
        solids=solid.solids,
        roots_in_file=solid.roots_in_file,
        roots_transferred=solid.roots_transferred,
    )


def _sample_surface(mesh: ModelMesh, count: int) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted points on the mesh with their triangle normals (fixed seed)."""
    corners = mesh.vertices[mesh.triangles]
    edge1 = corners[:, 1] - corners[:, 0]
    edge2 = corners[:, 2] - corners[:, 0]
    cross = np.cross(edge1, edge2)
    twice_area = np.linalg.norm(cross, axis=1)
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(corners), size=count, p=twice_area / twice_area.sum())
    uv = rng.random((count, 2))
    fold = uv.sum(axis=1) > 1.0
    uv[fold] = 1.0 - uv[fold]
    points = corners[pick, 0] + uv[:, :1] * edge1[pick] + uv[:, 1:] * edge2[pick]
    return points, cross[pick] / twice_area[pick, None]


# --------------------------------------------------------------------------- registration


@dataclass(frozen=True)
class Registration:
    transform: np.ndarray  # 4x4, scan frame to model frame
    fitness: float  # share of the evaluated points within the ICP search distance of the model surface
    inlier_rmse: float  # RMS distance to the model surface of those points, mm
    source_points: int  # scan points the transform was estimated from
    unconstrained: tuple[str, ...]  # directions in which the registered points leave the fit free


def transform_points(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    return points @ transform[:3, :3].T + transform[:3, 3]


def register_icp(points_mm: np.ndarray, mesh: ModelMesh, max_distance_mm: float) -> np.ndarray:
    """Point-to-plane ICP from the identity: scan to normals-bearing points sampled from the mesh."""
    o3d = _open3d()
    target_points, target_normals = _sample_surface(mesh, ICP_TARGET_POINTS)
    target = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(target_points))
    target.normals = o3d.utility.Vector3dVector(target_normals)
    source = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points_mm))
    reg = o3d.pipelines.registration
    result = reg.registration_icp(
        source,
        target,
        max_distance_mm,
        np.eye(4),
        reg.TransformationEstimationPointToPlane(),
        reg.ICPConvergenceCriteria(relative_fitness=1e-9, relative_rmse=1e-9, max_iteration=ICP_MAX_ITERATIONS),
    )
    return np.asarray(result.transformation, dtype=np.float64)


def _format_axis(axis: np.ndarray) -> str:
    axis = axis / np.linalg.norm(axis)
    if axis[np.argmax(np.abs(axis))] < 0:
        axis = -axis
    return "(" + ", ".join(f"{v + 0.0:.2f}" for v in np.round(axis, 2)) + ")"


def unconstrained_directions(points: np.ndarray, normals: np.ndarray) -> list[str]:
    """Rigid motions that points on surfaces with these unit normals cannot pin down.

    The 6 x 6 normal matrix of point-to-plane ICP has one row ``[r x n, n]`` per point: ``r`` is the
    point relative to the centroid in units of the RMS radius, so a rotation and a translation are
    both lengths.  An eigenvalue below ``WEAK_EIGENVALUE_RATIO`` of the largest is a motion that
    leaves every residual unchanged: a single plane cannot fix in-plane translation or the rotation
    about its normal.
    """
    if len(points) == 0:
        return []
    centred = points - points.mean(axis=0)
    radius = math.sqrt(float(np.mean(np.sum(centred**2, axis=1)))) or 1.0
    rows = np.hstack([np.cross(centred / radius, normals), normals])
    values, vectors = np.linalg.eigh(rows.T @ rows)
    if values[-1] <= 0.0:
        return []
    weak = vectors[:, values < WEAK_EIGENVALUE_RATIO * values[-1]]
    if weak.shape[1] == 0:
        return []
    projector = weak @ weak.T
    found: list[str] = []
    described = 0
    for rotation, block in ((True, projector[:3, :3]), (False, projector[3:, 3:])):
        share, axes = np.linalg.eigh(block)
        free = share > 0.5
        count = int(free.sum())
        if count == 0:
            continue
        described += count
        if count == 3:
            found.append("rotation about every axis" if rotation else "translation in every direction")
        elif count == 1:
            found.append(f"{'rotation about' if rotation else 'translation along'} {_format_axis(axes[:, free][:, 0])}")
        else:
            lead = "rotation about any axis" if rotation else "translation in any direction"
            found.append(f"{lead} perpendicular to {_format_axis(axes[:, ~free][:, 0])}")
    if weak.shape[1] > described:
        found.append(f"{weak.shape[1] - described} combined rotation and translation direction(s)")
    return found


# --------------------------------------------------------------------------- measurement


def closest_points_on_triangles(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Float64 closest point on triangle ``(a[i], b[i], c[i])`` to ``p[i]`` (Ericson, Real-Time Collision Detection 5.1.5)."""
    ab, ac = b - a, c - a
    ap, bp, cp = p - a, p - b, p - c
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    d3, d4 = np.einsum("ij,ij->i", ab, bp), np.einsum("ij,ij->i", ac, bp)
    d5, d6 = np.einsum("ij,ij->i", ab, cp), np.einsum("ij,ij->i", ac, cp)
    vc, vb, va = d1 * d4 - d3 * d2, d5 * d2 - d1 * d6, d3 * d6 - d5 * d4
    with np.errstate(divide="ignore", invalid="ignore"):
        # Projection onto the plane: the barycentric form loses about 1e-9 mm on a 3 m triangle.
        cross = np.cross(ab, ac)
        unit = cross / np.linalg.norm(cross, axis=1)[:, None]
        closest = p - np.einsum("ij,ij->i", ap, unit)[:, None] * unit
        on_ab = a + ab * (d1 / (d1 - d3))[:, None]
        on_ac = a + ac * (d2 / (d2 - d6))[:, None]
        on_bc = b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))[:, None]
    # The reference algorithm tests the regions in the order a, b, ab, c, ac, bc, interior and takes the
    # first hit, so they are applied here in the reverse order.
    for region, value in (
        ((va <= 0) & (d4 >= d3) & (d5 >= d6), on_bc),
        ((vb <= 0) & (d2 >= 0) & (d6 <= 0), on_ac),
        ((vc <= 0) & (d1 >= 0) & (d3 <= 0), on_ab),
        ((d6 >= 0) & (d5 <= d6), c),
        ((d3 >= 0) & (d4 <= d3), b),
        ((d1 <= 0) & (d2 <= 0), a),
    ):
        closest = np.where(region[:, None], value, closest)
    flat = ~np.isfinite(closest).all(axis=1)  # a zero-area triangle has no interior to project onto
    if flat.any():
        corners = np.stack([a[flat], b[flat], c[flat]], axis=1)
        nearest = np.linalg.norm(corners - p[flat][:, None, :], axis=2).argmin(axis=1)
        closest[flat] = corners[np.arange(len(nearest)), nearest]
    return closest


def _neighbour_table(triangles: np.ndarray, vertex_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every triangle, the other triangles that share a vertex with it, as ``(neighbour, start, count)``."""
    total = len(triangles)
    corner_vertex = triangles.ravel()
    fan = np.argsort(corner_vertex, kind="stable") // 3  # triangle of each corner, grouped by vertex
    degree = np.bincount(corner_vertex, minlength=vertex_count)
    first = np.cumsum(degree) - degree
    listed = degree[corner_vertex]
    slot = np.repeat(first[corner_vertex] - (np.cumsum(listed) - listed), listed) + np.arange(listed.sum())
    pair = np.unique(np.repeat(np.repeat(np.arange(total), 3), listed) * total + fan[slot])
    owner, neighbour = pair // total, pair % total
    apart = owner != neighbour
    count = np.bincount(owner[apart], minlength=total)
    return neighbour[apart], np.cumsum(count) - count, count


def _exact_distance(points: np.ndarray, vertices: np.ndarray, triangles: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """Float64 distance from each point to the nearest of triangle ``seed[i]`` and every triangle sharing a vertex with it.

    A neighbour is only evaluated when its bounding sphere and its plane both come closer than what is
    already found.
    """
    corners = vertices[triangles]
    centre = corners.mean(axis=1)
    radius = np.linalg.norm(corners - centre[:, None, :], axis=2).max(axis=1)
    normal = triangle_normals(vertices, triangles)
    neighbour, start, count = _neighbour_table(triangles, len(vertices))
    best = np.empty(len(points))
    for lo in range(0, len(points), PAIR_CHUNK_QUERIES):
        hi = min(lo + PAIR_CHUNK_QUERIES, len(points))
        here, own = points[lo:hi], seed[lo:hi]
        tri = corners[own]
        found = np.linalg.norm(here - closest_points_on_triangles(here, tri[:, 0], tri[:, 1], tri[:, 2]), axis=1)
        listed = count[own]
        owner = np.repeat(np.arange(hi - lo), listed)
        candidate = neighbour[np.repeat(start[own] - (np.cumsum(listed) - listed), listed) + np.arange(listed.sum())]
        at = here[owner]
        reach = found[owner]
        near = np.linalg.norm(at - centre[candidate], axis=1) - radius[candidate] < reach
        near &= np.abs(np.einsum("ij,ij->i", at - corners[candidate, 0], normal[candidate])) < reach
        owner, candidate, at = owner[near], candidate[near], at[near]
        if len(owner):
            tri = corners[candidate]
            distance = np.linalg.norm(at - closest_points_on_triangles(at, tri[:, 0], tri[:, 1], tri[:, 2]), axis=1)
            heads = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
            found[owner[heads]] = np.minimum(found[owner[heads]], np.minimum.reduceat(distance, heads))
        best[lo:hi] = found
    return best


@dataclass(frozen=True)
class Measurement:
    distance: np.ndarray  # mm, float64; negative inside the material when signed
    triangle: np.ndarray  # per point, the model triangle Open3D found closest


def measure(points_mm: np.ndarray, mesh: ModelMesh, *, signed: bool) -> Measurement:
    """Distance to the mesh.

    Open3D works in float32, which loses millimetres to coordinate magnitude and picks the wrong
    triangle on long thin ones.  So model and points are moved to the mesh centre before the cast,
    Open3D only names the closest triangle, and the distance is recomputed in float64 against that
    triangle and every triangle sharing a vertex with it.  The sign is a three-ray vote.
    """
    o3d = _open3d()
    centre = (mesh.vertices.min(axis=0) + mesh.vertices.max(axis=0)) / 2
    vertices = mesh.vertices - centre
    points = points_mm - centre
    tensor_mesh = o3d.t.geometry.TriangleMesh()
    tensor_mesh.vertex.positions = o3d.core.Tensor(vertices.astype(np.float32))
    tensor_mesh.triangle.indices = o3d.core.Tensor(mesh.triangles.astype(np.int32))
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(tensor_mesh)
    queries = o3d.core.Tensor(points.astype(np.float32))
    triangle = scene.compute_closest_points(queries)["primitive_ids"].numpy().astype(np.int64)
    distance = _exact_distance(points, vertices, mesh.triangles, triangle)
    if signed:
        inside = scene.compute_occupancy(queries, nsamples=OCCUPANCY_SAMPLES).numpy() > 0.5
        distance = np.where(inside, -distance, distance)
    return Measurement(distance=distance, triangle=triangle)


def triangle_normals(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    corners = vertices[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return cross / np.linalg.norm(cross, axis=1)[:, None]


@dataclass(frozen=True)
class ScanCheck:
    points: np.ndarray  # evaluated points in the model frame, mm
    deviation: np.ndarray  # mm, signed when ``signed``
    signed: bool
    mesh: ModelMesh
    original_count: int
    registration: Registration | None


def check(
    scan_file: Path,
    model_file: Path,
    *,
    to_mm: float,
    registration: str,
    icp_max_distance_mm: float,
    deflection_mm: float,
    max_points: int,
) -> ScanCheck:
    require_engines()
    all_points = read_scan_points(scan_file, to_mm)
    mesh = read_model_mesh(model_file, deflection_mm)
    points = downsample(all_points, max_points)
    transform = None
    source_points = 0
    if registration == "icp":
        source = downsample(points, ICP_MAX_SOURCE_POINTS)
        source_points = len(source)
        transform = register_icp(source, mesh, icp_max_distance_mm)
        points = transform_points(points, transform)
    measured = measure(points, mesh, signed=mesh.signed)
    if not np.isfinite(measured.distance).all():
        raise ToolFailure(
            "input.invalid",
            f"scan_path: {str(scan_file)!r} produced a non-finite distance to the model",
            {"path": str(scan_file)},
        )
    deviation = measured.distance
    reg = None
    if transform is not None:
        # Open3D's own fitness and RMSE are measured against sampled model points, which caps them by the
        # sampling spacing; the distance to the surface is what the caller needs to judge the fit.
        near = np.abs(deviation) <= icp_max_distance_mm
        rmse = float(np.sqrt(np.mean(deviation[near] ** 2))) if near.any() else 0.0
        weak = unconstrained_directions(points[near], triangle_normals(mesh.vertices, mesh.triangles)[measured.triangle[near]])
        reg = Registration(
            transform=transform,
            fitness=float(near.mean()),
            inlier_rmse=rmse,
            source_points=source_points,
            unconstrained=tuple(weak),
        )
    return ScanCheck(
        points=points,
        deviation=deviation,
        signed=mesh.signed,
        mesh=mesh,
        original_count=len(all_points),
        registration=reg,
    )


# --------------------------------------------------------------------------- statistics


def deviation_stats(deviation: np.ndarray) -> dict[str, float]:
    """Population statistics (ddof 0) of a deviation array, in the array's unit."""
    absolute = np.abs(deviation)
    return {
        "rms": float(np.sqrt(np.mean(deviation**2))),
        "mean": float(np.mean(deviation)),
        "std": float(np.std(deviation)),
        "max_abs": float(absolute.max()),
        "max": float(deviation.max()),
        "min": float(deviation.min()),
        "p95_abs": float(np.percentile(absolute, 95)),
    }


def histogram(deviation: np.ndarray) -> dict[str, Any]:
    absolute = np.abs(deviation)
    top = max(float(absolute.max()), HISTOGRAM_MIN_TOP_MM)  # keeps the edges apart after rounding
    counts, edges = np.histogram(absolute, bins=HISTOGRAM_BINS, range=(0.0, top))
    return {"unit": "mm", "edges": [round(float(e), 9) for e in edges], "counts": [int(c) for c in counts]}


def worst_points(points: np.ndarray, deviation: np.ndarray) -> list[dict[str, float]]:
    order = np.argsort(-np.abs(deviation), kind="stable")[:WORST_POINT_COUNT]
    return [
        {
            "x_mm": round(float(points[i, 0]), 6),
            "y_mm": round(float(points[i, 1]), 6),
            "z_mm": round(float(points[i, 2]), 6),
            "deviation_mm": round(float(deviation[i]), 6),
        }
        for i in order
    ]


@dataclass(frozen=True)
class Regions:
    labels: np.ndarray  # per input point: 1-based region id by descending size, -1 = in no region
    listed: list[dict[str, Any]]  # the largest MAX_REGIONS
    total: int


def _distinct_positions(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Indices of the first point at each position (rounded to ``MERGE_EPS_MM``) and, per point, its position's index."""
    keys = np.round(points / MERGE_EPS_MM).astype(np.int64)
    order = np.lexsort(keys.T[::-1])  # np.unique(axis=0) is several times slower on millions of rows
    ordered = keys[order]
    starts = np.r_[True, (ordered[1:] != ordered[:-1]).any(axis=1)]
    inverse = np.empty(len(points), dtype=np.int64)
    inverse[order] = np.cumsum(starts) - 1
    return order[starts], inverse


def scan_spacing(points: np.ndarray) -> float:
    """Median nearest-neighbour distance of the distinct positions of a scan, mm."""
    first, _ = _distinct_positions(points)
    if len(first) < 2:
        return 0.0
    o3d = _open3d()
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points[first]))
    return float(np.median(np.asarray(cloud.compute_nearest_neighbor_distance())))


def cluster_regions(points: np.ndarray, deviation: np.ndarray, spacing_mm: float) -> Regions:
    """DBSCAN over the distinct positions of the out-of-tolerance points, eps = ``DBSCAN_EPS_FACTOR`` x the scan's point spacing.

    A position recorded several times is one position, so repeated points neither shrink the spacing
    nor inflate a region's count.
    """
    labels = np.full(len(points), -1, dtype=np.int64)
    if len(points) < DBSCAN_MIN_POINTS:
        return Regions(labels, [], 0)
    o3d = _open3d()
    first, inverse = _distinct_positions(points)
    distinct = points[first]
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(distinct))
    eps = max(DBSCAN_EPS_FACTOR * spacing_mm, DBSCAN_EPS_FLOOR_MM)
    raw_distinct = np.asarray(cloud.cluster_dbscan(eps=eps, min_points=DBSCAN_MIN_POINTS, print_progress=False))
    raw = raw_distinct[inverse]
    found, counts = np.unique(raw_distinct[raw_distinct >= 0], return_counts=True)
    listed: list[dict[str, Any]] = []
    for rank, k in enumerate(np.argsort(-counts, kind="stable"), start=1):
        labels[raw == found[k]] = rank
        if rank <= MAX_REGIONS:
            cluster = distinct[raw_distinct == found[k]]
            listed.append(
                {
                    "id": rank,
                    "count": int(len(cluster)),
                    "centroid_mm": [round(float(v), 6) for v in cluster.mean(axis=0)],
                    "bbox_min_mm": [round(float(v), 6) for v in cluster.min(axis=0)],
                    "bbox_max_mm": [round(float(v), 6) for v in cluster.max(axis=0)],
                    "max_abs_deviation_mm": round(float(np.abs(deviation[raw == found[k]]).max()), 6),
                }
            )
    return Regions(labels, listed, len(found))


__all__ = [
    "MAX_CSV_ROWS",
    "MAX_POINTS_LIMIT",
    "MIN_POINTS_LIMIT",
    "MODEL_EXTENSIONS",
    "REGISTRATIONS",
    "SCAN_EXTENSIONS",
    "LOW_FITNESS",
    "ModelMesh",
    "Registration",
    "Regions",
    "ScanCheck",
    "check",
    "closest_points_on_triangles",
    "cluster_regions",
    "deviation_stats",
    "histogram",
    "measure",
    "read_model_mesh",
    "scan_spacing",
    "unconstrained_directions",
    "worst_points",
]
