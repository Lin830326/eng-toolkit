"""Tetrahedral meshing with Gmsh, and everything computed from the mesh afterwards (pure NumPy).

The caller never supplies a Gmsh script.  The solid read from the caller's file is written by :mod:`eng_toolkit.brep` as a BREP
file with a fixed name in the job directory, and Gmsh is started with a fixed argument list: ``part.brep -3 -order 2 -clmax <size>
-format msh2 -o part.msh``.  The only generated text is the size (a validated finite float) and the node/element files it writes
are read back by a strict parser, so no text from the caller reaches Gmsh at all.

Elements are 10-node tetrahedra (second order).  Gmsh numbers the midside nodes 5..10 as edges 12, 23, 31, 14, 34, 24 and
CalculiX (C3D10) as 12, 23, 31, 14, 24, 34: the last two are swapped on reading (:func:`read_msh`), and the mesh is refused if
a tetrahedron then has a non-positive Jacobian anywhere we look.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from eng_toolkit import brep
from eng_toolkit import external as ext
from eng_toolkit.errors import ToolFailure
from eng_toolkit.external_catalog import GMSH

TET_PER_UNIT_VOLUME = 6.0 * math.sqrt(2.0)  # elements of edge h per h^3 of volume: a regular tetrahedron has V = h^3 / (6 sqrt 2)
MAX_ELEMENTS = 60_000  # tetrahedra; the estimate before meshing and the count after it are both held to this
MAX_NODES = 120_000  # 10-node elements share corner and mid-edge nodes: about 1.4 nodes per element at this size
MIN_SIZE_FRACTION = 1.0 / 1000.0  # element size is at least this fraction of the bounding-box diagonal
MIN_PART_MM = 1e-3
MAX_PART_MM = 1e6
GMSH_TIMEOUT_S = 120.0
GMSH_MEMORY_MB = 4096
SLIVER_QUALITY = 0.05  # mean ratio below which an element is called a sliver
WEAK_QUALITY = 0.2  # below this the mean-ratio minimum is reported as weak
# msh2 point, line, triangle and quadrilateral types of order one or two.
# Everything else must be refused, never silently omitted from the volume model.
BOUNDARY_ELEMENT_TYPES = frozenset((1, 2, 3, 8, 9, 10, 15, 16))

# C3D10 midside nodes as the (corner, corner) pair, nodes 5..10 (0-based corner indices)
_MID_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
# the six edges of a tetrahedron, for the mean ratio
_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
# facet k is the face opposite corner k
_FACES = ((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2))
_MID_OF = {frozenset(pair): 4 + i for i, pair in enumerate(_MID_EDGES)}


@dataclass(frozen=True)
class TetMesh:
    nodes: np.ndarray  # (n, 3) float64, millimetres
    tets: np.ndarray  # (m, 10) int64, 0-based, CalculiX C3D10 order
    size_mm: float

    @property
    def diagonal_mm(self) -> float:
        return float(np.linalg.norm(self.nodes.max(axis=0) - self.nodes.min(axis=0)))


@dataclass(frozen=True)
class Facets:
    """The boundary of the mesh: triangles with six nodes, normals pointing out of the body."""

    corners: np.ndarray  # (f, 3) node indices
    mids: np.ndarray  # (f, 3) node indices of edges ab, bc, ca
    normals: np.ndarray  # (f, 3) outward unit normals of the corner triangle
    areas: np.ndarray  # (f,) mm^2, of the curved six-node triangle
    owner: np.ndarray  # (f,) the tetrahedron the facet belongs to


# --------------------------------------------------------------------------- limits before meshing


def estimate_elements(volume_mm3: float, size_mm: float) -> float:
    """Tetrahedra of edge ``size_mm`` that fill ``volume_mm3``: ``6 sqrt(2) V / h^3``.  An estimate: a part with thin walls needs more."""
    return TET_PER_UNIT_VOLUME * volume_mm3 / size_mm**3


def check_before_meshing(volume_mm3: float, bbox_min: tuple[float, float, float], bbox_max: tuple[float, float, float], size_mm: float) -> float:
    """Refuse a model or an element size that cannot be meshed within the limits; return the estimated element count."""
    extent = [hi - lo for lo, hi in zip(bbox_min, bbox_max)]
    diagonal = math.hypot(*extent)
    if not (MIN_PART_MM <= max(extent) and diagonal <= MAX_PART_MM):
        raise ToolFailure(
            "input.out_of_range",
            f"the part is {max(extent):.6g} mm at its longest (diagonal {diagonal:.6g} mm); this tool takes parts between {MIN_PART_MM:g} mm and {MAX_PART_MM:g} mm",
            {"param": "/model_path", "longest_mm": max(extent), "diagonal_mm": diagonal},
        )
    smallest = diagonal * MIN_SIZE_FRACTION
    if size_mm < smallest:
        raise ToolFailure(
            "input.too_large",
            f"element size {size_mm:.6g} mm is below 1/{round(1 / MIN_SIZE_FRACTION)} of the part's diagonal ({diagonal:.6g} mm, so at least {smallest:.4g} mm)",
            {"param": "/element_size", "size_mm": size_mm, "smallest_mm": smallest, "diagonal_mm": diagonal},
        )
    if not math.isfinite(size_mm) or size_mm > diagonal:
        raise ToolFailure(
            "input.out_of_range",
            f"element size {size_mm:.6g} mm is larger than the part's diagonal ({diagonal:.6g} mm)",
            {"param": "/element_size", "diagonal_mm": diagonal},
        )
    estimate = estimate_elements(volume_mm3, size_mm)
    if estimate > MAX_ELEMENTS:
        raise ToolFailure(
            "input.too_large",
            f"element size {size_mm:.6g} mm on a part of {volume_mm3:.6g} mm3 would need about {estimate:.3g} elements; the limit is {MAX_ELEMENTS} "
            f"(use a size of at least {(TET_PER_UNIT_VOLUME * volume_mm3 / MAX_ELEMENTS) ** (1 / 3):.4g} mm)",
            {"param": "/element_size", "estimated_elements": float(f"{estimate:.6g}"), "max_elements": MAX_ELEMENTS, "size_mm": size_mm, "volume_mm3": volume_mm3},
        )
    return estimate


# --------------------------------------------------------------------------- Gmsh


def run_gmsh(solid: "brep.Solid", size_mm: float, workdir: Path) -> tuple[Path, ext.Record]:
    """Write the solid as ``part.brep`` in ``workdir`` and mesh it; return the ``.msh`` path and the run record."""
    brep.write_brep(solid, workdir / "part.brep")
    out = workdir / "part.msh"
    args = [
        workdir / "part.brep",
        "-3",
        "-order",
        "2",
        "-clmax",
        repr(float(size_mm)),
        "-format",
        "msh2",
        "-nopopup",
        "-o",
        out,
    ]
    record = ext.run(GMSH, args, workdir=workdir, timeout_s=GMSH_TIMEOUT_S, memory_mb=GMSH_MEMORY_MB, check=False)
    if record.returncode != 0 or not out.is_file():
        raise ToolFailure(
            "engine.failure",
            f"Gmsh exited with code {record.returncode} and wrote no mesh: {record.stderr_tail[-300:] or record.stdout_tail[-300:]}",
            {"returncode": record.returncode, "stdout_tail": record.stdout_tail, "stderr_tail": record.stderr_tail},
        )
    return out, record


def read_msh(path: Path, size_mm: float) -> TetMesh:
    """Strict reader for the ASCII ``msh2`` file Gmsh wrote: its nodes and its 10-node tetrahedra (type 11); the surface and line
    elements are ignored.  Raises ``engine.failure`` for anything else (no tetrahedra, first-order tetrahedra, a node that does not exist)."""
    text = path.read_text(encoding="ascii", errors="replace").splitlines()
    try:
        i = text.index("$Nodes")
        count = int(text[i + 1])
        node_rows = np.array([row.split() for row in text[i + 2 : i + 2 + count]], dtype=np.float64)
        j = text.index("$Elements")
        n_elem = int(text[j + 1])
        tets: list[list[int]] = []
        for row in text[j + 2 : j + 2 + n_elem]:
            parts = row.split()
            kind = int(parts[1])
            if kind == 11:
                tets.append([int(v) for v in parts[3 + int(parts[2]) :]])
            elif kind not in BOUNDARY_ELEMENT_TYPES:
                raise ToolFailure(
                    "engine.failure", f"Gmsh wrote unsupported element type {kind}; only type 11 volume elements and known boundary elements are supported",
                    {"param": "/element_size", "element_type": kind},
                )
    except ToolFailure:
        raise
    except (ValueError, IndexError) as exc:
        raise ToolFailure("engine.failure", f"the mesh file written by Gmsh could not be read ({exc})", {}) from exc
    if not tets:
        raise ToolFailure(
            "engine.failure",
            "Gmsh wrote no second-order tetrahedra: the model may not be a closed solid it can mesh",
            {"param": "/element_size"},
        )
    connectivity = np.array(tets, dtype=np.int64)
    if connectivity.ndim != 2 or connectivity.shape[1] != 10:
        raise ToolFailure("engine.failure", "the mesh file written by Gmsh holds elements that are not 10-node tetrahedra", {})
    ids = node_rows[:, 0].astype(np.int64)
    lookup = {int(v): k for k, v in enumerate(ids)}
    try:
        index = np.vectorize(lookup.__getitem__, otypes=[np.int64])(connectivity)
    except KeyError as exc:
        raise ToolFailure("engine.failure", f"the mesh file refers to a node that does not exist ({exc})", {}) from exc
    # renumber to the nodes the tetrahedra use, in order of first use of their id
    used = np.unique(index)
    remap = np.full(len(ids), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    index = remap[index]
    coordinates = node_rows[used, 1:4]
    index[:, [8, 9]] = index[:, [9, 8]]  # Gmsh edges 34, 24 -> CalculiX edges 24, 34
    if not np.all(np.isfinite(coordinates)):
        raise ToolFailure("engine.failure", "the mesh file holds a node with a non-finite coordinate", {})
    return TetMesh(nodes=coordinates, tets=index, size_mm=size_mm)


# --------------------------------------------------------------------------- tetrahedron shape functions


def _tet10_derivatives(points: np.ndarray) -> np.ndarray:
    """dN/d(xi, eta, zeta) of the 10 shape functions at barycentric points ``(p, 4)`` (L1..L4): array ``(p, 10, 3)``."""
    p = points.shape[0]
    dl = np.array([[-1.0, -1.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    out = np.zeros((p, 10, 3))
    for a in range(4):
        out[:, a, :] = (4.0 * points[:, a] - 1.0)[:, None] * dl[a][None, :]
    for k, (a, b) in enumerate(_MID_EDGES):
        out[:, 4 + k, :] = 4.0 * (points[:, b][:, None] * dl[a][None, :] + points[:, a][:, None] * dl[b][None, :])
    return out


# Keast's 5-point rule, exact for cubics: reference volume 1/6.
_KEAST = np.array(
    [[0.25, 0.25, 0.25, 0.25], [0.5, 1 / 6, 1 / 6, 1 / 6], [1 / 6, 0.5, 1 / 6, 1 / 6], [1 / 6, 1 / 6, 0.5, 1 / 6], [1 / 6, 1 / 6, 1 / 6, 0.5]]
)
_KEAST_W = np.array([-0.8, 0.45, 0.45, 0.45, 0.45]) / 6.0
# The four C3D10 solver integration points, plus the volume rule and the nodes.
_GAUSS_LOW = (5.0 - math.sqrt(5.0)) / 20.0
_GAUSS = np.full((4, 4), _GAUSS_LOW)
np.fill_diagonal(_GAUSS, 1.0 - 3.0 * _GAUSS_LOW)
_CHECK_POINTS = np.array(
    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    + [[0.5 if k in pair else 0.0 for k in range(4)] for pair in _MID_EDGES]
    + _KEAST.tolist() + _GAUSS.tolist(),
    dtype=float,
)


def _jacobians(mesh: TetMesh, points: np.ndarray) -> np.ndarray:
    """det J of every element at every point: ``(m, p)``."""
    dn = _tet10_derivatives(points)  # (p, 10, 3)
    coords = mesh.nodes[mesh.tets]  # (m, 10, 3)
    coords = coords - coords[:, :1, :]  # derivatives sum to zero; remove translation before summing
    jac = np.einsum("mna,pnb->mpab", coords, dn)  # (m, p, 3, 3): J[a, b] = sum_n x_n[a] dN_n/dxi_b
    return np.linalg.det(jac)


# --------------------------------------------------------------------------- quality, volume, topology


@dataclass(frozen=True)
class Quality:
    min_ratio: float
    mean_ratio: float
    median_ratio: float
    slivers: int  # elements with a mean ratio below SLIVER_QUALITY
    min_jacobian_ratio: float
    worst_element: int


def check_and_measure(mesh: TetMesh) -> tuple[Quality, float]:
    """Element quality and the volume of the mesh (exact for the quadratic geometry).

    Quality is the *mean ratio* of the four corner nodes: ``12 (3 V)^(2/3) / (sum of the six squared edge lengths)``, 1 for a regular
    tetrahedron and 0 for a flat one (Liu and Joe).  ``min_jacobian_ratio`` is the smallest det J over the element, at the corners, the
    midside points, centre and integration points, divided by the largest sampled determinant in that element: 1 for a
    straight-sided element. Sampling is not a proof of positivity everywhere. Raises ``engine.failure`` when any sample is not positive.
    """
    corners = mesh.nodes[mesh.tets[:, :4]]  # (m, 4, 3)
    edges = np.array([corners[:, b] - corners[:, a] for a, b in _EDGES])  # (6, m, 3)
    sum_sq = np.einsum("emk,emk->m", edges, edges)
    volume6 = np.einsum("mk,mk->m", edges[0], np.cross(edges[1], edges[2]))  # 6 V from edges 01, 02, 03
    if np.any(volume6 <= 0.0):
        bad = int(np.argmin(volume6))
        raise ToolFailure(
            "engine.failure",
            f"the mesh holds {int(np.sum(volume6 <= 0.0))} tetrahedra with a non-positive volume (first: element {bad + 1}); no solver result can be trusted",
            {"param": "/element_size", "elements": int(np.sum(volume6 <= 0.0))},
        )
    ratio = 12.0 * (volume6 / 2.0) ** (2.0 / 3.0) / sum_sq
    dets = _jacobians(mesh, _CHECK_POINTS)
    if not np.all(np.isfinite(dets)) or np.any(dets <= 0.0):
        raise ToolFailure(
            "engine.failure",
            "second-order elements are inverted or invalid (non-positive or non-finite Jacobian at a corner, midside point, centre or integration point); use a smaller element size",
            {"param": "/element_size"},
        )
    jac_ratio = float(np.min(dets.min(axis=1) / dets.max(axis=1)))
    mesh_volume = float(np.sum(_jacobians(mesh, _KEAST) @ _KEAST_W))
    worst = int(np.argmin(ratio))
    return (
        Quality(
            min_ratio=float(ratio.min()),
            mean_ratio=float(ratio.mean()),
            median_ratio=float(np.median(ratio)),
            slivers=int(np.sum(ratio < SLIVER_QUALITY)),
            min_jacobian_ratio=jac_ratio,
            worst_element=worst + 1,
        ),
        mesh_volume,
    )


def connected_components(mesh: TetMesh) -> int:
    """Number of pieces the mesh falls into (tetrahedra that share a corner node are in one piece)."""
    parent = np.arange(len(mesh.nodes))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = int(parent[a])
        return a

    for row in mesh.tets[:, :4]:
        root = find(int(row[0]))
        for other in row[1:]:
            r = find(int(other))
            if r != root:
                parent[r] = root
    return len({find(int(i)) for i in np.unique(mesh.tets[:, :4])})


_RULE7 = None


def _triangle_rule() -> tuple[np.ndarray, np.ndarray]:
    """Radon's 7-point rule on the reference triangle (exact for degree 5): barycentric points ``(7, 3)`` and weights summing to 1/2."""
    global _RULE7
    if _RULE7 is None:
        s15 = math.sqrt(15.0)
        a, b = (6.0 - s15) / 21.0, (6.0 + s15) / 21.0
        wa, wb = (155.0 - s15) / 1200.0, (155.0 + s15) / 1200.0
        pts = [[1 / 3, 1 / 3, 1 / 3]]
        wts = [9.0 / 40.0]
        for p, w in ((a, wa), (b, wb)):
            for k in range(3):
                point = [p, p, p]
                point[k] = 1.0 - 2.0 * p
                pts.append(point)
                wts.append(w)
        _RULE7 = (np.array(pts), np.array(wts) * 0.5)
    return _RULE7


