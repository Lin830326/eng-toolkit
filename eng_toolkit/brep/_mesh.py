"""Welding a face-by-face tessellation into a shared-node mesh, and testing it for closure.

Pure NumPy: no OCP and no Open3D, so geometry code can weld and classify a mesh with neither installed.
Everything is vectorised; a mesh of a million triangles takes seconds.
"""

from __future__ import annotations

import numpy as np

from eng_toolkit.brep._types import Mesh, MeshTopology

WELD_EPS_MM = 1e-6


def weld(mesh: Mesh, eps_mm: float = WELD_EPS_MM) -> Mesh:
    """Merge vertices that lie in the same ``eps_mm`` grid cell, then drop what the merge leaves useless.

    Each vertex is quantised to a grid of ``eps_mm`` and the first vertex of each cell (lowest index) represents it,
    keeping its original coordinates, so the result is deterministic and its vertices keep their original order.
    A triangle that names one vertex twice after the merge is dropped, then every vertex no triangle uses.
    Triangles of zero area with three distinct vertices are kept: removing one would open a hole.

    Two nodes closer than ``eps_mm`` can still fall on opposite sides of a cell boundary and stay apart.  When the
    first pass is not closed and oriented (every edge shared by one triangle in each direction), the weld is repeated
    once on a grid shifted by half a cell.  The second result replaces the first when it is closed, or else when it
    merged more vertices.  Both grids cut a pair apart only when it straddles a boundary of the first grid on one axis
    and a boundary of the shifted grid on another; for two copies of one node that differ by float noise (about
    1e-12 mm) the chance is about (noise / eps_mm) squared, near 1e-12 per pair.
    """
    if eps_mm <= 0.0:
        raise ValueError(f"weld: eps_mm must be positive, got {eps_mm}")
    vertices = np.asarray(mesh.vertices, dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
    if not np.isfinite(vertices).all():
        raise ValueError("weld: vertex coordinates must be finite")
    if len(vertices) == 0 or len(triangles) == 0:
        return Mesh(np.empty((0, 3)), np.empty((0, 3), dtype=np.int32), mesh.deflection_mm)

    best = _weld_on_grid(vertices, triangles, eps_mm, 0.0)
    if not _closed_oriented(best[1]):
        shifted = _weld_on_grid(vertices, triangles, eps_mm, 0.5)
        if _closed_oriented(shifted[1]) or len(shifted[0]) < len(best[0]):
            best = shifted
    return Mesh(best[0], best[1].astype(np.int32), mesh.deflection_mm)


def mesh_topology(mesh: Mesh) -> MeshTopology:
    """Closure and manifoldness of a mesh whose shared nodes are already welded.

    ``edge_manifold_closed``: every undirected edge is used by exactly two triangles, one in each direction.
    This is stricter than Open3D's ``is_edge_manifold(allow_boundary_edges=False)``, which counts triangles per edge
    and ignores their orientation, so a mesh with one flipped triangle passes there and fails here.
    ``vertex_manifold``: the triangles around every vertex form one single fan (open or closed), as in Open3D's
    ``is_vertex_manifold``, and no edge has more than two triangles.  Two bodies that touch at one vertex fail it.
    A mesh with no triangles, or a triangle that names one vertex twice, is neither.
    """
    triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
    if len(triangles) == 0 or _has_repeated_index(triangles):
        return MeshTopology(edge_manifold_closed=False, vertex_manifold=False)
    edges = _EdgePairs(triangles)
    if edges.shared_by_more_than_two:
        return MeshTopology(edge_manifold_closed=False, vertex_manifold=False)
    return MeshTopology(edge_manifold_closed=edges.closed_oriented, vertex_manifold=bool(_vertex_manifold(triangles, edges)))


# --------------------------------------------------------------------------- welding


def _weld_on_grid(
    vertices: np.ndarray, triangles: np.ndarray, eps_mm: float, offset_cells: float
) -> tuple[np.ndarray, np.ndarray]:
    cells = np.round(vertices / eps_mm + offset_cells).astype(np.int64)
    order = np.lexsort(cells.T[::-1])  # stable, so the first of a cell is its lowest original index
    ordered = cells[order]
    is_first = np.empty(len(order), dtype=bool)
    is_first[0] = True
    is_first[1:] = (ordered[1:] != ordered[:-1]).any(axis=1)
    cell_of_sorted = np.cumsum(is_first) - 1
    representative = order[is_first]  # per cell, the vertex that stands for it
    by_first_appearance = np.argsort(representative, kind="stable")
    new_index_of_cell = np.empty(len(representative), dtype=np.int64)
    new_index_of_cell[by_first_appearance] = np.arange(len(representative))
    merged_into = np.empty(len(vertices), dtype=np.int64)
    merged_into[order] = new_index_of_cell[cell_of_sorted]

    triangles = merged_into[triangles]
    triangles = triangles[~_repeated_index(triangles)]

    used = np.zeros(len(representative), dtype=bool)
    used[triangles.ravel()] = True
    compact = np.cumsum(used) - 1
    kept = vertices[representative[by_first_appearance]][used]
    return kept, compact[triangles]


def _repeated_index(triangles: np.ndarray) -> np.ndarray:
    a, b, c = triangles.T
    return (a == b) | (b == c) | (c == a)


def _has_repeated_index(triangles: np.ndarray) -> bool:
    return bool(_repeated_index(triangles).any())


# --------------------------------------------------------------------------- topology


class _EdgePairs:
    """The three directed edges of every triangle, grouped by the undirected edge they lie on."""

    def __init__(self, triangles: np.ndarray) -> None:
        self.start = triangles.ravel()  # half-edge h = 3 * triangle + corner runs from vertex start[h] to end[h]
        self.end = np.roll(triangles, -1, axis=1).ravel()
        nodes = int(triangles.max()) + 1
        key = np.minimum(self.start, self.end) * nodes + np.maximum(self.start, self.end)
        self.order = np.argsort(key, kind="stable")
        key = key[self.order]
        first = np.empty(len(key), dtype=bool)
        first[0] = True
        first[1:] = key[1:] != key[:-1]
        starts = np.flatnonzero(first)
        sizes = np.diff(np.append(starts, len(key)))
        self.shared_by_more_than_two = bool((sizes > 2).any())
        self.pair_first = self.order[starts[sizes == 2]]  # of each shared edge, its two half-edges
        self.pair_second = self.order[starts[sizes == 2] + 1]
        self.has_boundary = bool((sizes == 1).any())

    @property
    def closed_oriented(self) -> bool:
        if self.shared_by_more_than_two or self.has_boundary:
            return False
        return bool((self.start[self.pair_first] == self.end[self.pair_second]).all())


def _closed_oriented(triangles: np.ndarray) -> bool:
    if len(triangles) == 0 or _has_repeated_index(triangles):
        return False
    return _EdgePairs(triangles).closed_oriented


def _vertex_manifold(triangles: np.ndarray, edges: _EdgePairs) -> bool:
    """One fan per vertex: the corners of the triangles at a vertex, linked across shared edges, form one component.

    A corner is identified with the half-edge that starts at it.  Across a shared edge the two triangles each have a
    corner at both ends of the edge, and the corners that sit on the same vertex are linked.
    """
    a, b = edges.pair_first, edges.pair_second
    a_end = 3 * (a // 3) + (a % 3 + 1) % 3
    b_end = 3 * (b // 3) + (b % 3 + 1) % 3
    same_direction = edges.start[a] == edges.start[b]
    partner_of_a_start = np.where(same_direction, b, b_end)
    partner_of_a_end = np.where(same_direction, b_end, b)
    fans = _components(triangles.size, np.concatenate([a, a_end]), np.concatenate([partner_of_a_start, partner_of_a_end]))
    return fans == np.count_nonzero(np.bincount(edges.start))


def _components(nodes: int, u: np.ndarray, w: np.ndarray) -> int:
    """Connected components of ``nodes`` points joined by the links ``u[i] - w[i]``: hook the larger root onto the smaller, then flatten."""
    label = np.arange(nodes)
    while True:
        label_u, label_w = label[u], label[w]
        open_link = label_u != label_w
        if not open_link.any():
            return int(np.count_nonzero(label == np.arange(nodes)))
        u, w = u[open_link], w[open_link]
        high = np.maximum(label_u, label_w)[open_link]
        low = np.minimum(label_u, label_w)[open_link]
        by_high = np.argsort(high * nodes + low)
        high, low = high[by_high], low[by_high]
        first = np.empty(len(high), dtype=bool)
        first[0] = True
        first[1:] = high[1:] != high[:-1]
        label[high[first]] = low[first]  # the smallest root each root is linked to
        while True:
            flatter = label[label]
            if (flatter == label).all():
                break
            label = flatter
