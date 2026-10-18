from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, eq=False)
class Solid:
    """A B-rep in millimetres.  ``_handle`` is the OCP ``TopoDS_Shape``; it never leaves this package."""

    _handle: object = field(repr=False)
    solids: int
    faces: int
    roots_in_file: int = 1  # root entities the STEP file offers for transfer
    roots_transferred: int = 1  # roots that produced at least one face


@dataclass(frozen=True, eq=False)
class Mesh:
    """Per-face triangles; the nodes of neighbouring faces are not shared (not welded)."""

    vertices: np.ndarray  # (n, 3) float64, millimetres
    triangles: np.ndarray  # (m, 3) int32, counter-clockwise seen from outside
    deflection_mm: float  # chord error bound the tessellation was asked for


@dataclass(frozen=True)
class MeshTopology:
    """What :func:`eng_toolkit.brep.mesh_topology` found; see there for the exact definitions."""

    edge_manifold_closed: bool  # every edge is shared by two triangles, one in each direction
    vertex_manifold: bool  # the triangles around every vertex form one fan

    @property
    def watertight(self) -> bool:
        return self.edge_manifold_closed and self.vertex_manifold