def tri6_shape(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shape functions ``(q, 6)`` and their derivatives w.r.t. ``(xi, eta) = (L2, L3)`` ``(q, 6, 2)`` of the six-node triangle,
    nodes in the order corner a, b, c, midside ab, bc, ca."""
    l1, l2, l3 = points[:, 0], points[:, 1], points[:, 2]
    n = np.stack([l1 * (2 * l1 - 1), l2 * (2 * l2 - 1), l3 * (2 * l3 - 1), 4 * l1 * l2, 4 * l2 * l3, 4 * l3 * l1], axis=1)
    dl = np.array([[-1.0, -1.0], [1.0, 0.0], [0.0, 1.0]])
    d = np.zeros((points.shape[0], 6, 2))
    for a in range(3):
        d[:, a, :] = (4.0 * points[:, a] - 1.0)[:, None] * dl[a][None, :]
    for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
        d[:, 3 + k, :] = 4.0 * (points[:, b][:, None] * dl[a][None, :] + points[:, a][:, None] * dl[b][None, :])
    return n, d


def boundary_facets(mesh: TetMesh) -> Facets:
    """Faces that belong to exactly one tetrahedron, oriented outward, with their curved areas."""
    m = len(mesh.tets)
    corners = mesh.tets[:, :4]
    tri = np.stack([corners[:, list(face)] for face in _FACES], axis=1).reshape(-1, 3)  # (4m, 3)
    local = np.tile(np.array(_FACES), (m, 1))  # local corner indices of each face
    owner = np.repeat(np.arange(m), 4)
    opposite = np.tile(np.arange(4), m)
    n = np.int64(len(mesh.nodes))
    s = np.sort(tri, axis=1)
    key = (s[:, 0] * n + s[:, 1]) * n + s[:, 2]
    unique, first, counts = np.unique(key, return_index=True, return_counts=True)
    if np.any(counts > 2):
        raise ToolFailure("engine.failure", "the mesh has a face shared by more than two tetrahedra", {})
    pick = first[counts == 1]
    tri, local, owner, opposite = tri[pick], local[pick], owner[pick], opposite[pick]
    a, b, c = (mesh.nodes[tri[:, k]] for k in range(3))
    normal = np.cross(b - a, c - a)
    inward = mesh.nodes[corners[owner, opposite]] - a
    flip = np.einsum("fk,fk->f", normal, inward) > 0.0  # the normal points at the opposite corner: swap b and c
    tri[flip] = tri[flip][:, [0, 2, 1]]
    local[flip] = local[flip][:, [0, 2, 1]]
    normal = np.cross(mesh.nodes[tri[:, 1]] - mesh.nodes[tri[:, 0]], mesh.nodes[tri[:, 2]] - mesh.nodes[tri[:, 0]])
    length = np.linalg.norm(normal, axis=1)
    normal = normal / length[:, None]
    mids = np.empty_like(tri)
    for k, (p, q) in enumerate(((0, 1), (1, 2), (2, 0))):
        mid_index = np.array([_MID_OF[frozenset((int(i), int(j)))] for i, j in zip(local[:, p], local[:, q])])
        mids[:, k] = mesh.tets[owner, mid_index]
    facets = Facets(corners=tri, mids=mids, normals=normal, areas=np.zeros(len(tri)), owner=owner)
    return Facets(corners=tri, mids=mids, normals=normal, areas=facet_integrals(mesh, facets, np.arange(len(tri)))[2], owner=owner)


def facet_integrals(mesh: TetMesh, facets: Facets, chosen: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For the chosen facets: ``(scalar, vector, area)`` where ``scalar[f, i]`` is the integral of ``N_i dA`` over facet f, ``vector[f, i, :]`` the
    integral of ``N_i n dA`` (n outward) and ``area[f]`` the facet's area.

    Integrated on the curved six-node triangle with a degree-5 rule; ``n dA`` is the cross product of the two tangent vectors, outward.
    ``sum(N_i) = 1`` makes the nodal shares of each facet add up to its area (scalar) and to its area vector (vector) exactly.  For a
    corner-node and a midside node of a straight-sided triangle the shares are 0 and area / 3.
    """
    pts, wts = _triangle_rule()
    n, d = tri6_shape(pts)  # (q, 6), (q, 6, 2)
    nodes6 = np.concatenate([facets.corners[chosen], facets.mids[chosen]], axis=1)  # (f, 6)
    x = mesh.nodes[nodes6]  # (f, 6, 3)
    x = x - x[:, :1, :]
    tangent = np.einsum("fnk,qnj->fqkj", x, d)  # (f, q, 3, 2)
    area_vec = np.cross(tangent[..., 0], tangent[..., 1])  # (f, q, 3)
    magnitude = np.linalg.norm(area_vec, axis=2)  # (f, q)
    vector = np.einsum("q,qn,fqk->fnk", wts, n, area_vec)
    scalar = np.einsum("q,qn,fq->fn", wts, n, magnitude)
    return scalar, vector, scalar.sum(axis=1)


def nodes_of(facets: Facets, chosen: np.ndarray) -> np.ndarray:
    """Sorted unique node indices (corner and midside) of the chosen facets."""
    return np.unique(np.concatenate([facets.corners[chosen].ravel(), facets.mids[chosen].ravel()]))
